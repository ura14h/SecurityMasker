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
| P0-9 テナント/ユーザー分離 | **done**（第2次で完了） | `tenant` / `tenant_user` の2モード。ADR-0008 |
| P1-1 session選択の安定化＋alias有・stable無でblock | done | `gateway/session.py`, `test_multiturn_session.py` |
| P1-9 Redis配線＋lock | **partial** | owner token・bounded wait・atomic release・TTL更新・書き込み前の所有権再確認まで実装。**fencing tokenによる完全な原子性は未実装**（下記「残存リスク」） |

## Milestone D — 検出・構造保持

| 項目 | 状態 | 備考 |
|---|---|---|
| P0-7 Unicode結合文字（base+combining合成）＋span map | done | `normalization.py`, `test_normalization.py` |
| P1-3 重大Secretの最低安全ポリシー（priorityで弱められない） | done | `policy.py`, `test_policy_safety.py` |
| P1-4 Secret Detector拡張（prefix/format固定、低FP） | done | `detectors/secret_patterns.py` |
| §5.6 My Number min_scoreゲート | done | `detectors/japanese_my_number.py` |
| §5.7 法人番号（check digit＋文脈/ T接頭辞、公開情報でliteral既定・opt-in） | done | `detectors/japanese_corporate_number.py` |
| P1-6 URL/file_path/numericの構造保持 | **done** | `aliases/structure.py`。再構築できない値はblock |
| P1-7 コード/Markdown/shell等の文脈分類 | **done**（第2次で完了） | `context/segmenter.py` |
| P1-8 alias長のADR | **done** | ADR-0007。48bit へ引き上げ、IPv4は762通り、枯渇はfail-closed |
| §5.3 未登録の氏名/法人/地名のNER補完 | **done（optional・既定OFF）** | ADR-0009。実測比較のうえ採用 |
| §5.7 旅券/免許/在留/年金/保険/銀行 | **partial** | `japanese_identifiers.py`。在留カードのみcheck digit検証、他は形式＋文脈語。§5.8 クラウド識別子はユーザーRegex委譲 |

## Milestone E — streaming・運用・供給網・文書

| 項目 | 状態 | 備考 |
|---|---|---|
| P1-10 stream buffer上限＋done欠落＋invalid JSON | done | `test_limits.py`。**JSON validityはtrustと独立**に全tool callで検査（第4次で是正） |
| P1-5 session mapping上限（fail-closed） | done | `aliases/factory.py` |
| P1-5 detector timeout / ReDoS対策 | **done**（第2次で完了） | `detectors/safety.py` ＋ 検出器ごとの時間予算 |
| P2-1 doctor実設定検証・CLI sessions honesty | done | `sessions`コマンドは未実装として非0終了 |
| P2-2 metrics/audit のGateway接続 | **partial** | 安全labelのlogは有。網羅的metrics/audit配線は未 |
| P2-3 再現ビルド | **done** | runtime/demo段分離、lock分離、**base image digest固定＋静的検査**|
| P2-4 ドキュメント整合 | done（本書＋主要doc修正） | 設定なし=透過等の誤記を修正 |

## 既知の制限（第1次時点。第2次での解消状況は後段の表を参照）

> 以下のうち URL/file_path・文脈分類・NER・detector timeout は**第2次作業で解消済み**。
> 残っているものだけを「なお残る制限」として後段に再掲する。

- **日本固有の公的/業務識別子は限定的。** EntityType の宣言のみでは「対応済み」としない。
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

## 製品ギャップの解消（`Gap 1..4`）

監査が「リリースブロッカー」と判定した製品ギャップのうち、依存追加なしで解消できるものを実装した。

| ギャップ | 状態 | 内容 |
|---|---|---|
| URL/file path の構造保持（不変条件3） | **done** | `aliases/structure.py`。scheme/port/深さ/絶対相対/POSIX・Windows・UNC/ドライブ/末尾スラッシュ/クエリkeyと順序/フラグメント有無/拡張子（`.tar.gz`含む）を保持し、host・userinfo・全セグメント・全クエリ値・fragment・UNCのserver/shareを置換。安全に再構築できない値（scheme無し、`mailto:`/`data:`/`javascript:`、不正port、空パス）は **block** |
| alias空間（24bit / IPv4 254通り） | **done** | ADR-0007。token を 12hex(48bit) へ（10k mapping で衝突確率 ~1.8e-10）。IPv4 は RFC 5737 の3レンジで762通り。**枯渇時は alias 再利用せず fail-closed**（テストで実証） |
| Detector timeout / ReDoS | **done** | `detectors/safety.py` が設定読込時に破滅的バックトラッキング形状を拒否（`(a+)+$` は24文字で実測0.9秒）。engine は検出器ごとに時間予算（既定10秒）を課し、超過は fail-closed。※`re` は中断できないため「待たない」だけである点をコードに明記 |
| EAIメール（§5.4） | **done** | 全角＠・日本語ローカル部・IDNドメインを検出。日本語は語間空白が無いため、**厳密パターン（ひらがな除外）を優先し、区切り位置限定の補助パターンでひらがなローカル部も拾う**2段構成。「連絡先は山田＠…」から `山田＠…` だけを正しく切り出す |
| 日本固有の公的・業務識別子（§5.7） | **partial** | `japanese_identifiers.py`。在留カード（**公式チェックディジット検証**）、旅券・運転免許・基礎年金・雇用保険・健康保険・銀行口座は**形式＋文脈語を必須**とし、裸の数字では発火しない。公開チェックディジットが無いものは形式＋文脈のみである旨をコードに明記 |
| 表記ゆれ（氏名の空白）§5.3 | **done** | 辞書が「山田太郎」登録で「山田 太郎」「山田　太郎」にも一致（CJK語のみ、改行は跨がない） |
| 評価コーパス | **改善（8/5 → 24/22）** | 17エンティティ型、表記ゆれ・EAI・住所バリエーション・公的識別子・コード/YAML文脈を追加。負例に**公的番号と紛らわしい業務ID**（注文番号・ビルド番号・非Luhnの16桁・文書用IP・「さくら」「葵」等の曖昧語）を22件追加 |

**評価結果の読み方（重要）**: 現在 `P=R=F1=1.00 (tp=24)` だが、これは**合成コーパス上の値**であり、
実運用の日本語PII性能を意味しない。コーパスは検出器の実装者が作成しており、
未登録氏名・法人名・地名は依然としてNER無しでは検出できない。

## 製品ギャップの解消（第2次・branch `codex/close-product-gaps`）

前節で「未実装」としていた項目は、オーナー承認のもとすべて実装した。

| 項目 | 状態 | 実装 |
|---|---|---|
| `securitymasker run` のproxy経路保証 | **done** | `/ready` が `ready:true` でなければ**子プロセスを起動しない**。Claude は `ANTHROPIC_BASE_URL`＋セッションヘッダ（既存 `ANTHROPIC_CUSTOM_HEADERS` は保全マージ）、Codex は**プロセス単位 `-c` override**（`~/.codex/config.toml` は不変更）。direct provider 環境変数、未知ツールは**拒否**。引数・生session IDはログしない |
| 文脈分類 | **done** | `context/segmenter.py`。prose / fenced・inline code / shell / JSON / YAML / diff を**ロスレス**に分割（property test 済み）。offsetは原文絶対座標へ復元。fuzzy NER のみ code 系を skip、辞書と決定論的検出器は全context動作 |
| 同一tenant内のuser分離 | **done** | ADR-0008。`local` / `tenant` / `tenant_user` の3モード。tenant+user+timestamp を**長さ前置・版付き canonical payload で一体署名**、constant-time 比較。store key・response binding・session読取検証すべてに適用 |
| 未登録の日本語氏名・法人名・地名 | **done（optional）** | ADR-0009。Presidio と HF 2候補を**実測比較**して `tsmatz/xlm-roberta-ner-japanese` を採用。既定OFF・revision/digest固定・`local_files_only`・safetensorsのみ・`trust_remote_code` 不使用 |
| doctor の runtime 検査 | **done** | 20項目超を個別checkとして列挙。失敗時 non-zero、secret非出力、実provider不接触、`--json` 対応 |
| image digest 固定 | **done** | python/redis を multi-arch index digest で固定。**未固定を検出する静的テスト**を追加（dev lock の redis 欠落を実際に検出） |
| Detector timeout / ReDoS | **done** | 破滅的パターンを設定読込時に拒否＋検出器ごとの時間予算 |

### なお残る制限（`done` とは書かない）

- **NERの評価は合成コーパス上の値**である。ADR-0009 の F1 は回帰ベースラインであり、実運用性能の主張ではない。
- **Python パッケージの hash 検証・image 署名/provenance・SBOM・CIでの脆弱性scan は未実装**（`docs/operations.md` の表に明記）。
- 住所の「番地なし」「建物名の空白分離」は一部改善にとどまる。
- 識別子系のうち公開チェックディジットが無いもの（旅券・免許・年金・雇用保険・健康保険・銀行口座）は**形式＋文脈語のみ**の判定である。

## 完了検証コマンド

```bash
ruff check src tests
mypy src
pytest tests/unit tests/evaluation -q
SM_RUN_LIVE=1 pytest tests/integration/test_live_gateway.py -q   # 明示許可時のみ
```


## 第4回監査の是正（`R1..R9`）

前節で `done` としていた4項目は、**実装はあっても配線・検証が伴っていなかった**。監査の指摘どおり
`partial` へ戻したうえで是正し、下表の状態に更新した。

| 項目 | 第3次の表記 | 実際 | 現在 |
|---|---|---|---|
| NER供給網・製品配線 | done | lockなし・Docker不在・safetensors非強制・digest検査が欠落ファイルを見逃す | **partial**（第5回で是正 → 下表）。lockが真の推移閉包でなく（`huggingface-hub`欠落）、manifestは6成果物中3件しか固定しておらず「完全検査」は成立していなかった |
| 文脈分類 | done | fenced/inline/bare-diff のみ。裸のshell/JSON/YAML/source/apply_patchはprose。`PATCH`未生成。O(n²) | **partial**（第5回で是正 → 下表）。1行形のshell/SQL/コード文が prose のままで、`_outside_fence` も線形ではなかった |
| doctor完全配線 | done | Gateway不通でも exit 0、config欠落でtraceback、detector 3重構築 | **partial**（第5回で是正 → 下表）。`doctor` 自身が engine を2回構築していた |
| user assertionのreplay対策 | done | timestamp任意で無期限再利用可、`nan`が時刻窓を通過 | **done**（timestamp必須、整数epoch限定、NaN/Inf/小数/前後窓を拒否） |
| NER timeout | done | `wait_for`は待機を終えるだけでworkerは継続 | **done**（固定プール＋在庫上限＋過負荷拒否。「timeoutが推論を止める」記述は訂正） |

### なお `partial` / 未実装のまま

- **`securitymasker run` の保証範囲** — 「実Codex CLIでparse検証済み」という第4次の記述は
  **撤回済み**（`--strict-config` は `login` / `mcp` で受け付けられず、`--version` / `--help` は
  設定構築前に終了するため、当時の手順は何も検証していなかった）。
  **第7回で実CLI E2Eを実装し、この項目は解消した**（下記）。
- **供給網**: package hash検証・image署名/provenance・SBOM・CI脆弱性scan は引き続き**未実装**。
- **日本固有識別子**: 公開チェックディジットが無いものは形式＋文脈語のみ。
- **NER評価**: 合成コーパス上の値であり、実運用性能ではない。

## 第5回監査の是正（`R10..R16`）

第4次で `done` とした4項目のうち3項目は、監査の指摘どおり**実装はあっても完全ではなかった**。
上表の「現在」列を `partial` へ戻したうえで是正した結果が下表である。

| 項目 | 第4次の主張 | 実際 | 現在 |
|---|---|---|---|
| NER lock | `requirements-ner.lock` は推移閉包 | 手作業で列挙したため `huggingface-hub` 等が欠落 | **done**。インストール済みメタデータの `requires()` から推移閉包を実際に計算して再生成（22パッケージ）。Dockerfile は `--no-deps` で導入し、lockに無い依存が暗黙に入らないようにした |
| NER manifest | 「完全マニフェスト」 | 6成果物中3件のみ固定。`config.json`（`id2label` = 検出ラベル体系）と tokenizer 設定が未固定 | **partial**（第6回で是正 → 下表）。6件に増やしたが、追加した3件の digest を**末尾改行を除去した内容から計算**しており、実モデルを拒否する状態だった |
| 文脈分類の網羅 | 6形状を追加 | 1行の shell / SQL / コード文が prose 扱いのまま | **done**。`_SHELL_COMMAND`（既知バイナリ＋パイプ/リダイレクト/フラグの後読み）、`_SQL_STATEMENT`、`_CODE_STATEMENT` を追加。複数行形の後に評価し、既存の分類を変えない |
| 文脈分類の計算量 | 「線形化」 | `_outside_fence` が fence ごとに走査する O(n·m) | **done**。fence開始位置の昇順配列に対する `bisect_right` で判定。セグメント上限も収集中に判定し、上限超過は `SegmentationLimitError` で fail-closed |
| 検出器の実効上限 | セグメント上限＝上限 | セグメント数を絞ってもセグメントあたり全検出器が走る | **撤回**（第6回で再設計 → 下表）。導入した「検出器予算」自体が**回避可能な盲点**だった。予算超過分をNER非対象にしたため、inline code を並べて予算を超えさせれば未登録氏名をNERから隠せた |
| Docker demo stage | ビルド可能 | `chown /app/tests` が存在しないパスを参照し `mock-upstream` のビルドが失敗 | **done**。`/app/devtools` へ修正。`docker compose build mock-upstream` の成功を実機で確認 |
| Codex `-c` override | 正しい設定を生成 | **`http_headers` を JSON オブジェクト記法で生成しており、TOMLとしては構文エラー**（`:` は不正） | **done**。TOML inline table 記法（`{ "k" = "v" }`）へ修正。`-c` は config.toml に重ねられ TOML として解釈されるため、従来の値は Codex に渡した時点で壊れていた |
| テスト配置 | — | `test_audit_round3.py` / `test_audit_round4.py` が監査回ごとの束になっており、責務からテストを探せない | **done**。21件を責務別ファイル（`test_config_fail_closed` / `test_sessions` / `test_leak_gate` / `test_multiturn_session` / `test_responses_stream` / `test_config` / `test_run_guarantee`）へ移設し、監査回ファイルを削除 |

**この回で分かったこと**: 第4次の「実Codex CLIでparse検証済み」という虚偽の検証を撤回し、
実際に検証できることだけを検証するテストへ書き換えた結果、**生成していた `http_headers` が
そもそも妥当な TOML ではない**という実バグが見つかった。検証手順を正直にした副産物であり、
「検証済み」と書いたまま放置していれば発見されなかった。

## 完了検証コマンド（第5回時点）

```bash
ruff check src tests devtools
mypy src
pytest tests/unit tests/evaluation -q
docker compose config --quiet && docker compose build mock-upstream
SM_RUN_LIVE=1 pytest tests/integration/test_live_gateway.py -q   # 明示許可時のみ
```

## 第6回監査の是正（`R17..R24`）

第5回の是正のうち2件は、**是正そのものが誤り**だった。1件は実モデルを拒否するマニフェスト、
もう1件は回避可能な検出予算である。いずれも「テストは通るが製品としては壊れている」型の失敗で、
検証の当て方が原因だった。

### P1

| 項目 | 実際の不具合 | 是正 |
|---|---|---|
| NERマニフェストが実モデルを拒否 | 追加した3つのJSON成果物の digest とサイズが、末尾改行を除いた内容のものだった（1029→1028 等）。`require_verified()` は固定revisionの実ファイルに対して3件とも `ArtifactVerificationError` を返し、**NER有効時の起動と `models fetch` が両方壊れていた** | 実ファイルのbyte列から再生成。**固定snapshotそのものを検査するテスト**を追加（未取得時は skip、決して pass しない）。旧digestに戻すとテストが落ちることを確認済み |
| 文脈分割でNERを回避できた | 検出予算（64）を超えたセグメントはモデル検出器を完全にskip。inline code 40個を挟んで末尾に未登録氏名を置くと、**決定論的に**NER検出ゼロになった | 予算を撤廃し、**モデル検出器はfuzzy対象spanを連結してリクエスト全体で1回**走らせ、offsetを元座標へ写像する方式へ変更（ADR-0011）。コストはセグメント数ではなく散文量に比例するため、同じ文面をどう分割しても検査量は減らない。実測: 5分割でも300分割でもモデル呼び出しは1回 |
| NERが長文の末尾を無言で捨てていた | 512トークン超の入力でpipelineは**例外を出さず**前半だけを分類する。3312文字の合成入力で末尾の氏名は検出ゼロ。監査指摘外だが同種のより広い盲点 | tokenizer offset で重なり付きwindowへ分割し、window毎に推論して重複を除去。実モデルで先頭・中間・末尾すべての氏名を検出することと、全文字がいずれかのwindowに含まれることを確認 |
| 上限超過時の扱い | 「一部だけ検査して成功を返す」= 正常終了と区別できない | `defaults.max_fuzzy_chars` 超過は **fail-closed**（block）。上限が挙動を変える唯一の箇所であり、必ず可視化する |

### P2

| 項目 | 実際 | 是正 |
|---|---|---|
| `AGENTS.md` / `CLAUDE.md` | Codexが作業開始時に読む**現行指示**であるにもかかわらず、製品定義をLiteLLM拡張とし、構成図・ディレクトリ・フェーズ・`uv`（ADR-0002で不採用）を旧状態のまま記載。CLAUDE.md は doc/00 を「正典・矛盾時優先」としており AGENTS.md と逆 | 両ファイルを現行アーキテクチャへ全面改訂。優先順位を**不変条件 ＞ 最新ADR ＞ doc/00 の手段記述**で統一。環境状況も現在の値へ更新 |
| ADR-0010 / ADR-0011 不在 | コードとテストの20箇所が参照する ADR が存在せず、供給網とNER予算の設計判断が**レビュー不能**だった。今回の2件のP1はまさにその2領域 | 両ADRを実際に執筆。ADR-0011 は撤回した予算方式とその失敗理由も記録。**参照されたADRの実在を検査するテスト**を追加 |
| 旧LiteLLM E2Eスクリプト | `scripts/codex_e2e_setup.py` が実行可能なまま、実 `~/.codex/auth.json` の OAuth token を LiteLLM 形式へ複製していた。用途は既に消滅 | 削除（`securitymasker run` が後継）。`docs/compatibility.md` の歴史節に削除理由を記載 |
| Redis 起動手順が誤り | `SECURITYMASKER_STORE=redis docker compose --profile redis up` はRedisを起動するだけで、**Gatewayはmemory storeのまま**。Composeはシェル前置き変数をコンテナ環境へ自動注入しない | overlay 指定へ修正し、誤る理由も明記。`docker compose config` の実出力で両者の差を確認。**手順がoverlayを含むことを検査するテスト**を追加 |
| `run` の保証表現 | 実CLI E2E未実施のまま `GUARANTEED` と表現 | 「検証済み＝生成設定・拒否条件・未起動保証」「未検証＝起動後のプロセスが常に経路を守ること」を明記した表現へ変更（cli.py / launcher.py / operations.md） |
| その他の現行文書 | SECURITY.md が撤廃済み pre-call hook と不存在の `test_live_masking.py` を根拠にしていた。threat-model のスコープ、streaming docstring、pyproject の ruff 例外、`multitenant` 推奨も旧状態 | すべて現行の実体（`test_live_gateway.py`、pre-send 再検査、`tenant_user`）へ更新。Phase 2/3 設計メモと doc/01 には**現行指示ではない旨のバナー**を追加 |

**この回で分かったこと**: 第5回の「manifest完全化」は、**拒否できることだけを検証して受理できることを
検証していなかった**。合成fixtureで作った manifest テストは、正しいモデルまで拒否する manifest を
全件パスさせる。同様に「検出予算」は、コストの上限としては正しくても**攻撃者が分割方法を選べる**という
前提を見落としていた。どちらも「テストが通る」ことと「製品が動く／守れる」ことの差である。

## 第7回: 実CLI E2E の実装（未実施項目の解消）

「CIに実バイナリが無いため未実施」と繰り返し書いていたが、**ローカルには codex 0.145.0 と
claude 2.1.212 の両方が入っており、試していなかっただけだった**。実行し、テスト化した。

`tests/integration/test_real_cli_e2e.py`（opt-in: `SM_RUN_CLI_E2E=1`、4件）:

| 検証 | 結果 |
|---|---|
| 実 `codex exec` を `securitymasker run` 経由で起動 | rc=0。上流に届いた本文は `担当はSM_PERSON_20FB4CC7694Fです。sm-host-b7bfb4487b2e.example.invalid に接続する Python を書いて。` — **原文の氏名・ホスト名は不在、非機密部分は到達**（＝マスクされた結果であって欠落ではない） |
| 実 `claude -p` を同様に起動 | rc=0、`/v1/messages` へ到達、同じくalias化。Codexは`-c`、Claudeは環境変数と**経路が別**なので独立に検証 |
| 実CLIがセッションヘッダを実際に送るか | `X-SecurityMasker-Session-ID` が受信側に到達することを確認 |
| `~/.codex` を書き換えないこと | 隔離した `CODEX_HOME` に `config.toml` が作られないことを確認。実 `~/.codex/config.toml` に生成物の痕跡が0件、mtimeも実行前のままであることを確認済み |

**実provider不接触**: gateway の上流はローカルmock。ネットワークは loopback のみ。

### この E2E が即座に見つけたこと

第6回で修正した `http_headers` の TOML 記法バグを、**修正前の記法で実 codex に与えて再現**した:

```
Error loading config.toml: invalid type: string "{\"X-SecurityMasker-Session-ID\": ...}",
expected a map in `model_providers.securitymasker.http_headers`
```

codex は `-c` の値を TOML として解釈し、失敗すると**生の文字列**として扱う仕様のため、
JSON記法は文字列になり `http_headers` の型検査で弾かれる。**rc=1 で起動せず、リクエストを
1件も送らない。** つまり `securitymasker run codex` は当時**完全に動作していなかった**。
単体テストが全て通っていたにもかかわらず、である。

### なお未検証

- **実provider（OpenAI / Anthropic）側の挙動**。E2Eの上流は意図的にmockであり、
  実providerへの送信は行わない方針を維持する。
- 実CLIの**全サブコマンド・全機能**を網羅したわけではない（`exec` / `-p` の1ターン）。
