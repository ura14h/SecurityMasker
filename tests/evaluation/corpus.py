"""Anonymized Japanese evaluation corpus (§31). Synthetic data only (§30).

Mixes prose, chat, logs, config, and source code with positive and negative
examples. Gold labels are ``(entity_type, surface)`` pairs; negatives have none.
Names/orgs are covered by the dictionary the evaluation engine is built with.
"""

from __future__ import annotations

from dataclasses import dataclass

from securitymasker.detectors.japanese_identifiers import residence_check_digit
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
CARD = EntityType.CREDIT_CARD.value
DBCONN = EntityType.DB_CONNECTION_STRING.value
RESIDENCE = EntityType.JP_RESIDENCE_CARD.value
PASSPORT = EntityType.JP_PASSPORT_NUMBER.value
LICENSE = EntityType.JP_DRIVER_LICENSE_NUMBER.value
PENSION = EntityType.JP_PENSION_NUMBER.value
EMPINS = EntityType.JP_EMPLOYMENT_INSURANCE_NUMBER.value
BANK = EntityType.JP_BANK_ACCOUNT.value

# Synthetic residence-card number with a valid check digit (§30).
_RESIDENCE = f"AB1234567{residence_check_digit('1234567')}CD"

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
    # --- 表記ゆれ: full-width, ideographic space, combining marks -------------
    # Registered as 山田太郎; written here with an ideographic space. The gold is
    # the SURFACE as it appears, which is what gets replaced.
    Example("担当者は山田　太郎（やまだ たろう）です。", ((PERSON, "山田　太郎"),)),
    Example("連絡先は０９０－１２３４－５６７８です。", ((PHONE, "０９０－１２３４－５６７８"),)),
    Example("メールは TARO.YAMADA@EXAMPLE.CO.JP です。",
            ((EMAIL, "TARO.YAMADA@EXAMPLE.CO.JP"),)),
    # --- EAI / 国際化メール (doc/06 §5.4) -------------------------------------
    Example("連絡先は山田＠example.co.jpです。", ((EMAIL, "山田＠example.co.jp"),)),
    Example("問い合わせは山田太郎@例え.jpまで。", ((EMAIL, "山田太郎@例え.jp"),)),
    # --- 住所のバリエーション (doc/06 §5.5) -----------------------------------
    Example("送付先は東京都渋谷区神宮前1-2-3です。", ((ADDR, "東京都渋谷区神宮前1-2-3"),)),
    Example("所在地は東京都渋谷区神宮前1丁目2番3号 秘密ビル401です。",
            ((ADDR, "東京都渋谷区神宮前1丁目2番3号 秘密ビル401"),)),
    # --- 日本固有の公的・業務識別子 (doc/06 §5.7) -----------------------------
    Example(f"在留カード番号は{_RESIDENCE}です。", ((RESIDENCE, _RESIDENCE),)),
    Example("旅券番号はTK1234567です。", ((PASSPORT, "TK1234567"),)),
    Example("運転免許証番号は123456789012です。", ((LICENSE, "123456789012"),)),
    Example("基礎年金番号は1234-567890です。", ((PENSION, "1234-567890"),)),
    Example("雇用保険被保険者番号は1234-567890-1です。", ((EMPINS, "1234-567890-1"),)),
    Example("振込先の口座番号は1234567です。", ((BANK, "1234567"),)),
    # --- secrets in code/config contexts --------------------------------------
    Example("ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz01",
            ((APIKEY, "sk-ant-abcdefghijklmnopqrstuvwxyz01"),), context="source_code"),
    Example("db: postgres://admin:hunter2@db.internal.example:5432/app",
            ((DBCONN, "postgres://admin:hunter2@db.internal.example:5432/app"),),
            context="yaml_scalar"),
    Example("カード番号は4111111111111111です。", ((CARD, "4111111111111111"),)),
]

NEGATIVES: list[Example] = [
    Example("リリース日は2026年7月24日です。"),
    Example("build_idは09012345678です。", context="source_code"),
    Example("クラス名はSakuraServiceです。", context="source_code"),
    Example("チケット番号は123-4567です。"),
    Example("テストデータのUserクラスを生成してください。", context="source_code"),
    # --- 数値の衝突: 公的番号と紛らわしいが文脈のない業務ID ------------------
    Example("注文番号は1234567です。"),
    Example("ビルド番号は123456789012が最新です。", context="source_code"),
    Example("エラーコードは1234-567890を参照。", context="source_code"),
    Example("バージョン 2001.0.113 をリリースしました。"),
    # 16 digits but NOT Luhn-valid: an ordinary id must not be taken for a card.
    # (A Luhn-VALID 16-digit run is treated as a card by design — fail-closed.)
    Example("commit 4111111111111112 は取り消されました。", context="source_code"),
    # --- 一般的な語・識別子との誤検出 ----------------------------------------
    Example("さくらの季節になりました。"),
    Example("葵さんという名前のクラスを作りました。", context="source_code"),
    Example("ひかりネットワークの構成を確認します。"),
    Example("東京駅で待ち合わせます。"),
    Example("株式会社の登記について教えてください。"),
    Example("email という変数名を使っています。", context="source_code"),
    Example("IPアドレスの説明: 192.0.2.1 は文書用の例です。"),
    Example("パスポートの申請方法を教えてください。"),
    Example("運転免許の更新時期はいつですか。"),
    Example("口座を開設したいです。"),
    Example("SELECT * FROM users WHERE id = 1234567;", context="source_code"),
    Example("def build_id(): return 1234567890", context="source_code"),
]

ALL: list[Example] = POSITIVES + NEGATIVES
