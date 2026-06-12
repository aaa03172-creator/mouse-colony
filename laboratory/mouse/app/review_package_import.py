from __future__ import annotations

from typing import Any


ALLOWED_SCHEMA_VERSION = "mouse-colony-review-package.v1"


class ReviewPackageError(ValueError):
    pass


def _text(value: Any) -> str:
    return "" if value is None else " ".join(str(value).split())


def _rule_terms(payload: dict[str, Any], key: str) -> set[str]:
    rules = payload.get("import_rules") or {}
    values = rules.get(key) or []
    return {_text(value).lower() for value in values if _text(value)}


def _check_forbidden(payload: dict[str, Any]) -> None:
    checked_payload = {key: value for key, value in payload.items() if key != "import_rules"}
    payload_text = str(checked_payload).lower()
    for term in _rule_terms(payload, "forbidden_text"):
        if term in payload_text:
            raise ReviewPackageError(f"forbidden text present: {term}")
    for term in _rule_terms(payload, "excluded_terms"):
        if term in payload_text:
            raise ReviewPackageError(f"excluded strain present: {term}")


def parse_review_package(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != ALLOWED_SCHEMA_VERSION:
        raise ReviewPackageError("unsupported schema_version")
    if payload.get("canonical") is not False:
        raise ReviewPackageError("canonical must be false")
    _check_forbidden(payload)

    cards = []
    for card in payload.get("cards", []):
        card_id = _text(card.get("card_id"))
        if not card_id:
            raise ReviewPackageError("card_id is required")
        strain_key = _text(card.get("sheet_name") or card.get("strain"))
        cards.append(
            {
                "card_id": card_id,
                "source_layer": "parsed or intermediate result",
                "review_status": _text(card.get("review_status") or "candidate"),
                "raw": {
                    "cage_no": _text(card.get("cage_no")),
                    "strain": _text(card.get("strain")),
                    "sheet_name": _text(card.get("sheet_name")),
                    "sex_total": _text(card.get("sex_total_raw")),
                    "id": _text(card.get("id_raw")),
                    "genotype": _text(card.get("genotype_raw")),
                    "dob": _text(card.get("dob_raw")),
                    "mating_date": _text(card.get("mating_date_raw")),
                    "pubs_or_note": _text(card.get("pubs_or_note_raw")),
                },
                "normalized": {
                    "strain_key": strain_key,
                },
                "source": {
                    "photo": _text(card.get("source_photo")),
                    "note": _text(card.get("source_note")),
                },
            }
        )

    events = []
    for event in payload.get("events", []):
        events.append(
            {
                "card_id": _text(event.get("card_id")),
                "event_type": _text(event.get("event_type")),
                "raw": {
                    "event_date": _text(event.get("event_date_raw")),
                    "event_value": _text(event.get("event_value_raw")),
                },
                "source": {
                    "photo": _text(event.get("source_photo")),
                    "note": _text(event.get("source_note")),
                },
            }
        )

    return {
        "schema_version": ALLOWED_SCHEMA_VERSION,
        "canonical": False,
        "owner": _text(payload.get("owner")),
        "cards": cards,
        "events": events,
    }
