from __future__ import annotations

import pytest

from knowledge.services.item_contracts import ITEM_CONTRACT_VERSION, ItemContractRegistry, ItemContractValidationError
from knowledge.services.knowledge_items import KnowledgeItemValidationService


@pytest.mark.parametrize(
    ("item_type", "payload"),
    [
        ("fact", {"fact": "A fact value"}),
        ("rule", {"rule": "A rule value"}),
        ("procedure", {"steps": ["step 1", "step 2"]}),
        ("faq", {"question": "Q?", "answer": "A!"}),
        ("reference", {"reference_text": "A reference block"}),
    ],
)
def test_item_contract_registry_accepts_valid_payloads(item_type: str, payload: dict):
    registry = ItemContractRegistry()

    result = registry.validate_item_payload(item_type, ITEM_CONTRACT_VERSION, payload)

    assert result.item_type == item_type
    assert result.item_contract_version == ITEM_CONTRACT_VERSION
    assert result.payload == payload


def test_item_contract_registry_lists_all_v1_contracts():
    registry = ItemContractRegistry()

    contracts = registry.list_item_contracts()

    assert [item.item_type for item in contracts] == ["fact", "faq", "procedure", "reference", "rule"]
    assert all(item.item_contract_version == ITEM_CONTRACT_VERSION for item in contracts)


@pytest.mark.parametrize(
    ("item_type", "payload", "error_code", "field"),
    [
        ("fact", {}, "missing_required_field", "fact"),
        ("rule", {"rule": ""}, "invalid_field_value", "rule"),
        ("procedure", {"steps": []}, "invalid_field_value", "steps"),
        ("procedure", {"steps": "one"}, "invalid_field_type", "steps"),
        ("faq", {"question": "Q?"}, "missing_required_field", "answer"),
        ("reference", {"reference_text": 123}, "invalid_field_type", "reference_text"),
    ],
)
def test_item_contract_registry_rejects_invalid_payloads(item_type: str, payload: dict, error_code: str, field: str):
    registry = ItemContractRegistry()

    with pytest.raises(ItemContractValidationError) as exc_info:
        registry.validate_item_payload(item_type, ITEM_CONTRACT_VERSION, payload)

    errors = exc_info.value.errors
    assert errors
    assert errors[0].error_code == error_code
    assert errors[0].field == field
    assert errors[0].item_type == item_type
    assert errors[0].item_contract_version == ITEM_CONTRACT_VERSION


def test_item_contract_registry_rejects_unknown_type_and_version():
    registry = ItemContractRegistry()

    with pytest.raises(ItemContractValidationError) as type_error:
        registry.validate_item_payload("unknown", ITEM_CONTRACT_VERSION, {"value": "x"})
    assert type_error.value.errors[0].error_code == "unknown_item_type"

    with pytest.raises(ItemContractValidationError) as version_error:
        registry.validate_item_payload("fact", "v2", {"fact": "x"})
    assert version_error.value.errors[0].error_code == "unknown_item_contract_version"


def test_knowledge_item_validation_service_reuses_registry_for_candidate_and_revision():
    service = KnowledgeItemValidationService()
    candidate = service.validate_candidate_payload(
        item_type="faq",
        item_contract_version=ITEM_CONTRACT_VERSION,
        structured_payload_json={"question": "What?", "answer": "This."},
    )
    revision = service.validate_revision_payload(
        item_type="procedure",
        item_contract_version=ITEM_CONTRACT_VERSION,
        structured_payload_json={"steps": ["first", "second"]},
    )

    assert candidate.item_type == "faq"
    assert candidate.payload["answer"] == "This."
    assert revision.item_type == "procedure"
    assert revision.payload["steps"] == ["first", "second"]
