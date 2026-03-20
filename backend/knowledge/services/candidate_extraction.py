from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.models import EvidenceUnit, KnowledgeItemCandidate, Source, SourceAsset
from knowledge.services.item_contracts import ITEM_CONTRACT_VERSION
from knowledge.services.knowledge_items import KnowledgeItemValidationService


@dataclass
class CandidateGenerationStats:
    created_count: int = 0
    reused_count: int = 0
    candidates: list[KnowledgeItemCandidate] | None = None

    def __post_init__(self) -> None:
        if self.candidates is None:
            self.candidates = []


class CandidateExtractionService:
    def __init__(self, validation_service: KnowledgeItemValidationService | None = None) -> None:
        self.validation_service = validation_service or KnowledgeItemValidationService()

    def generate_for_asset(self, db: Session, kb_id: int, asset_id: int) -> CandidateGenerationStats:
        asset = db.get(SourceAsset, asset_id)
        if asset is None or asset.kb_id != kb_id:
            raise LookupError("asset not found")
        evidence_units = list(
            db.scalars(
                select(EvidenceUnit)
                .where(EvidenceUnit.kb_id == kb_id)
                .where(EvidenceUnit.asset_id == asset.id)
                .order_by(EvidenceUnit.id.asc())
            ).all()
        )
        return self._generate_from_evidence(db, kb_id, evidence_units)

    def generate_for_source(self, db: Session, kb_id: int, source_id: int) -> CandidateGenerationStats:
        source = db.get(Source, source_id)
        if source is None or source.kb_id != kb_id:
            raise LookupError("source not found")
        evidence_units = list(
            db.scalars(
                select(EvidenceUnit)
                .join(SourceAsset, SourceAsset.id == EvidenceUnit.asset_id)
                .where(EvidenceUnit.kb_id == kb_id)
                .where(SourceAsset.source_id == source.id)
                .order_by(EvidenceUnit.id.asc())
            ).all()
        )
        return self._generate_from_evidence(db, kb_id, evidence_units)

    def _generate_from_evidence(self, db: Session, kb_id: int, evidence_units: list[EvidenceUnit]) -> CandidateGenerationStats:
        stats = CandidateGenerationStats()
        for evidence in evidence_units:
            created_from_job_id = f"extract:evidence:{evidence.id}"
            existing = db.scalar(
                select(KnowledgeItemCandidate)
                .where(KnowledgeItemCandidate.kb_id == kb_id)
                .where(KnowledgeItemCandidate.created_from_job_id == created_from_job_id)
                .where(KnowledgeItemCandidate.review_status == "pending_review")
            )
            if existing is not None:
                stats.reused_count += 1
                stats.candidates.append(existing)
                continue
            item_type, payload = self._infer_item_type_and_payload(evidence)
            validated = self.validation_service.validate_candidate_payload(
                item_type=item_type,
                item_contract_version=ITEM_CONTRACT_VERSION,
                structured_payload_json=payload,
            )
            title, statement = self._build_title_and_statement(item_type, validated.payload, evidence.text)
            candidate = KnowledgeItemCandidate(
                kb_id=kb_id,
                title=title,
                statement=statement,
                item_type=item_type,
                structured_payload_json=validated.payload,
                item_contract_version=validated.item_contract_version,
                origin_type="extracted",
                origin_confidence=0.6,
                review_status="pending_review",
                created_from_job_id=created_from_job_id,
                provenance_json={
                    "evidence_unit_ids": [evidence.id],
                    "asset_id": evidence.asset_id,
                    "source_locator": evidence.source_locator,
                },
            )
            db.add(candidate)
            db.flush()
            stats.created_count += 1
            stats.candidates.append(candidate)
        db.commit()
        for candidate in stats.candidates:
            db.refresh(candidate)
        return stats

    def _infer_item_type_and_payload(self, evidence: EvidenceUnit) -> tuple[str, dict]:
        text = (evidence.text or "").strip()
        locator = evidence.source_locator or {}
        file_type = str(locator.get("file_type") or "").strip().lower()
        lower_text = text.lower()
        if ("q:" in lower_text and "a:" in lower_text) or ("question:" in lower_text and "answer:" in lower_text):
            question, answer = self._extract_faq(text)
            return "faq", {"question": question, "answer": answer}
        if "\n1." in text or lower_text.startswith("1.") or "\n- " in text or "step " in lower_text or "步骤" in text:
            steps = self._extract_steps(text)
            if steps:
                return "procedure", {"steps": steps}
        if file_type in {"json", "yaml", "csv"} or evidence.evidence_type in {"json_record", "yaml_block", "csv_row_group"}:
            return "reference", {"reference_text": text}
        if lower_text.startswith("rule:") or " must " in lower_text or " should " in lower_text or "规则" in text:
            return "rule", {"rule": text}
        return "fact", {"fact": text}

    @staticmethod
    def _build_title_and_statement(item_type: str, payload: dict, fallback_text: str) -> tuple[str, str]:
        if item_type == "faq":
            question = str(payload.get("question") or "").strip()
            answer = str(payload.get("answer") or "").strip()
            return question[:120] or fallback_text[:120], answer or fallback_text
        if item_type == "procedure":
            steps = [str(item).strip() for item in (payload.get("steps") or []) if str(item).strip()]
            title = (steps[0] if steps else fallback_text)[:120]
            return title, "\n".join(steps) if steps else fallback_text
        if item_type == "rule":
            rule = str(payload.get("rule") or fallback_text).strip()
            return rule[:120], rule
        if item_type == "reference":
            reference_text = str(payload.get("reference_text") or fallback_text).strip()
            return reference_text[:120], reference_text
        fact = str(payload.get("fact") or fallback_text).strip()
        return fact[:120], fact

    @staticmethod
    def _extract_faq(text: str) -> tuple[str, str]:
        question = ""
        answer = ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            lower_line = line.lower()
            if lower_line.startswith("q:") or lower_line.startswith("question:"):
                question = line.split(":", 1)[1].strip()
            elif lower_line.startswith("a:") or lower_line.startswith("answer:"):
                answer = line.split(":", 1)[1].strip()
        if not question and lines:
            question = lines[0]
        if not answer and len(lines) > 1:
            answer = lines[1]
        return question or text[:120], answer or text

    @staticmethod
    def _extract_steps(text: str) -> list[str]:
        steps: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("- "):
                steps.append(line[2:].strip())
                continue
            if len(line) > 2 and line[0].isdigit() and line[1] == ".":
                steps.append(line[2:].strip())
                continue
        return [step for step in steps if step]
