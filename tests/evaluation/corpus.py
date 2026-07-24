"""Anonymized Japanese evaluation corpus (§31). Synthetic data only (§30).

Mixes prose, chat, logs, config, and source code with positive and negative
examples. Gold labels are ``(entity_type, surface)`` pairs; negatives have none.
Names/orgs are covered by the dictionary the evaluation engine is built with.
"""

from __future__ import annotations

from dataclasses import dataclass

from securitymasker.detectors.japanese_my_number import check_digit
from securitymasker.models import EntityType

# Synthetic My Number with a valid check digit (never a real number, §14.5).
_MYNUM = "12345678901" + str(check_digit("12345678901"))


@dataclass(frozen=True)
class Example:
    text: str
    gold: tuple[tuple[str, str], ...] = ()
    context: str = "prose"


PERSON = EntityType.PERSON.value
ORG = EntityType.ORGANIZATION.value
PHONE = EntityType.PHONE.value
EMAIL = EntityType.EMAIL.value
ADDR = EntityType.JP_ADDRESS.value
POSTAL = EntityType.JP_POSTAL_CODE.value
MYNUM = EntityType.JP_MY_NUMBER.value
DOB = EntityType.DATE_OF_BIRTH.value
APIKEY = EntityType.API_KEY.value

# Dictionary the evaluation engine registers (names/orgs are user-defined, §14).
DICTIONARY: dict[str, tuple[str, ...]] = {
    PERSON: ("山田太郎",),
    ORG: ("株式会社極秘技研", "極秘技研"),
}

POSITIVES: list[Example] = [
    Example("担当者は山田太郎です。", ((PERSON, "山田太郎"),)),
    Example("連絡先は090-1234-5678です。", ((PHONE, "090-1234-5678"),)),
    Example("メールはtaro.yamada@example.co.jpです。", ((EMAIL, "taro.yamada@example.co.jp"),)),
    Example(f"個人番号は{_MYNUM}です。", ((MYNUM, _MYNUM),)),
    Example("住所は東京都渋谷区神宮前1丁目2番3号です。", ((ADDR, "東京都渋谷区神宮前1丁目2番3号"),)),
    Example("株式会社極秘技研のプロジェクトです。", ((ORG, "株式会社極秘技研"),)),
    Example("生年月日は1985年4月12日です。", ((DOB, "1985年4月12日"),)),
    Example(
        "export OPENAI_KEY=sk-abcdefghijklmnopqrstuvwxyz0123",
        ((APIKEY, "sk-abcdefghijklmnopqrstuvwxyz0123"),),
        context="source_code",
    ),
]

NEGATIVES: list[Example] = [
    Example("リリース日は2026年7月24日です。"),
    Example("build_idは09012345678です。", context="source_code"),
    Example("クラス名はSakuraServiceです。", context="source_code"),
    Example("チケット番号は123-4567です。"),
    Example("テストデータのUserクラスを生成してください。", context="source_code"),
]

ALL: list[Example] = POSITIVES + NEGATIVES
