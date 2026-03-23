from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from knowledge.db.base import Base
from knowledge.db.schema import ensure_runtime_schema
from knowledge.db.session import engine, session_scope
from knowledge.models import (
    EvidenceUnit,
    KBRelease,
    KBReleaseItem,
    KnowledgeBase,
    KnowledgeItem,
    KnowledgeItemCandidate,
    KnowledgeItemEvidenceLink,
    KnowledgeItemRevision,
    RetrievalLog,
    ServiceGrant,
    ServicePrincipal,
    Source,
    SourceAsset,
    WalletUser,
)
from knowledge.schemas.future_domain import (
    EvidenceUnitRead,
    KBReleaseItemRead,
    KBReleaseRead,
    KnowledgeItemCandidateRead,
    KnowledgeItemRead,
    KnowledgeItemEvidenceLinkRead,
    KnowledgeItemRevisionRead,
    RetrievalLogRead,
    ServiceGrantRead,
    ServicePrincipalRead,
    SourceAssetRead,
    SourceRead,
)
from knowledge.utils.time import utc_now


FUTURE_TABLES = (
    "sources",
    "source_assets",
    "evidence_units",
    "knowledge_item_candidates",
    "knowledge_items",
    "knowledge_item_revisions",
    "knowledge_item_evidence_links",
    "kb_releases",
    "kb_release_items",
    "service_principals",
    "service_grants",
    "retrieval_logs",
)


def _ensure_schema_ready() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema(engine)


def _seed_user_and_kb(db, suffix: str = "") -> tuple[str, KnowledgeBase]:
    token = suffix or uuid4().hex
    wallet_address = f"wallet-{token}"
    user = WalletUser(wallet_address=wallet_address)
    kb = KnowledgeBase(owner_wallet_address=wallet_address, name=f"KB {token[:8]}", description="future-models")
    db.add_all([user, kb])
    db.flush()
    return wallet_address, kb


def _seed_source_graph(db, suffix: str = "") -> tuple[str, KnowledgeBase, Source, SourceAsset, EvidenceUnit]:
    token = suffix or uuid4().hex
    wallet_address, kb = _seed_user_and_kb(db, token)
    source = Source(
        kb_id=kb.id,
        source_path=f"/apps/knowledge.yeying.pub/library/{token}",
        scope_type="directory",
        sync_status="pending_sync",
    )
    db.add(source)
    db.flush()
    asset = SourceAsset(
        kb_id=kb.id,
        source_id=source.id,
        asset_path=f"{source.source_path}/asset.txt",
        asset_name="asset.txt",
        asset_type="text",
        source_version="v1",
        availability_status="available",
    )
    db.add(asset)
    db.flush()
    evidence = EvidenceUnit(
        kb_id=kb.id,
        asset_id=asset.id,
        evidence_type="paragraph",
        text="Future evidence unit baseline",
        metadata_json={"parser": "text"},
        source_locator={"line_start": 1, "line_end": 2},
        vector_status="indexed",
    )
    db.add(evidence)
    db.flush()
    return wallet_address, kb, source, asset, evidence


def test_future_domain_tables_exist_and_read_schemas_validate():
    _ensure_schema_ready()
    inspector = inspect(engine)
    for table_name in FUTURE_TABLES:
        assert inspector.has_table(table_name), f"missing table: {table_name}"

    with session_scope() as db:
        wallet_address, kb, source, asset, evidence = _seed_source_graph(db)
        candidate = KnowledgeItemCandidate(
            kb_id=kb.id,
            title="Rule candidate",
            statement="Always verify the published release.",
            item_type="rule",
            structured_payload_json={"rule": "verify_release"},
            item_contract_version="v1",
            origin_type="extracted",
            origin_confidence=0.98,
            review_status="pending_review",
            provenance_json={"evidence_ids": [evidence.id]},
        )
        db.add(candidate)
        db.flush()

        item = KnowledgeItem(kb_id=kb.id, item_type="rule", origin_type="manual", lifecycle_status="confirmed")
        db.add(item)
        db.flush()

        revision = KnowledgeItemRevision(
            knowledge_item_id=item.id,
            revision_no=1,
            title="Published release rule",
            statement="Published release is the only release truth.",
            structured_payload_json={"rule": "published_release_only_truth"},
            item_contract_version="v1",
            review_status="accepted",
            visibility_status="active",
            created_by=wallet_address,
            reviewed_by=wallet_address,
            provenance_type="manual",
            provenance_json={"source": "seed"},
            source_note="seeded in test",
            applicability_scope_json={"kb_id": kb.id},
        )
        db.add(revision)
        db.flush()
        item.current_revision_id = revision.id

        evidence_link = KnowledgeItemEvidenceLink(
            knowledge_item_revision_id=revision.id,
            evidence_unit_id=evidence.id,
            role="supporting",
            rank=1,
            summary="Evidence for release truth",
        )
        db.add(evidence_link)
        db.flush()

        release = KBRelease(
            kb_id=kb.id,
            version=f"release-{uuid4().hex[:8]}",
            status="published",
            release_note="seed release",
            published_at=utc_now(),
            created_by=wallet_address,
        )
        db.add(release)
        db.flush()

        release_item = KBReleaseItem(
            release_id=release.id,
            knowledge_item_id=item.id,
            knowledge_item_revision_id=revision.id,
            item_version_hash="hash-1",
            content_health_status="healthy",
        )
        db.add(release_item)
        db.flush()

        principal = ServicePrincipal(
            owner_wallet_address=wallet_address,
            service_id=f"svc-{uuid4().hex[:8]}",
            display_name="Search Service",
            identity_type="api_key",
            credential_fingerprint="fingerprint-1",
            public_key_jwk={},
            principal_status="active",
        )
        db.add(principal)
        db.flush()

        grant = ServiceGrant(
            owner_wallet_address=wallet_address,
            kb_id=kb.id,
            service_principal_id=principal.id,
            grant_status="active",
            release_selection_mode="latest_published",
            default_result_mode="compact",
            expires_at=utc_now() + timedelta(days=7),
        )
        db.add(grant)
        db.flush()

        retrieval_log = RetrievalLog(
            owner_wallet_address=wallet_address,
            kb_id=kb.id,
            service_grant_id=grant.id,
            service_principal_id=principal.id,
            query="release truth",
            query_mode="formal_first",
            release_id=release.id,
            result_summary_json={"hits": 1},
            trace_json={"trace_id": "future-model-trace"},
        )
        db.add(retrieval_log)
        db.flush()

        assert SourceRead.model_validate(source).source_path == source.source_path
        assert SourceAssetRead.model_validate(asset).asset_path == asset.asset_path
        assert EvidenceUnitRead.model_validate(evidence).source_locator["line_start"] == 1
        assert KnowledgeItemCandidateRead.model_validate(candidate).review_status == "pending_review"
        assert KnowledgeItemRead.model_validate(item).current_revision_id == revision.id
        assert KnowledgeItemRevisionRead.model_validate(revision).revision_no == 1
        assert KnowledgeItemEvidenceLinkRead.model_validate(evidence_link).role == "supporting"
        assert KBReleaseRead.model_validate(release).status == "published"
        assert KBReleaseItemRead.model_validate(release_item).knowledge_item_revision_id == revision.id
        assert ServicePrincipalRead.model_validate(principal).identity_type == "api_key"
        assert ServiceGrantRead.model_validate(grant).release_selection_mode == "latest_published"
        assert RetrievalLogRead.model_validate(retrieval_log).trace_json["trace_id"] == "future-model-trace"


def test_future_domain_unique_constraints_are_enforced():
    _ensure_schema_ready()
    with session_scope() as db:
        wallet_address, kb, source, asset, evidence = _seed_source_graph(db)

        item = KnowledgeItem(kb_id=kb.id, item_type="fact", origin_type="manual", lifecycle_status="confirmed")
        db.add(item)
        db.flush()

        revision = KnowledgeItemRevision(
            knowledge_item_id=item.id,
            revision_no=1,
            title="Revision 1",
            statement="First revision",
            structured_payload_json={"value": 1},
            item_contract_version="v1",
            review_status="accepted",
            visibility_status="active",
            created_by=wallet_address,
            reviewed_by=wallet_address,
            provenance_type="manual",
            provenance_json={"source": "test"},
            source_note="initial",
            applicability_scope_json={},
        )
        db.add(revision)
        db.flush()

        release = KBRelease(kb_id=kb.id, version=f"v-{uuid4().hex[:8]}", status="published", published_at=utc_now(), created_by=wallet_address)
        db.add(release)
        db.flush()

        release_item = KBReleaseItem(
            release_id=release.id,
            knowledge_item_id=item.id,
            knowledge_item_revision_id=revision.id,
            item_version_hash="hash-1",
            content_health_status="healthy",
        )
        db.add(release_item)
        db.flush()

        principal = ServicePrincipal(
            owner_wallet_address=wallet_address,
            service_id=f"svc-{uuid4().hex[:8]}",
            display_name="Service A",
            identity_type="api_key",
            credential_fingerprint="fp-a",
            public_key_jwk={},
            principal_status="active",
        )
        db.add(principal)
        db.flush()

        grant = ServiceGrant(
            owner_wallet_address=wallet_address,
            kb_id=kb.id,
            service_principal_id=principal.id,
            grant_status="active",
            release_selection_mode="latest_published",
            default_result_mode="compact",
        )
        db.add(grant)
        db.flush()

        with db.begin_nested():
            db.add(Source(kb_id=kb.id, source_path=source.source_path, source_type="warehouse", scope_type="directory"))
            with pytest.raises(IntegrityError):
                db.flush()

        with db.begin_nested():
            db.add(
                SourceAsset(
                    kb_id=kb.id,
                    source_id=source.id,
                    asset_path=asset.asset_path,
                    asset_name=asset.asset_name,
                    asset_type=asset.asset_type,
                    source_version="v2",
                    availability_status="changed",
                )
            )
            with pytest.raises(IntegrityError):
                db.flush()

        with db.begin_nested():
            db.add(
                KnowledgeItemRevision(
                    knowledge_item_id=item.id,
                    revision_no=1,
                    title="Revision duplicate",
                    statement="duplicate",
                    structured_payload_json={},
                    item_contract_version="v1",
                    review_status="draft",
                    visibility_status="active",
                    created_by=wallet_address,
                    reviewed_by="",
                    provenance_type="manual",
                    provenance_json={},
                    source_note="",
                    applicability_scope_json={},
                )
            )
            with pytest.raises(IntegrityError):
                db.flush()

        with db.begin_nested():
            db.add(
                KBReleaseItem(
                    release_id=release.id,
                    knowledge_item_id=item.id,
                    knowledge_item_revision_id=revision.id,
                    item_version_hash="hash-2",
                    content_health_status="healthy",
                )
            )
            with pytest.raises(IntegrityError):
                db.flush()

        with db.begin_nested():
            db.add(
                ServiceGrant(
                    owner_wallet_address=wallet_address,
                    kb_id=kb.id,
                    service_principal_id=principal.id,
                    grant_status="active",
                    release_selection_mode="latest_published",
                    default_result_mode="compact",
                )
            )
            with pytest.raises(IntegrityError):
                db.flush()

        with db.begin_nested():
            db.add(
                KnowledgeItemEvidenceLink(
                    knowledge_item_revision_id=revision.id,
                    evidence_unit_id=evidence.id,
                    role="supporting",
                    rank=2,
                    summary="duplicate link",
                )
            )
            db.flush()
            db.add(
                KnowledgeItemEvidenceLink(
                    knowledge_item_revision_id=revision.id,
                    evidence_unit_id=evidence.id,
                    role="supporting",
                    rank=3,
                    summary="duplicate link again",
                )
            )
            with pytest.raises(IntegrityError):
                db.flush()


def test_release_truth_model_supports_multiple_releases_for_one_item():
    _ensure_schema_ready()
    with session_scope() as db:
        wallet_address, kb, _source, _asset, _evidence = _seed_source_graph(db)
        item = KnowledgeItem(kb_id=kb.id, item_type="procedure", origin_type="manual", lifecycle_status="confirmed")
        db.add(item)
        db.flush()

        revision_1 = KnowledgeItemRevision(
            knowledge_item_id=item.id,
            revision_no=1,
            title="Revision 1",
            statement="First published wording",
            structured_payload_json={"step": 1},
            item_contract_version="v1",
            review_status="accepted",
            visibility_status="active",
            created_by=wallet_address,
            reviewed_by=wallet_address,
            provenance_type="manual",
            provenance_json={"source": "seed"},
            source_note="r1",
            applicability_scope_json={},
        )
        revision_2 = KnowledgeItemRevision(
            knowledge_item_id=item.id,
            revision_no=2,
            title="Revision 2",
            statement="Updated wording",
            structured_payload_json={"step": 2},
            item_contract_version="v1",
            review_status="accepted",
            visibility_status="hotfix",
            created_by=wallet_address,
            reviewed_by=wallet_address,
            provenance_type="manual_from_extracted",
            provenance_json={"source": "seed"},
            source_note="r2",
            applicability_scope_json={},
        )
        db.add_all([revision_1, revision_2])
        db.flush()
        item.current_revision_id = revision_2.id

        release_1 = KBRelease(kb_id=kb.id, version=f"release-a-{uuid4().hex[:6]}", status="published", published_at=utc_now(), created_by=wallet_address)
        release_2 = KBRelease(
            kb_id=kb.id,
            version=f"release-b-{uuid4().hex[:6]}",
            status="published",
            published_at=utc_now(),
            created_by=wallet_address,
            supersedes_release_id=None,
        )
        db.add_all([release_1, release_2])
        db.flush()
        release_2.supersedes_release_id = release_1.id

        db.add_all(
            [
                KBReleaseItem(
                    release_id=release_1.id,
                    knowledge_item_id=item.id,
                    knowledge_item_revision_id=revision_1.id,
                    item_version_hash="hash-r1",
                    content_health_status="healthy",
                ),
                KBReleaseItem(
                    release_id=release_2.id,
                    knowledge_item_id=item.id,
                    knowledge_item_revision_id=revision_2.id,
                    item_version_hash="hash-r2",
                    content_health_status="healthy",
                ),
            ]
        )
        db.flush()

        assert "published_in_release_id" not in KnowledgeItem.__table__.c.keys()
        assert item.current_revision_id == revision_2.id
        assert len(release_1.release_items) == 1
        assert len(release_2.release_items) == 1
        assert release_1.release_items[0].knowledge_item_revision_id == revision_1.id
        assert release_2.release_items[0].knowledge_item_revision_id == revision_2.id


def test_service_principal_and_grant_truths_are_separate():
    _ensure_schema_ready()
    with session_scope() as db:
        wallet_address, kb, _source, _asset, _evidence = _seed_source_graph(db)
        release = KBRelease(kb_id=kb.id, version=f"pin-{uuid4().hex[:8]}", status="published", published_at=utc_now(), created_by=wallet_address)
        db.add(release)
        db.flush()

        principal = ServicePrincipal(
            owner_wallet_address=wallet_address,
            service_id=f"svc-{uuid4().hex[:10]}",
            display_name="Pinned Search Service",
            identity_type="signed_jwt",
            credential_fingerprint="fingerprint-jwt",
            public_key_jwk={"kty": "oct"},
            principal_status="active",
        )
        db.add(principal)
        db.flush()

        grant = ServiceGrant(
            owner_wallet_address=wallet_address,
            kb_id=kb.id,
            service_principal_id=principal.id,
            grant_status="active",
            release_selection_mode="pinned_release",
            pinned_release_id=release.id,
            default_result_mode="audit",
            expires_at=utc_now() + timedelta(days=30),
        )
        db.add(grant)
        db.flush()

        log = RetrievalLog(
            owner_wallet_address=wallet_address,
            kb_id=kb.id,
            service_grant_id=grant.id,
            service_principal_id=principal.id,
            query="audit query",
            query_mode="audit",
            release_id=release.id,
            result_summary_json={"mode": "audit"},
            trace_json={"grant_id": grant.id, "principal_id": principal.id},
        )
        db.add(log)
        db.flush()

        assert "kb_id" not in ServicePrincipal.__table__.c.keys()
        assert "credential_fingerprint" not in ServiceGrant.__table__.c.keys()
        assert grant.pinned_release_id == release.id
        assert grant.service_principal_id == principal.id
        assert log.service_grant_id == grant.id
        assert log.service_principal_id == principal.id
