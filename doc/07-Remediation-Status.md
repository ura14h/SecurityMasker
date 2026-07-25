# 07 — doc/06 Remediation Status

作成日: 2026-07-25

`doc/06-Issue.md` の是正実装の到達状況を、コードとテストに一致する形で記録する。
「implemented / tested」「partial」「deferred」を区別し、誇大な保証を残さない（doc/06 §10, P2-4）。
コミットはマイルストーン境界ごと（`git log` の `Milestone A..E`）。

## Milestone A — 外部送信ゲート（実装・テスト済み）

| 項目 | 状態 | 主なコード / テスト |
|---|---|---|
| P0-1 設定必須・readiness分離 | done | `gateway/runtime.py`, `gateway/app.py` / `test_gateway_hardening.py` |
| P0-2 不正/非object/未対応エンコーディング拒否 | done | `app._read_json_object` / 同上 |
| P0-3 ルートallowlist（catch-all廃止） | done | `app.create_app` / 同上 |
| P0-4 最終ペイロード全体のblock-only漏えいガード | done | `engine.assert_no_leak_in_payload` / 同上 |
| P0-5 body上限＋検出器の暗黙トランケーション廃止 | done | `app.MAX_BODY_BYTES`, `detectors/regex.py` / 同上 |
| 内部ヘッダ除去・認証は正しい上流へ透過 | done | `app._client_headers` / 同上 |

## Milestone B — 設定・Detector障害のfail-closed（実装・テスト済み）

| 項目 | 状態 | 備考 |
|---|---|---|
| P1-2 strict config（extra禁止・version・duration・regex group/profile） | done | `test_config_fail_closed.py` |
| P0-6 `value_from_env` 未設定/空で起動失敗 | done | |
| P0-6 presidio/ner 必須ロード失敗で起動失敗、実行時例外でblock | done | |
| fail_mode配線（closed既定、openはfuzzy NERのみskip、重大Secretは常にfail-closed） | done | |
| preserve_aliases / session TTL / doctorの実設定検証 | done | |
| inject_alias_instruction 配線 | done (Milestone D) | `test_alias_instruction.py` |

## Milestone C — セッション・テナント・tool trust（実装・テスト済み）

| 項目 | 状態 | 備考 |
|---|---|---|
| P0-7 発行済みaliasのメンバーシップ確認 | done | `detectors/existing_alias.py` |
| P0-8 tool-argument trust（既定未信頼、allowlistのみliteral復元、stream両対応） | done | `tool_trust.py`, 両adapter, 両stream |
| P0-9 テナント分離（local/multitenant、namespace鍵、未解決はfail-closed） | done | `gateway/session.py`, `test_session_tenant.py` |
| P1-1 session選択の安定化＋alias有・stable無でblock | done | 同上 |
| P1-9 Redis配線＋lock（owner token・bounded wait・atomic release・get/create/save同一排他） | done | `sessions/redis.py`, `test_redis_store.py` |

## Milestone D — 検出・構造保持（一部実装、一部deferred）

| 項目 | 状態 | 備考 |
|---|---|---|
| P0-7 Unicode結合文字（base+combining合成）＋span map | done | `normalization.py`, `test_normalization.py` |
| P1-3 重大Secretの最低安全ポリシー（priorityで弱められない） | done | `policy.py`, `test_policy_safety.py` |
| P1-4 Secret Detector拡張（prefix/format固定、低FP） | done | `detectors/secret_patterns.py` |
| §5.6 My Number min_scoreゲート | done | `detectors/japanese_my_number.py` |
| §5.7 法人番号（check digit＋文脈/ T接頭辞、公開情報でliteral既定・opt-in） | done | `detectors/japanese_corporate_number.py` |
| P1-6 URL/file_path/numericの完全な構造保持 | **deferred** | 現状はnumericのみ桁数保持。url/file_pathは不透明alias（安全だが構造非保持）。下記「既知の制限」参照 |
| P1-7 コード/Markdown/shell等の文脈分類 | **deferred** | NERはcode文脈skipを実装済みだが、本文の文脈分類器は未実装 |
| P1-8 alias長のADR | **deferred** | 現行token長は既存実装のまま。定量評価とADRは未 |
| §5.3 未登録の氏名/法人/地名のNER補完 | **deferred（要依存承認）** | HF `transformers` 追加が必要。未承認のため未実装 |
| §5.7/§5.8 旅券/免許/在留/年金/保険/銀行/クラウド識別子 | **deferred** | 多くは公式checksum確認かユーザーRegex委譲。EntityType宣言のみで「対応済み」とはしない |

## Milestone E — streaming・運用・供給網・文書

| 項目 | 状態 | 備考 |
|---|---|---|
| P1-10 stream buffer上限＋done欠落で部分literal復元しない | done | `test_limits.py` |
| P1-5 session mapping上限（fail-closed） | done | `aliases/factory.py` |
| P1-5 detector timeout / ReDoS対策 | **partial** | body/field上限は実施。同期detectorのtimeout/circuit breakerは未。ReDoSはユーザーRegex設計依存 |
| P2-1 doctor実設定検証・CLI sessions honesty | done | `sessions`コマンドは未実装として非0終了 |
| P2-2 metrics/audit のGateway接続 | **partial** | 安全labelのlogは有。網羅的metrics/audit配線は未 |
| P2-3 再現ビルド（Docker lock導入・prod非mock・段分離） | done | `Dockerfile`（runtime/demo段）, `docker-compose.yml`。base image digest固定は運用者向けに手順記載 |
| P2-4 ドキュメント整合 | done（本書＋主要doc修正） | 設定なし=透過等の誤記を修正 |

## 既知の制限（誇大保証を避けるための明示）

- **URL / file_path プロファイルは構造保持ではない。** 現状は不透明alias（`SM_VALUE_...`）で、
  漏えいはしないが URL/パス構文としての妥当性は保証しない。構造保持が必要な用途は
  ユーザーRegexで対象コンポーネントを個別指定するか、P1-6 実装まで待つこと。
- **文脈分類（code/markdown/shell/JSON）は未実装。** 本文は概ね prose として扱う。
  辞書・高信頼Secret・決定論的検出器はコード文脈でも動作するが、あいまいNERの
  文脈別ポリシーは未整備。
- **未登録の日本語氏名/法人/地名の自動検出には NER（`transformers`）が必要で未導入。**
  ユーザー辞書が最優先・最信頼。NER 追加は依存承認後に実施する。
- **日本固有の公的/業務識別子（旅券・免許・在留・年金・保険・銀行・クラウド）は限定的。**
  EntityType の宣言のみでは「対応済み」としない。必要なものはユーザーRegex/辞書で登録する。
- **同期detectorのtimeout/circuit breakerは未実装。** 入力サイズ上限で最悪ケースを抑制する。
- **CLI の `sessions` 系は未実装**（共有ストア前提）。非0終了で明示する。

## 完了検証コマンド

```bash
ruff check src tests
mypy src
pytest tests/unit tests/evaluation -q
SM_RUN_LIVE=1 pytest tests/integration/test_live_gateway.py -q   # 明示許可時のみ
```
