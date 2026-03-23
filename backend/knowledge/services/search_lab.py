from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from knowledge.models import EvidenceUnit, KBRelease, KnowledgeBase, RetrievalLog, Source, SourceAsset
from knowledge.schemas.service_search import ServiceSearchResponse
from knowledge.services.release_management import ReleaseManagementService
from knowledge.services.service_search import ServiceSearchService


class SearchLabService:
    def __init__(
        self,
        service_search_service: ServiceSearchService | None = None,
        release_management_service: ReleaseManagementService | None = None,
    ) -> None:
        self.service_search_service = service_search_service or ServiceSearchService()
        self.release_management_service = release_management_service or ReleaseManagementService()

    def compare(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        *,
        query: str,
        top_k: int,
        result_view: str,
        availability_mode: str,
    ) -> tuple[KBRelease | None, ServiceSearchResponse, ServiceSearchResponse, ServiceSearchResponse, RetrievalLog]:
        kb = self._get_kb_or_404(db, wallet_address, kb_id)
        release = self._current_release(db, wallet_address, kb.id)
        used_evidence_ids: set[int] = set()

        formal_hits = (
            self.service_search_service._search_formal(  # noqa: SLF001
                db,
                release=release,
                query=query,
                result_view=result_view,
                availability_mode=availability_mode,
                top_k=top_k,
                include_zero_scores=True,
                used_evidence_ids=used_evidence_ids,
            )
            if release is not None
            else []
        )
        evidence_hits = self.service_search_service._search_evidence(  # noqa: SLF001
            db,
            kb_id=kb.id,
            query=query,
            result_view=result_view,
            availability_mode=availability_mode,
            top_k=top_k,
            exclude_evidence_ids=set(),
            include_zero_scores=True,
        )
        fallback_evidence_hits = self.service_search_service._search_evidence(  # noqa: SLF001
            db,
            kb_id=kb.id,
            query=query,
            result_view=result_view,
            availability_mode=availability_mode,
            top_k=max(0, top_k - len(formal_hits)),
            exclude_evidence_ids=used_evidence_ids,
            include_zero_scores=True,
        )
        formal_only = ServiceSearchResponse(
            kb_id=kb.id,
            mode="formal_only",
            result_view=result_view,
            availability_mode=availability_mode,
            release=release,
            grant=None,
            hits=formal_hits[:top_k],
        )
        evidence_only = ServiceSearchResponse(
            kb_id=kb.id,
            mode="evidence_only",
            result_view=result_view,
            availability_mode=availability_mode,
            release=release,
            grant=None,
            hits=evidence_hits[:top_k],
        )
        formal_first = ServiceSearchResponse(
            kb_id=kb.id,
            mode="formal_first",
            result_view=result_view,
            availability_mode=availability_mode,
            release=release,
            grant=None,
            hits=[*formal_hits[:top_k], *fallback_evidence_hits[: max(0, top_k - len(formal_hits))]],
        )
        log = self.service_search_service.record_retrieval_log(
            db,
            owner_wallet_address=wallet_address,
            kb_id=kb.id,
            service_grant_id=None,
            service_principal_id=None,
            query=query,
            query_mode="search_lab_compare",
            release_id=release.id if release is not None else None,
            response=formal_first,
        )
        log.result_summary_json = {
            "formal_only_count": len(formal_only.hits),
            "evidence_only_count": len(evidence_only.hits),
            "formal_first_count": len(formal_first.hits),
            "formal_only_health": [hit.content_health_status for hit in formal_only.hits],
            "evidence_only_health": [hit.content_health_status for hit in evidence_only.hits],
        }
        log.trace_json = {
            "query": query,
            "top_k": top_k,
            "result_view": result_view,
            "availability_mode": availability_mode,
            "release_id": release.id if release is not None else None,
        }
        db.commit()
        db.refresh(log)
        return release, formal_only, evidence_only, formal_first, log

    def list_retrieval_logs(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        *,
        limit: int = 50,
    ) -> list[RetrievalLog]:
        self._get_kb_or_404(db, wallet_address, kb_id)
        return list(
            db.scalars(
                select(RetrievalLog)
                .where(RetrievalLog.owner_wallet_address == wallet_address)
                .where(RetrievalLog.kb_id == kb_id)
                .order_by(RetrievalLog.created_at.desc(), RetrievalLog.id.desc())
                .limit(max(1, min(limit, 200)))
            ).all()
        )

    def get_retrieval_log_or_404(self, db: Session, wallet_address: str, kb_id: int, log_id: int) -> RetrievalLog:
        self._get_kb_or_404(db, wallet_address, kb_id)
        log = db.get(RetrievalLog, log_id)
        if log is None or log.kb_id != kb_id or log.owner_wallet_address != wallet_address:
            raise LookupError("retrieval log not found")
        return log

    def source_governance(self, db: Session, wallet_address: str, kb_id: int) -> dict:
        self._get_kb_or_404(db, wallet_address, kb_id)
        sources = list(
            db.scalars(
                select(Source)
                .where(Source.kb_id == kb_id)
                .order_by(Source.created_at.asc(), Source.id.asc())
            ).all()
        )
        assets = list(
            db.scalars(
                select(SourceAsset)
                .where(SourceAsset.kb_id == kb_id)
                .order_by(SourceAsset.source_id.asc(), SourceAsset.asset_path.asc(), SourceAsset.id.asc())
            ).all()
        )
        evidence_counts = dict(
            db.execute(
                select(EvidenceUnit.asset_id, func.count(EvidenceUnit.id))
                .where(EvidenceUnit.kb_id == kb_id)
                .group_by(EvidenceUnit.asset_id)
            ).all()
        )
        affected_assets = [
            {
                "asset_id": asset.id,
                "source_id": asset.source_id,
                "asset_path": asset.asset_path,
                "availability_status": asset.availability_status,
                "evidence_count": int(evidence_counts.get(asset.id, 0) or 0),
                "last_ingested_at": asset.last_ingested_at,
            }
            for asset in assets
            if asset.availability_status in {"missing", "missing_unconfirmed", "changed"}
        ]
        missing_source_ids = {asset["source_id"] for asset in affected_assets if asset["availability_status"] == "missing"}
        sources_payload = []
        for source in sources:
            source_assets = [item for item in affected_assets if item["source_id"] == source.id]
            if not source_assets and source.sync_status not in {"source_missing", "failed", "syncing"}:
                continue
            sources_payload.append(
                {
                    "source_id": source.id,
                    "source_path": source.source_path,
                    "sync_status": source.sync_status,
                    "affected_assets": source_assets,
                }
            )
        return {
            "kb_id": kb_id,
            "status_counts": {
                "sources_total": len(sources),
                "source_missing": sum(
                    1 for source in sources if source.sync_status == "source_missing" or source.id in missing_source_ids
                ),
                "sources_failed": sum(1 for source in sources if source.sync_status == "failed"),
                "assets_total": len(assets),
                "assets_missing": sum(1 for asset in assets if asset.availability_status == "missing"),
                "assets_missing_unconfirmed": sum(1 for asset in assets if asset.availability_status == "missing_unconfirmed"),
                "stale": sum(1 for asset in assets if asset.availability_status == "changed"),
                "evidence_total": int(sum(evidence_counts.values())),
                "evidence_missing_impacted": sum(
                    int(evidence_counts.get(asset.id, 0) or 0) for asset in assets if asset.availability_status == "missing"
                ),
                "evidence_missing_unconfirmed_impacted": sum(
                    int(evidence_counts.get(asset.id, 0) or 0)
                    for asset in assets
                    if asset.availability_status == "missing_unconfirmed"
                ),
                "evidence_stale_impacted": sum(
                    int(evidence_counts.get(asset.id, 0) or 0) for asset in assets if asset.availability_status == "changed"
                ),
            },
            "sources": sources_payload,
            "assets": affected_assets,
        }

    @staticmethod
    def _get_kb_or_404(db: Session, wallet_address: str, kb_id: int) -> KnowledgeBase:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None or kb.owner_wallet_address != wallet_address:
            raise LookupError("knowledge base not found")
        return kb

    def _current_release(self, db: Session, wallet_address: str, kb_id: int) -> KBRelease | None:
        try:
            return self.release_management_service.get_current_release_or_404(db, wallet_address, kb_id)
        except LookupError:
            return None
