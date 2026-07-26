"""日本語NER backendを比較するための合成corpus。

Everything here is invented. No real person, company, or address appears — names
are constructed from common surname/given-name characters, organisations use
obviously fictional coinages, and addresses use the documentation-style ward and
block numbers already used elsewhere in the suite.

The corpus is deliberately adversarial in both directions, because a NER backend
is only useful to us if it is good at BOTH:

- ``POSITIVES`` — unregistered names/orgs/places we want found (recall), across
  kanji, hiragana, katakana, romaji, spacing variants, and honorifics;
- ``NEGATIVES`` — text that must NOT be flagged (precision), split into ordinary
  prose ambiguity (さくら/葵/ひかり as words, station and product names) and code
  contexts (identifiers, class names, imports) where a name-shaped token is
  almost never a person.

Gold labels are ``(entity, surface)``. LOC is labelled separately from an actual
postal address: conflating "Tokyo" with someone's home address is a precision
problem we want to be able to see, not average away.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PERSON = "PERSON"
ORG = "ORGANIZATION"
LOC = "LOCATION"


@dataclass(frozen=True)
class NerExample:
    text: str
    gold: tuple[tuple[str, str], ...] = ()
    context: str = "prose"
    note: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


# --- positives: unregistered entities we want detected ---------------------------

POSITIVES: list[NerExample] = [
    # kanji names
    NerExample("担当者は佐々木健一です。", ((PERSON, "佐々木健一"),), tags=("kanji",)),
    NerExample("申込者の高橋美咲さんに連絡してください。", ((PERSON, "高橋美咲"),), tags=("kanji", "honorific")),
    NerExample("契約者は中村大輔、連絡先は後述。", ((PERSON, "中村大輔"),), tags=("kanji",)),
    NerExample("代表者名: 小林彩花", ((PERSON, "小林彩花"),), tags=("kanji", "field")),
    # spacing variants
    NerExample("担当は 佐々木 健一 が務めます。", ((PERSON, "佐々木 健一"),), tags=("kanji", "space")),
    NerExample("氏名：高橋　美咲", ((PERSON, "高橋　美咲"),), tags=("kanji", "ideographic-space")),
    # hiragana / katakana / romaji
    NerExample("お客様のやまだたろう様よりご連絡。", ((PERSON, "やまだたろう"),), tags=("hiragana",)),
    NerExample("申込者はサトウハナコさんです。", ((PERSON, "サトウハナコ"),), tags=("katakana",)),
    NerExample("The engineer Kenichi Sasaki joined.", ((PERSON, "Kenichi Sasaki"),), tags=("romaji",)),
    # organisations (invented)
    NerExample("株式会社桜庭電機と契約しました。", ((ORG, "株式会社桜庭電機"),), tags=("org",)),
    NerExample("有限会社みどり物産の担当者。", ((ORG, "有限会社みどり物産"),), tags=("org",)),
    NerExample("合同会社ホシノ技研が受注。", ((ORG, "合同会社ホシノ技研"),), tags=("org",)),
    NerExample("弊社は青嶺システムズと協業します。", ((ORG, "青嶺システムズ"),), tags=("org", "no-suffix")),
    # places
    NerExample("会場は神奈川県横浜市です。", ((LOC, "神奈川県横浜市"),), tags=("loc",)),
    NerExample("出張先は北海道旭川市になりました。", ((LOC, "北海道旭川市"),), tags=("loc",)),
    NerExample("支店は福岡県福岡市博多区にあります。", ((LOC, "福岡県福岡市博多区"),), tags=("loc",)),
    # mixed
    NerExample("株式会社桜庭電機の佐々木健一が神奈川県横浜市を訪問。",
               ((ORG, "株式会社桜庭電機"), (PERSON, "佐々木健一"), (LOC, "神奈川県横浜市")),
               tags=("mixed",)),
]


# --- negatives: prose ambiguity ---------------------------------------------------

NEGATIVES_PROSE: list[NerExample] = [
    NerExample("さくらの開花予想を教えてください。", note="flower, not a name", tags=("ambiguous",)),
    NerExample("葵の花言葉は何ですか。", note="plant, not a name", tags=("ambiguous",)),
    NerExample("ひかり回線の速度を測定します。", note="product word", tags=("ambiguous",)),
    NerExample("みどりの窓口で切符を買いました。", note="common noun", tags=("ambiguous",)),
    NerExample("今日は快晴で気持ちがよいです。", note="plain prose"),
    NerExample("この機能の仕様を説明してください。", note="plain prose"),
    NerExample("会議は明日の午後三時からです。", note="plain prose"),
    NerExample("プロジェクトの進捗を共有します。", note="plain prose"),
]


# --- negatives: code contexts -------------------------------------------------------

NEGATIVES_CODE: list[NerExample] = [
    NerExample("class SakuraService:\n    pass", context="source_code",
               note="class name", tags=("identifier",)),
    NerExample("def aoi_handler(request):\n    return None", context="source_code",
               note="function name", tags=("identifier",)),
    NerExample("import hikari_client as client", context="source_code",
               note="module name", tags=("identifier",)),
    NerExample("const midoriConfig = { retries: 3 };", context="source_code",
               note="variable name", tags=("identifier",)),
    NerExample("SELECT * FROM sakura_orders WHERE id = 1;", context="source_code",
               note="table name", tags=("identifier",)),
    NerExample("$ kubectl get pods -n aoi-namespace", context="shell",
               note="namespace name", tags=("identifier",)),
    NerExample('{"service": "midori-api", "port": 8080}', context="json_string",
               note="service name", tags=("identifier",)),
    NerExample("service: hikari-gateway\nreplicas: 2", context="yaml_scalar",
               note="service name", tags=("identifier",)),
    NerExample("--- a/sakura.py\n+++ b/sakura.py\n@@ -1 +1 @@\n-x = 1\n+x = 2",
               context="diff", note="file name", tags=("identifier",)),
    NerExample("git commit -m 'fix aoi parser'", context="shell",
               note="commit message", tags=("identifier",)),
]

NEGATIVES: list[NerExample] = NEGATIVES_PROSE + NEGATIVES_CODE
ALL: list[NerExample] = POSITIVES + NEGATIVES


def summary() -> dict[str, int]:
    return {
        "positives": len(POSITIVES),
        "negatives_prose": len(NEGATIVES_PROSE),
        "negatives_code": len(NEGATIVES_CODE),
        "gold_spans": sum(len(e.gold) for e in POSITIVES),
    }
