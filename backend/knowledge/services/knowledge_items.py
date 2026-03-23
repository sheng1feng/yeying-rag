from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from knowledge.models import (
    EvidenceUnit,
    KnowledgeBase,
    KnowledgeItem,
    KnowledgeItemCandidate,
    KnowledgeItemEvidenceLink,
    KnowledgeItemRevision,
)
from knowledge.schemas.item_contracts import ItemContractValidationFailure, ItemContractValidationResult
from knowledge.services.item_contracts import ITEM_CONTRACT_VERSION, ItemContractRegistry, ItemContractValidationError
from knowledge.utils.time import utc_now


@dataclass
class KnowledgeItemAcceptanceResult:
    candidate: KnowledgeItemCandidate
    item: KnowledgeItem
    revision: KnowledgeItemRevision


@dataclass
class KnowledgeItemCreateResult:
    item: KnowledgeItem
    revision: KnowledgeItemRevision


class KnowledgeItemValidationService:
    def __init__(self, item_contract_registry: ItemContractRegistry | None = None) -> None:
        self.item_contract_registry = item_contract_registry or ItemContractRegistry()

    def validate_candidate_payload(
        self,
        *,
        item_type: str,
        item_contract_version: str = ITEM_CONTRACT_VERSION,
        structured_payload_json: dict | None,
    ) -> ItemContractValidationResult:
        return self.item_contract_registry.validate_item_payload(
            item_type=item_type,
            item_contract_version=item_contract_version,
            payload=structured_payload_json,
        )

    def validate_revision_payload(
        self,
        *,
        item_type: str,
        item_contract_version: str = ITEM_CONTRACT_VERSION,
        structured_payload_json: dict | None,
    ) -> ItemContractValidationResult:
        return self.item_contract_registry.validate_item_payload(
            item_type=item_type,
            item_contract_version=item_contract_version,
            payload=structured_payload_json,
        )

    @staticmethod
    def build_failure_payload(exc: ItemContractValidationError) -> ItemContractValidationFailure:
        return ItemContractValidationFailure(
            item_type=exc.item_type,
            item_contract_version=exc.item_contract_version,
            errors=exc.errors,
        )


class KnowledgeItemsService:
    def __init__(self, validation_service: KnowledgeItemValidationService | None = None) -> None:
        self.validation_service = validation_service or KnowledgeItemValidationService()

    def list_candidates(self, db: Session, wallet_address: str, kb_id: int) -> list[KnowledgeItemCandidate]:
        self._get_kb_or_404(db, wallet_address, kb_id)
        return list(
            db.scalars(
                select(KnowledgeItemCandidate)
                .where(KnowledgeItemCandidate.kb_id == kb_id)
                .order_by(KnowledgeItemCandidate.created_at.desc(), KnowledgeItemCandidate.id.desc())
            ).all()
        )

    def get_candidate_or_404(self, db: Session, wallet_address: str, kb_id: int, candidate_id: int) -> KnowledgeItemCandidate:
        self._get_kb_or_404(db, wallet_address, kb_id)
        candidate = db.get(KnowledgeItemCandidate, candidate_id)
        if candidate is None or candidate.kb_id != kb_id:
            raise LookupError("candidate not found")
        return candidate

    def accept_candidate(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        candidate_id: int,
        *,
        title: str | None = None,
        statement: str | None = None,
        item_type: str | None = None,
        structured_payload_json: dict | None = None,
        item_contract_version: str | None = None,
        evidence_unit_ids: list[int] | None = None,
        source_note: str = "",
        applicability_scope_json: dict | None = None,
        effective_from=None,
        effective_to=None,
    ) -> KnowledgeItemAcceptanceResult:
        candidate = self.get_candidate_or_404(db, wallet_address, kb_id, candidate_id)
        next_title = title if title is not None else candidate.title
        next_statement = statement if statement is not None else candidate.statement
        next_item_type = item_type if item_type is not None else candidate.item_type
        next_payload = structured_payload_json if structured_payload_json is not None else dict(candidate.structured_payload_json or {})
        next_contract_version = item_contract_version if item_contract_version is not None else candidate.item_contract_version
        validated = self.validation_service.validate_revision_payload(
            item_type=next_item_type,
            item_contract_version=next_contract_version,
            structured_payload_json=next_payload,
        )
        edited = any(
            [
                next_title != candidate.title,
                next_statement != candidate.statement,
                next_item_type != candidate.item_type,
                validated.payload != (candidate.structured_payload_json or {}),
                next_contract_version != candidate.item_contract_version,
            ]
        )
        final_origin_type = "manual_from_extracted" if edited else candidate.origin_type
        item = KnowledgeItem(
            kb_id=kb_id,
            item_type=next_item_type,
            origin_type=final_origin_type,
            lifecycle_status="confirmed",
            is_hotfix=False,
        )
        db.add(item)
        db.flush()
        revision = KnowledgeItemRevision(
            knowledge_item_id=item.id,
            revision_no=1,
            title=next_title,
            statement=next_statement,
            structured_payload_json=validated.payload,
            item_contract_version=validated.item_contract_version,
            review_status="accepted",
            visibility_status="active",
            created_by=wallet_address,
            reviewed_by=wallet_address,
            provenance_type=final_origin_type,
            provenance_json={
                **(candidate.provenance_json or {}),
                "candidate_id": candidate.id,
                "accepted_from_candidate": True,
            },
            source_note=source_note,
            applicability_scope_json=applicability_scope_json or {},
            effective_from=effective_from,
            effective_to=effective_to,
            is_workspace_head=True,
        )
        db.add(revision)
        db.flush()
        item.current_revision_id = revision.id
        candidate.review_status = "accepted"
        self._create_evidence_links(
            db,
            kb_id=kb_id,
            revision_id=revision.id,
            evidence_unit_ids=self._resolve_candidate_evidence_ids(candidate, evidence_unit_ids),
            statement=next_statement,
        )
        db.commit()
        db.refresh(candidate)
        db.refresh(item)
        db.refresh(revision)
        return KnowledgeItemAcceptanceResult(candidate=candidate, item=item, revision=revision)

    def reject_candidate(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        candidate_id: int,
        *,
        source_note: str = "",
    ) -> KnowledgeItemCandidate:
        candidate = self.get_candidate_or_404(db, wallet_address, kb_id, candidate_id)
        candidate.review_status = "rejected"
        provenance = dict(candidate.provenance_json or {})
        if source_note:
            provenance["rejection_note"] = source_note
        candidate.provenance_json = provenance
        db.commit()
        db.refresh(candidate)
        return candidate

    def create_manual_item(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        *,
        title: str,
        statement: str,
        item_type: str,
        structured_payload_json: dict,
        item_contract_version: str,
        evidence_unit_ids: list[int],
        source_note: str,
        applicability_scope_json: dict,
        effective_from=None,
        effective_to=None,
    ) -> KnowledgeItemCreateResult:
        self._get_kb_or_404(db, wallet_address, kb_id)
        validated = self.validation_service.validate_revision_payload(
            item_type=item_type,
            item_contract_version=item_contract_version,
            structured_payload_json=structured_payload_json,
        )
        item = KnowledgeItem(
            kb_id=kb_id,
            item_type=item_type,
            origin_type="manual",
            lifecycle_status="confirmed",
            is_hotfix=False,
        )
        db.add(item)
        db.flush()
        revision = KnowledgeItemRevision(
            knowledge_item_id=item.id,
            revision_no=1,
            title=title,
            statement=statement,
            structured_payload_json=validated.payload,
            item_contract_version=validated.item_contract_version,
            review_status="accepted",
            visibility_status="active",
            created_by=wallet_address,
            reviewed_by=wallet_address,
            provenance_type="manual",
            provenance_json={"created_via": "manual", "evidence_unit_ids": evidence_unit_ids},
            source_note=source_note,
            applicability_scope_json=applicability_scope_json,
            effective_from=effective_from,
            effective_to=effective_to,
            is_workspace_head=True,
        )
        db.add(revision)
        db.flush()
        item.current_revision_id = revision.id
        self._create_evidence_links(db, kb_id=kb_id, revision_id=revision.id, evidence_unit_ids=evidence_unit_ids, statement=statement)
        db.commit()
        db.refresh(item)
        db.refresh(revision)
        return KnowledgeItemCreateResult(item=item, revision=revision)

    def update_manual_item(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        item_id: int,
        *,
        title: str | None = None,
        statement: str | None = None,
        item_type: str | None = None,
        structured_payload_json: dict | None = None,
        item_contract_version: str | None = None,
        evidence_unit_ids: list[int] | None = None,
        source_note: str | None = None,
        applicability_scope_json: dict | None = None,
        effective_from=None,
        effective_to=None,
    ) -> KnowledgeItemCreateResult:
        item = self.get_item_or_404(db, wallet_address, kb_id, item_id)
        current_revision = self.get_current_revision_or_404(db, item)
        next_item_type = item_type if item_type is not None else item.item_type
        next_payload = structured_payload_json if structured_payload_json is not None else dict(current_revision.structured_payload_json or {})
        next_contract_version = item_contract_version if item_contract_version is not None else current_revision.item_contract_version
        validated = self.validation_service.validate_revision_payload(
            item_type=next_item_type,
            item_contract_version=next_contract_version,
            structured_payload_json=next_payload,
        )
        current_revision.is_workspace_head = False
        revision = KnowledgeItemRevision(
            knowledge_item_id=item.id,
            revision_no=self._next_revision_no(db, item.id),
            title=title if title is not None else current_revision.title,
            statement=statement if statement is not None else current_revision.statement,
            structured_payload_json=validated.payload,
            item_contract_version=validated.item_contract_version,
            review_status="accepted",
            visibility_status="active",
            created_by=wallet_address,
            reviewed_by=wallet_address,
            provenance_type="manual",
            provenance_json={"updated_via": "manual", "previous_revision_id": current_revision.id},
            source_note=source_note if source_note is not None else current_revision.source_note,
            applicability_scope_json=applicability_scope_json if applicability_scope_json is not None else dict(current_revision.applicability_scope_json or {}),
            effective_from=effective_from if effective_from is not None else current_revision.effective_from,
            effective_to=effective_to if effective_to is not None else current_revision.effective_to,
            is_workspace_head=True,
        )
        db.add(revision)
        db.flush()
        item.item_type = next_item_type
        item.current_revision_id = revision.id
        item.updated_at = utc_now()
        resolved_evidence_ids = evidence_unit_ids if evidence_unit_ids is not None else [
            link.evidence_unit_id for link in self._list_revision_evidence_links(db, current_revision.id)
        ]
        self._create_evidence_links(db, kb_id=kb_id, revision_id=revision.id, evidence_unit_ids=resolved_evidence_ids, statement=revision.statement)
        db.commit()
        db.refresh(item)
        db.refresh(revision)
        return KnowledgeItemCreateResult(item=item, revision=revision)

    def list_items(self, db: Session, wallet_address: str, kb_id: int) -> list[KnowledgeItem]:
        self._get_kb_or_404(db, wallet_address, kb_id)
        return list(
            db.scalars(
                select(KnowledgeItem)
                .where(KnowledgeItem.kb_id == kb_id)
                .order_by(KnowledgeItem.created_at.desc(), KnowledgeItem.id.desc())
            ).all()
        )

    def get_item_or_404(self, db: Session, wallet_address: str, kb_id: int, item_id: int) -> KnowledgeItem:
        self._get_kb_or_404(db, wallet_address, kb_id)
        item = db.get(KnowledgeItem, item_id)
        if item is None or item.kb_id != kb_id:
            raise LookupError("knowledge item not found")
        return item

    def get_item_detail(self, db: Session, wallet_address: str, kb_id: int, item_id: int) -> tuple[KnowledgeItem, KnowledgeItemRevision | None, list[tuple[KnowledgeItemRevision, list[KnowledgeItemEvidenceLink]]]]:
        item = self.get_item_or_404(db, wallet_address, kb_id, item_id)
        revisions = list(
            db.scalars(
                select(KnowledgeItemRevision)
                .options(selectinload(KnowledgeItemRevision.evidence_links))
                .where(KnowledgeItemRevision.knowledge_item_id == item.id)
                .order_by(KnowledgeItemRevision.revision_no.asc(), KnowledgeItemRevision.id.asc())
            ).all()
        )
        current_revision = next((revision for revision in revisions if revision.id == item.current_revision_id), None)
        detail = []
        for revision in revisions:
            detail.append((revision, list(revision.evidence_links or [])))
        return item, current_revision, detail

    def get_current_revision_or_404(self, db: Session, item: KnowledgeItem) -> KnowledgeItemRevision:
        if item.current_revision_id is None:
            raise LookupError("knowledge item has no current revision")
        revision = db.get(KnowledgeItemRevision, item.current_revision_id)
        if revision is None or revision.knowledge_item_id != item.id:
            raise LookupError("knowledge item current revision not found")
        return revision

    def _next_revision_no(self, db: Session, knowledge_item_id: int) -> int:
        latest = db.scalar(
            select(KnowledgeItemRevision)
            .where(KnowledgeItemRevision.knowledge_item_id == knowledge_item_id)
            .order_by(KnowledgeItemRevision.revision_no.desc(), KnowledgeItemRevision.id.desc())
        )
        return int(latest.revision_no or 0) + 1 if latest is not None else 1

    def _create_evidence_links(
        self,
        db: Session,
        *,
        kb_id: int,
        revision_id: int,
        evidence_unit_ids: list[int],
        statement: str,
    ) -> None:
        for index, evidence_unit_id in enumerate(dict.fromkeys(int(value) for value in (evidence_unit_ids or []) if int(value) > 0)):
            evidence = db.get(EvidenceUnit, evidence_unit_id)
            if evidence is None or evidence.kb_id != kb_id:
                raise ValueError(f"evidence unit {evidence_unit_id} not found in knowledge base")
            db.add(
                KnowledgeItemEvidenceLink(
                    knowledge_item_revision_id=revision_id,
                    evidence_unit_id=evidence.id,
                    role="supporting",
                    rank=index + 1,
                    summary=(statement or evidence.text or "")[:255],
                )
            )
        db.flush()

    @staticmethod
    def _resolve_candidate_evidence_ids(candidate: KnowledgeItemCandidate, override_evidence_unit_ids: list[int] | None) -> list[int]:
        if override_evidence_unit_ids:
            return override_evidence_unit_ids
        provenance = candidate.provenance_json or {}
        return [int(value) for value in (provenance.get("evidence_unit_ids") or []) if str(value).strip()]

    def _list_revision_evidence_links(self, db: Session, revision_id: int) -> list[KnowledgeItemEvidenceLink]:
        return list(
            db.scalars(
                select(KnowledgeItemEvidenceLink)
                .where(KnowledgeItemEvidenceLink.knowledge_item_revision_id == revision_id)
                .order_by(KnowledgeItemEvidenceLink.rank.asc(), KnowledgeItemEvidenceLink.id.asc())
            ).all()
        )

    @staticmethod
    def _get_kb_or_404(db: Session, wallet_address: str, kb_id: int) -> KnowledgeBase:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None or kb.owner_wallet_address != wallet_address:
            raise LookupError("knowledge base not found")
        return kb
