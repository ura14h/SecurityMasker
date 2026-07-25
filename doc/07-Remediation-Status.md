# 07 — doc/06 Remediation Status

作成日: 2026-07-25 / 更新: 2026-07-25（第2回監査の是正を反映）

> **第2回監査（HEAD `50925d6`）の結果、本書の初版にあった複数の `done` 判定は実装と一致していなかった。**
> 指摘された8件のリリースブロッカーはすべて再現確認のうえ修正し、回帰テストを追加した（`Audit fix 1..5`）。
> 判定の誤りの内訳と是正内容は末尾「第2回監査の是正」を参照。

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

## 第2回監査の是正（`Audit fix 1..5`）

初版で `done` としたが**実装が伴っていなかった**項目と、その是正。すべて再現テストを先に追加した。

| # | 監査指摘（再現済み） | 是正 | 回帰テスト |
|---|---|---|---|
| 1 | 最終ガードがSecretパターンとマイナンバーしか再検査せず、未知フィールドのカード/メール/電話が通過。登録値の比較もcase-sensitive | ガードを**全決定論的検出器**（fuzzy NER以外）へ拡張。登録リテラルはcasefold比較。自セッションのaliasは除外 | `test_leak_gate.py` |
| 2 | ヘッダー素通しで、カスタムヘッダーの秘密・他Provider認証が到達 | **Provider別allowlist**（deny by default）。非認証ヘッダーは検査して**block** | `test_leak_gate.py` |
| 3 | multi-tenantがクライアント指定ヘッダーを信用（偽装可能） | tenantを**HMAC証明**必須化。秘密未設定なら起動失敗。公開bindは明示承認制 | `test_tenant_auth.py` |
| 4 | `previous_response_id` を stable 扱いし3ターン目で対応表が分岐 | **response-id → session binding**（memory/Redis両対応）。未解決なら fail-closed | `test_multiturn_session.py` |
| 5 | alias保護が4profileのみで IPv4/UUID/numeric が再マスク | 保護を**発行済みalias集合の厳密一致**へ変更（全profile対応） | `test_alias_stability.py` |
| 6 | 上限超過後もbufferへappendし続け、上限が機能せず | 超過で**蓄積停止＋破棄**。overflow/done欠落は`error`イベントで**可視的にfail-closed** | `test_limits.py` |
| 7 | Redis lockが固定30秒・更新なしで処理中に失効 | **owner検証付きTTL更新（watchdog）** | `test_redis_store.py` |
| 8 | readinessがstore障害を検出せず。dev透過モードが実Providerへ送信可 | `/ready`が**storeを実プローブ**。dev透過は**loopback上流のみ**許可 | `test_tenant_auth.py` |

その他の是正:

- policy安全格子が entity floor しか比較せず、低priorityの `block` が高priorityの `literal` に負けた
  → **実効強度**（自身のpolicyとfloorの厳しい方）で比較。
- strict config: 空値・重複値・範囲外 priority/score/group を**起動時に拒否**。
- request body: `Content-Length` 事前判定＋**ストリーム読み込み中**に上限強制（全量メモリ化しない）。
- 供給網: lock を **runtime専用**（21パッケージ）と **dev用**に分離。本番イメージから
  pytest/mypy/ruff/hypothesis が消えたことを実イメージで確認。CI は lock からインストール。
- composeデモが `PROD_DB_HOST`/`INTERNAL_API_KEY` 未設定で起動失敗した問題を修正（合成値を供給）。

### 第2回監査で指摘され、なお未対応の項目

以下は**引き続き未実装**であり、「対応済み」とは表現しない（上記「既知の制限」と同じ扱い）:

- URL/file path の構造保持、alias長のADR（24bit初期値・IPv4は254通り）
- detector timeout / ReDoS対策 / 文脈分類 / metrics・audit の完全配線
- `securitymasker run` のproxy設定自動化、doctorのstore/master key/upstream/public bind検査
- Docker/Redis image の digest 固定
- 日本固有: 未登録の氏名・法人名・地名、EAIメール、番地なし住所、旅券・免許・在留・年金・
  雇用保険・健康保険・銀行/業務ID、クラウド資源識別子
- 評価コーパスは依然として小規模（正例8・負例5、氏名/法人は辞書登録済み）。
  **表示される F1=1.00 は日本語PIIの実用性能を意味しない。**

## 第3回監査の是正（`Audit fix 6`）

第2回是正後もなお残っていた10件。すべて再現確認のうえ修正し、`tests/unit/test_audit_round3.py` 等に回帰テストを追加した。

| # | 指摘 | 是正 |
|---|---|---|
| 1 [P0] | 設定エラーに辞書の**実値**が露出（重複値のメッセージ＋PydanticのValidationError文字列化） | エラーは**位置（`values[i]`）のみ**を報告。Pydanticエラーは `loc` と `msg` だけを抽出し、`from None` で入力値を含む連鎖トレースも遮断 |
| 2 [P0] | `securitymasker run` が**全引数と生session ID**をstderrへ出力 | 実行ファイル**名のみ**＋session IDは**SHA-256短縮fingerprint**。引数は件数のみ表示 |
| 3 [P1] | Redis lockの更新失敗・戻り値0を握り潰し、所有権喪失後も継続 | `LockHandle.check()` を導入。watchdogが喪失を検知すると**送信前に `SessionError` で中断** |
| 4 [P1] | InMemory storeの`delete`が**保持中のlockごと削除**し二重侵入可能 | lockのライフサイクルをsessionから分離（`delete`はsessionのみ削除） |
| 5 [P1] | 未知の`previous_response_id`をalias**形状ヒューリスティック**で判定し、numeric/UUID aliasが素通り | binding未解決なら**payload内容に関わらず一律409** |
| 6 [P1] | Codexの`session-id`/`thread-id`等がallowlistから脱落（綴り違い） | 設計書(doc/05)準拠のヘッダー群＋`x-codex-*`を透過 |
| 8 [P1] | lockに`redis`欠落、compose profileがGatewayを未設定、公開bind承認をイメージに固定、HEALTHCHECKが`/health` | lockへ`redis`追加、composeで`SECURITYMASKER_STORE`/URL/master keyを設定、**承認をイメージから除去しcompose側で明示**、HEALTHCHECKを`/ready`へ |
| 9 [P1] | invalid tool JSONをrawで再送 | overflow同様に**provider互換 `error` イベント**でfail-closed（両プロトコル） |
| 10 [P1] | `build_leak_scanners`と`build_engine`で検出器を二重構築し、spaCy/HFモデルを2回ロード | パイプラインを**1回だけ構築して共有** |

`/ready` のプローブがtenant付きで作成しtenantなしで削除していた不整合も修正した。

### 7 [P1] multi-tenantのuser分離 — **未実装（既知の制限として明記）**

HMACは**tenant IDのみ**の静的proofで、store keyにuser IDが入らない。したがって
**同一tenant内の利用者間は分離されない**（他利用者のsession IDを指定可能）。
`namespaced_key()` は user 引数を受け付ける形にしたが、**認証主体がuser IDを表明・署名する仕組みは未実装**。

- **安全な構成**: 単一利用者のローカル運用、または「1テナント＝1顧客」で信頼プロキシがヘッダーを完全管理する構成。
- **安全でない構成**: 1テナント内に相互不信の複数利用者がいる構成。この用途では**使用しないこと**。

## 第3回監査の未対応理由に対する再評価

監査から「延期理由が不妥当」と判定された項目は、以下のとおり受け入れる。**いずれも未実装であり、
`done` とは表現しない**:

- **URL/file path構造保持** — 標準ライブラリで実装可能。未実装のまま該当profileを有効にしているのは
  不変条件3の観点で不適切。→ 次の作業単位で「実装」または「該当profileのblock化」を行う。
- **alias長（24bit / IPv4 254通り）** — 依存不要。ADR作成とtoken長変更が必要。
- **日本語評価コーパス（正例8/負例5）** — 合成fixture拡充に依存追加は不要。**現状のF1=1.00は製品判断に使用不可**。
- **EAIメール・番地なし住所** — 依存承認を理由にできない。決定論的処理で改善可能。
- **doctor/run自動化・image digest固定** — 依存不要。（`run`の生引数ログのみ本回で即時修正済み。）
- **Detector timeout/ReDoS** — 10MB上限ではcatastrophic regexを防げないという指摘を受け入れる。

## 完了検証コマンド

```bash
ruff check src tests
mypy src
pytest tests/unit tests/evaluation -q
SM_RUN_LIVE=1 pytest tests/integration/test_live_gateway.py -q   # 明示許可時のみ
```
