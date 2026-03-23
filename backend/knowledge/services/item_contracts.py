from __future__ import annotations

from dataclasses import dataclass

from knowledge.schemas.item_contracts import ItemContractError, ItemContractInfo, ItemContractValidationResult


ITEM_CONTRACT_VERSION = "v1"
SUPPORTED_ITEM_TYPES = ("fact", "rule", "procedure", "faq", "reference")


@dataclass(frozen=True)
class ItemContractDefinition:
    item_type: str
    item_contract_version: str
    required_fields: tuple[str, ...]


class ItemContractValidationError(Exception):
    def __init__(self, item_type: str, item_contract_version: str, errors: list[ItemContractError]) -> None:
        self.item_type = item_type
        self.item_contract_version = item_contract_version
        self.errors = errors
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        return "; ".join(error.message for error in self.errors)


class ItemContractRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], ItemContractDefinition] = {
            ("fact", ITEM_CONTRACT_VERSION): ItemContractDefinition("fact", ITEM_CONTRACT_VERSION, ("fact",)),
            ("rule", ITEM_CONTRACT_VERSION): ItemContractDefinition("rule", ITEM_CONTRACT_VERSION, ("rule",)),
            ("procedure", ITEM_CONTRACT_VERSION): ItemContractDefinition("procedure", ITEM_CONTRACT_VERSION, ("steps",)),
            ("faq", ITEM_CONTRACT_VERSION): ItemContractDefinition("faq", ITEM_CONTRACT_VERSION, ("question", "answer")),
            ("reference", ITEM_CONTRACT_VERSION): ItemContractDefinition("reference", ITEM_CONTRACT_VERSION, ("reference_text",)),
        }

    def list_item_contracts(self) -> list[ItemContractInfo]:
        return [
            ItemContractInfo(
                item_type=definition.item_type,
                item_contract_version=definition.item_contract_version,
                required_fields=list(definition.required_fields),
            )
            for definition in sorted(self._definitions.values(), key=lambda item: (item.item_type, item.item_contract_version))
        ]

    def get_item_contract(self, item_type: str, item_contract_version: str = ITEM_CONTRACT_VERSION) -> ItemContractInfo:
        definition = self._get_definition(item_type, item_contract_version)
        return ItemContractInfo(
            item_type=definition.item_type,
            item_contract_version=definition.item_contract_version,
            required_fields=list(definition.required_fields),
        )

    def validate_item_payload(
        self,
        item_type: str,
        item_contract_version: str,
        payload: dict | None,
    ) -> ItemContractValidationResult:
        definition = self._get_definition(item_type, item_contract_version)
        normalized_payload = payload if isinstance(payload, dict) else {}
        errors = self._validate_payload(definition, normalized_payload)
        if errors:
            raise ItemContractValidationError(definition.item_type, definition.item_contract_version, errors)
        return ItemContractValidationResult(
            item_type=definition.item_type,
            item_contract_version=definition.item_contract_version,
            payload=normalized_payload,
        )

    def _get_definition(self, item_type: str, item_contract_version: str) -> ItemContractDefinition:
        normalized_type = str(item_type or "").strip()
        normalized_version = str(item_contract_version or "").strip() or ITEM_CONTRACT_VERSION

        if normalized_type not in SUPPORTED_ITEM_TYPES:
            raise ItemContractValidationError(
                normalized_type,
                normalized_version,
                [
                    ItemContractError(
                        item_type=normalized_type,
                        item_contract_version=normalized_version,
                        error_code="unknown_item_type",
                        field="item_type",
                        message=f"unsupported item_type: {normalized_type}",
                    )
                ],
            )
        if normalized_version != ITEM_CONTRACT_VERSION:
            raise ItemContractValidationError(
                normalized_type,
                normalized_version,
                [
                    ItemContractError(
                        item_type=normalized_type,
                        item_contract_version=normalized_version,
                        error_code="unknown_item_contract_version",
                        field="item_contract_version",
                        message=f"unsupported item_contract_version: {normalized_version}",
                    )
                ],
            )
        return self._definitions[(normalized_type, normalized_version)]

    def _validate_payload(self, definition: ItemContractDefinition, payload: dict) -> list[ItemContractError]:
        errors: list[ItemContractError] = []
        for field_name in definition.required_fields:
            if field_name not in payload:
                errors.append(
                    ItemContractError(
                        item_type=definition.item_type,
                        item_contract_version=definition.item_contract_version,
                        error_code="missing_required_field",
                        field=field_name,
                        message=f"{field_name} is required for item_type={definition.item_type}",
                    )
                )
                continue
            value = payload[field_name]
            if definition.item_type == "procedure" and field_name == "steps":
                if not isinstance(value, list):
                    errors.append(
                        ItemContractError(
                            item_type=definition.item_type,
                            item_contract_version=definition.item_contract_version,
                            error_code="invalid_field_type",
                            field=field_name,
                            message="steps must be a list",
                        )
                    )
                    continue
                if not value:
                    errors.append(
                        ItemContractError(
                            item_type=definition.item_type,
                            item_contract_version=definition.item_contract_version,
                            error_code="invalid_field_value",
                            field=field_name,
                            message="steps must not be empty",
                        )
                    )
                    continue
            elif not isinstance(value, str):
                errors.append(
                    ItemContractError(
                        item_type=definition.item_type,
                        item_contract_version=definition.item_contract_version,
                        error_code="invalid_field_type",
                        field=field_name,
                        message=f"{field_name} must be a string",
                    )
                )
                continue
            elif not value.strip():
                errors.append(
                    ItemContractError(
                        item_type=definition.item_type,
                        item_contract_version=definition.item_contract_version,
                        error_code="invalid_field_value",
                        field=field_name,
                        message=f"{field_name} must not be empty",
                    )
                )
        return errors
