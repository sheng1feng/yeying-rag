from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.core.settings import get_settings
from knowledge.models import EvidenceUnit, KnowledgeBase, Source, SourceAsset
from knowledge.services.chunking import DocumentChunker
from knowledge.services.filetypes import infer_file_type
from knowledge.services.parser import DocumentParser
from knowledge.services.source_registry import SourceRegistryService
from knowledge.services.warehouse import WarehouseGateway, build_warehouse_gateway
from knowledge.services.warehouse_access import WarehouseAccessService
from knowledge.services.vector_store import build_vector_store
from knowledge.utils.time import utc_now


ELIGIBLE_ASSET_STATUSES = {"discovered", "available", "changed"}


@dataclass
class EvidenceBuildStats:
    processed_asset_count: int = 0
    built_evidence_count: int = 0
    skipped_asset_count: int = 0
    failed_asset_ids: list[int] | None = None

    def __post_init__(self) -> None:
        if self.failed_asset_ids is None:
            self.failed_asset_ids = []


class EvidencePipelineService:
    def __init__(
        self,
        warehouse_gateway: WarehouseGateway | None = None,
        warehouse_access_service: WarehouseAccessService | None = None,
        parser: DocumentParser | None = None,
        chunker: DocumentChunker | None = None,
    ) -> None:
        self.settings = get_settings()
        self.warehouse_gateway = warehouse_gateway or build_warehouse_gateway()
        self.warehouse_access_service = warehouse_access_service or WarehouseAccessService(warehouse_gateway=self.warehouse_gateway)
        self.parser = parser or DocumentParser()
        self.chunker = chunker or DocumentChunker()
        self.vector_store = build_vector_store()
        self.source_registry_service = SourceRegistryService()

    def build_for_asset(self, db: Session, wallet_address: str, kb_id: int, asset_id: int) -> EvidenceBuildStats:
        kb = self._get_kb_or_404(db, wallet_address, kb_id)
        asset = self._get_asset_or_404(db, kb.id, asset_id)
        if asset.availability_status not in ELIGIBLE_ASSET_STATUSES:
            raise ValueError(f"asset {asset.id} is not eligible for evidence build")
        return self._build_assets(db, wallet_address, kb, source_id=asset.source_id, assets=[asset])

    def build_for_source(self, db: Session, wallet_address: str, kb_id: int, source_id: int) -> EvidenceBuildStats:
        kb = self._get_kb_or_404(db, wallet_address, kb_id)
        source = self.source_registry_service.get_source_or_404(db, wallet_address, kb.id, source_id)
        assets = list(
            db.scalars(
                select(SourceAsset)
                .where(SourceAsset.kb_id == kb.id)
                .where(SourceAsset.source_id == source.id)
                .order_by(SourceAsset.asset_path.asc(), SourceAsset.id.asc())
            ).all()
        )
        return self._build_assets(db, wallet_address, kb, source_id=source.id, assets=assets)

    def list_evidence(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        *,
        source_id: int | None = None,
        asset_id: int | None = None,
        evidence_type: str | None = None,
        vector_status: str | None = None,
    ) -> list[EvidenceUnit]:
        kb = self._get_kb_or_404(db, wallet_address, kb_id)
        stmt = (
            select(EvidenceUnit)
            .join(SourceAsset, SourceAsset.id == EvidenceUnit.asset_id)
            .where(EvidenceUnit.kb_id == kb.id)
            .order_by(EvidenceUnit.asset_id.asc(), EvidenceUnit.id.asc())
        )
        if source_id is not None:
            stmt = stmt.where(SourceAsset.source_id == source_id)
        if asset_id is not None:
            stmt = stmt.where(EvidenceUnit.asset_id == asset_id)
        if evidence_type:
            stmt = stmt.where(EvidenceUnit.evidence_type == evidence_type)
        if vector_status:
            stmt = stmt.where(EvidenceUnit.vector_status == vector_status)
        return list(db.scalars(stmt).all())

    def get_evidence_or_404(self, db: Session, wallet_address: str, kb_id: int, evidence_id: int) -> EvidenceUnit:
        kb = self._get_kb_or_404(db, wallet_address, kb_id)
        evidence = db.get(EvidenceUnit, evidence_id)
        if evidence is None or evidence.kb_id != kb.id:
            raise LookupError("evidence not found")
        return evidence

    def _build_assets(
        self,
        db: Session,
        wallet_address: str,
        kb: KnowledgeBase,
        *,
        source_id: int,
        assets: list[SourceAsset],
    ) -> EvidenceBuildStats:
        stats = EvidenceBuildStats()
        for asset in assets:
            if asset.availability_status not in ELIGIBLE_ASSET_STATUSES:
                stats.skipped_asset_count += 1
                continue
            try:
                built_count = self._build_for_single_asset(db, wallet_address, kb, asset)
            except Exception:
                stats.failed_asset_ids.append(asset.id)
                raise
            stats.processed_asset_count += 1
            stats.built_evidence_count += built_count
        db.commit()
        return stats

    def _build_for_single_asset(self, db: Session, wallet_address: str, kb: KnowledgeBase, asset: SourceAsset) -> int:
        resolved = self.warehouse_access_service.resolve_path_read_access(
            db,
            wallet_address,
            kb.id,
            asset.asset_path,
        )
        try:
            raw_content = self.warehouse_gateway.read_file(wallet_address, asset.asset_path, auth=resolved.auth)
            self.warehouse_access_service.mark_access_success(resolved)
        except Exception as exc:
            if self.warehouse_access_service.is_auth_error(exc):
                self.warehouse_access_service.mark_access_invalid(resolved)
            raise
        parsed_text = self.parser.parse(asset.asset_name, raw_content)
        if not parsed_text.strip():
            self._delete_existing_evidence(db, asset)
            asset.last_ingested_at = utc_now()
            asset.availability_status = "available"
            return 0

        chunks = self.chunker.chunk(asset.asset_name, parsed_text, self._build_chunk_config(kb))
        self._delete_existing_evidence(db, asset)

        evidence_units: list[EvidenceUnit] = []
        file_type = infer_file_type(asset.asset_name)
        for chunk_index, chunk in enumerate(chunks):
            evidence = EvidenceUnit(
                kb_id=kb.id,
                asset_id=asset.id,
                evidence_type=self._map_evidence_type(file_type),
                text=chunk.text,
                metadata_json=dict(chunk.metadata or {}),
                source_locator=self._build_source_locator(
                    asset=asset,
                    file_type=file_type,
                    chunk_index=chunk_index,
                    metadata=chunk.metadata or {},
                ),
                vector_status="pending",
            )
            db.add(evidence)
            evidence_units.append(evidence)
        db.flush()
        self._index_evidence_units(wallet_address, kb.id, asset, file_type, evidence_units)
        asset.last_ingested_at = utc_now()
        asset.availability_status = "available"
        db.flush()
        return len(evidence_units)

    def _delete_existing_evidence(self, db: Session, asset: SourceAsset) -> None:
        existing = list(
            db.scalars(
                select(EvidenceUnit)
                .where(EvidenceUnit.asset_id == asset.id)
                .order_by(EvidenceUnit.id.asc())
            ).all()
        )
        vector_ids = [self._vector_id_for_evidence(evidence.id) for evidence in existing]
        self.vector_store.delete_vectors(vector_ids)
        for evidence in existing:
            db.delete(evidence)
        db.flush()

    def _index_evidence_units(
        self,
        wallet_address: str,
        kb_id: int,
        asset: SourceAsset,
        file_type: str,
        evidence_units: list[EvidenceUnit],
    ) -> None:
        if not evidence_units:
            return
        payloads = []
        for index, evidence in enumerate(evidence_units):
            payloads.append(
                {
                    "vector_id": self._vector_id_for_evidence(evidence.id),
                    "text": evidence.text,
                    "metadata": {
                        "wallet_address": wallet_address,
                        "kb_id": kb_id,
                        "document_id": asset.id,
                        "chunk_id": evidence.id,
                        "source_path": asset.asset_path,
                        "source_kind": "app",
                        "file_name": asset.asset_name,
                        "file_type": file_type,
                        "chunk_index": index,
                        "source_version": asset.source_version,
                        "chunk_strategy": str((evidence.metadata_json or {}).get("chunk_strategy") or ""),
                    },
                }
            )
        try:
            self.vector_store.index_chunks(payloads)
        except Exception:
            for evidence in evidence_units:
                evidence.vector_status = "failed"
            raise
        for evidence in evidence_units:
            evidence.vector_status = "indexed"

    def _build_chunk_config(self, kb: KnowledgeBase) -> dict:
        retrieval_config = kb.retrieval_config or {}
        return {
            "chunk_size": int(retrieval_config.get("chunk_size", self.settings.chunk_size)),
            "chunk_overlap": int(retrieval_config.get("chunk_overlap", self.settings.chunk_overlap)),
            "retrieval_top_k": int(retrieval_config.get("retrieval_top_k", self.settings.retrieval_top_k)),
            "memory_top_k": int(retrieval_config.get("memory_top_k", self.settings.memory_top_k)),
            "embedding_model": retrieval_config.get("embedding_model", self.settings.embedding_model),
        }

    @staticmethod
    def _map_evidence_type(file_type: str) -> str:
        return {
            "markdown": "markdown_section",
            "pdf": "pdf_passage",
            "json": "json_record",
            "csv": "csv_row_group",
            "yaml": "yaml_block",
        }.get(file_type, "text_passage")

    @staticmethod
    def _vector_id_for_evidence(evidence_id: int) -> str:
        return f"evidence-{evidence_id}"

    @staticmethod
    def _build_source_locator(
        *,
        asset: SourceAsset,
        file_type: str,
        chunk_index: int,
        metadata: dict,
    ) -> dict:
        locator = {
            "asset_path": asset.asset_path,
            "source_version": asset.source_version,
            "chunk_index": chunk_index,
            "file_type": file_type,
            "chunk_strategy": metadata.get("chunk_strategy"),
        }
        if metadata.get("section"):
            locator["section_path"] = metadata["section"]
        return locator

    def _get_kb_or_404(self, db: Session, wallet_address: str, kb_id: int) -> KnowledgeBase:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None or kb.owner_wallet_address != wallet_address:
            raise LookupError("knowledge base not found")
        return kb

    def _get_asset_or_404(self, db: Session, kb_id: int, asset_id: int) -> SourceAsset:
        asset = db.get(SourceAsset, asset_id)
        if asset is None or asset.kb_id != kb_id:
            raise LookupError("asset not found")
        return asset
