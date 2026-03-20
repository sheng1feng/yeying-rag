from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.models import Source, SourceAsset
from knowledge.services.asset_inventory import AssetInventoryService, SourcePathMissingError, SourceScopeMismatchError
from knowledge.services.source_registry import SourceRegistryService
from knowledge.utils.time import utc_now


@dataclass
class SourceScanStats:
    total_assets: int = 0
    discovered_assets: int = 0
    available_assets: int = 0
    changed_assets: int = 0
    missing_assets: int = 0
    ignored_assets: int = 0


class SourceSyncService:
    def __init__(
        self,
        source_registry_service: SourceRegistryService | None = None,
        asset_inventory_service: AssetInventoryService | None = None,
    ) -> None:
        self.source_registry_service = source_registry_service or SourceRegistryService()
        self.asset_inventory_service = asset_inventory_service or AssetInventoryService()

    def scan_source(self, db: Session, wallet_address: str, kb_id: int, source_id: int) -> tuple[Source, SourceScanStats]:
        source = self.source_registry_service.get_source_or_404(db, wallet_address, kb_id, source_id)
        source.sync_status = "syncing"
        db.commit()
        db.refresh(source)

        try:
            snapshots = self.asset_inventory_service.list_asset_snapshots(db, wallet_address, source.source_path, source.scope_type)
        except SourcePathMissingError:
            return self._mark_source_missing(db, source)
        except SourceScopeMismatchError:
            source.sync_status = "failed"
            db.commit()
            raise

        stats = SourceScanStats()
        now = utc_now()
        existing_assets = {
            asset.asset_path: asset
            for asset in db.scalars(
                select(SourceAsset)
                .where(SourceAsset.source_id == source.id)
                .order_by(SourceAsset.id.asc())
            ).all()
        }
        seen_paths: set[str] = set()

        for snapshot in snapshots:
            seen_paths.add(snapshot.asset_path)
            asset = existing_assets.get(snapshot.asset_path)
            if asset is None:
                asset = SourceAsset(
                    kb_id=source.kb_id,
                    source_id=source.id,
                    asset_path=snapshot.asset_path,
                    asset_name=snapshot.asset_name,
                    asset_type=snapshot.asset_type,
                    source_version=snapshot.source_version,
                    availability_status="discovered",
                )
                db.add(asset)
                stats.discovered_assets += 1
                continue

            previous_version = str(asset.source_version or "")
            asset.asset_name = snapshot.asset_name
            asset.asset_type = snapshot.asset_type
            asset.source_version = snapshot.source_version
            if previous_version and previous_version != snapshot.source_version:
                asset.availability_status = "changed"
                stats.changed_assets += 1
            else:
                asset.availability_status = "available"
                stats.available_assets += 1

        for asset_path, asset in existing_assets.items():
            if asset_path in seen_paths:
                continue
            asset.availability_status = "missing"
            stats.missing_assets += 1

        source.last_seen_at = now
        source.last_synced_at = now
        source.sync_status = "synced" if source.enabled else "disabled"
        db.commit()
        db.refresh(source)

        total_assets = len(seen_paths) + stats.missing_assets
        stats.total_assets = total_assets
        return source, stats

    @staticmethod
    def _mark_source_missing(db: Session, source: Source) -> tuple[Source, SourceScanStats]:
        stats = SourceScanStats()
        assets = list(
            db.scalars(
                select(SourceAsset)
                .where(SourceAsset.source_id == source.id)
                .order_by(SourceAsset.id.asc())
            ).all()
        )
        for asset in assets:
            asset.availability_status = "missing"
            stats.missing_assets += 1
        source.sync_status = "source_missing"
        source.last_seen_at = utc_now()
        source.last_synced_at = utc_now()
        db.commit()
        db.refresh(source)
        stats.total_assets = len(assets)
        return source, stats
