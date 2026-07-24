"""Japanese PII recognizer tests (§14, §30.1). Synthetic data only (§30)."""

from __future__ import annotations

import pytest

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.date_of_birth import DateOfBirthDetector
from securitymasker.detectors.japanese_address import CompositeAddressDetector
from securitymasker.detectors.japanese_my_number import (
    JapaneseMyNumberDetector,
    check_digit,
    is_valid_my_number,
)
from securitymasker.detectors.japanese_phone import JapanesePhoneDetector
from securitymasker.detectors.japanese_postal_code import JapanesePostalCodeDetector
from securitymasker.models import EntityType
from securitymasker.normalization import normalize


def ctx(text: str) -> DetectionContext:
    return DetectionContext(norm=normalize(text, "nfkc"))


# Build a synthetic-but-checksum-valid My Number (never a real person's, §14.5).
_BODY11 = "12345678901"
VALID_MYNUMBER = _BODY11 + str(check_digit(_BODY11))


def test_my_number_checksum_helpers() -> None:
    assert is_valid_my_number(VALID_MYNUMBER)
    # Flip the check digit -> invalid.
    bad = _BODY11 + str((check_digit(_BODY11) + 1) % 10)
    assert not is_valid_my_number(bad) or bad == VALID_MYNUMBER


@pytest.mark.asyncio
async def test_my_number_valid_detected_invalid_ignored() -> None:
    det = JapaneseMyNumberDetector()
    hits = await det.detect(ctx(f"個人番号は{VALID_MYNUMBER}です"))
    assert len(hits) == 1
    assert hits[0].entity_type == EntityType.JP_MY_NUMBER.value
    assert hits[0].score >= 0.9  # context word present

    bad = _BODY11 + str((check_digit(_BODY11) + 1) % 10)
    if bad != VALID_MYNUMBER:
        assert await det.detect(ctx(f"個人番号は{bad}です")) == []


@pytest.mark.asyncio
async def test_my_number_fullwidth_and_hyphen_grouping() -> None:
    det = JapaneseMyNumberDetector()
    grouped = f"{VALID_MYNUMBER[:4]}-{VALID_MYNUMBER[4:8]}-{VALID_MYNUMBER[8:]}"
    assert len(await det.detect(ctx(f"マイナンバー {grouped}"))) == 1
    # Full-width digits fold under NFKC.
    fw = "".join(chr(ord(c) + 0xFEE0) for c in VALID_MYNUMBER)
    assert len(await det.detect(ctx(f"個人番号 {fw}"))) == 1


@pytest.mark.asyncio
async def test_phone_formats_and_context() -> None:
    det = JapanesePhoneDetector()
    for number in ("03-1234-5678", "090-1234-5678", "0120-123-456", "+81 90 1234 5678", "03（1234）5678"):
        hits = await det.detect(ctx(f"電話は{number}です"))
        assert hits, number
        assert hits[0].entity_type == EntityType.PHONE.value


@pytest.mark.asyncio
async def test_phone_extension_and_bare_number() -> None:
    det = JapanesePhoneDetector()
    # "内線" is itself a phone context word, so an extension is a valid detection.
    assert await det.detect(ctx("内線1234")) != []
    # A bare 4-digit number with no phone shape/context is NOT a phone.
    assert await det.detect(ctx("チケット番号は1234")) == []


@pytest.mark.asyncio
async def test_postal_code_needs_marker_or_prefecture() -> None:
    det = JapanesePostalCodeDetector()
    assert await det.detect(ctx("チケット番号は123-4567")) == []  # bare -> ignored (§14.6)
    assert await det.detect(ctx("〒150-0001")) != []
    assert await det.detect(ctx("150-0001 東京都渋谷区")) != []


@pytest.mark.asyncio
async def test_date_of_birth_context_promotion() -> None:
    det = DateOfBirthDetector()
    assert await det.detect(ctx("2026年7月24日にリリース")) == []  # plain date
    hits = await det.detect(ctx("1985年4月12日生まれ"))
    assert hits and hits[0].entity_type == EntityType.DATE_OF_BIRTH.value


@pytest.mark.asyncio
async def test_optional_adapters_no_op_when_unavailable() -> None:
    """Presidio/NER cleanly no-op when the ML extras/models are not installed (§13)."""
    from securitymasker.detectors.japanese_ner import JapaneseNerDetector
    from securitymasker.detectors.presidio import PresidioDetector

    ner = JapaneseNerDetector(model=None)  # no model -> disabled, never hardcoded
    assert ner.available is False
    assert await ner.detect(ctx("山田太郎")) == []

    presidio = PresidioDetector()
    # Whether or not presidio is installed, detect must not raise.
    assert await presidio.detect(ctx("山田太郎")) == [] or presidio.available


@pytest.mark.asyncio
async def test_credit_card_luhn_and_block() -> None:
    from securitymasker.detectors.formats import FormatsDetector
    from securitymasker.models import RestorePolicy

    det = FormatsDetector()
    # A Luhn-valid test card number (synthetic).
    hits = await det.detect(ctx("card 4242 4242 4242 4242"))
    cards = [h for h in hits if h.entity_type == EntityType.CREDIT_CARD.value]
    assert cards and cards[0].restore_policy == RestorePolicy.BLOCK.value
    # A non-Luhn number is not a card.
    assert not [h for h in await det.detect(ctx("id 1234 5678 9012 3456"))
                if h.entity_type == EntityType.CREDIT_CARD.value]


@pytest.mark.asyncio
async def test_composite_address_single_span() -> None:
    det = CompositeAddressDetector()
    hits = await det.detect(ctx("住所は東京都渋谷区神宮前1丁目2番3号 秘密ビル401です"))
    assert len(hits) == 1
    assert hits[0].entity_type == EntityType.JP_ADDRESS.value
    # The whole address is one span (no partial-address leakage, §14.2).
    assert "東京都渋谷区神宮前" in hits[0].original_value
    assert "号" in hits[0].original_value
