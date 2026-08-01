# ADR-0020 — binaryをLite版とFull版へ分離する

- 状態：採用
- 日付：2026-08-01
- 関連：ADR-0009（日本語NER）、ADR-0010（model supply chain）、ADR-0012、ADR-0017

## 背景

従来のPyInstaller one-file buildは、Python runtime、runtime dependency、標準日本語NER modelの
weightとtokenizerを一つの実行ファイルへ同梱していました。この構成は外部networkなしで直ちに
起動できますが、次の問題があります。

1. 約1.1 GBのmodel artifactをSecurityMaskerのartifactとして再配布するため、model作者が表示する
   MIT licenseだけでなく、CC BY-SA 3.0の学習datasetから生成したweightに追加条件が及ぶかを
   公開前に確認する必要がある。
2. SecurityMaskerの変更だけでもmodelを毎回再梱包し、one-file起動時に一時directoryへ展開する。
3. modelの更新とSecurityMasker executableの更新を独立して扱えない。
4. modelを同梱しないsource setupには、固定revisionを明示的に取得し、全artifactのsizeとSHA-256を
   検証する`securitymasker model-load`が既にあるが、従来のfrozen runtimeは同梱modelだけを探索し、
   利用者のlocal cacheを使用できない。

binaryを公開しない私的buildでも、通常運用ではexecutableと検証済みmodel cacheを分離した方が、
build、更新、cache復旧を管理しやすい。一方、air-gapped端末へ単一artifactを搬入する用途では、
model同梱にも明確な価値があります。

## 決定

共通のbinary build基盤に、利用者向け名称が明確な二つのprofileを設けます。

| profile | modelの配置 | 初回network | 主な用途 |
|---|---|---|---|
| `lite` | 利用者のlocal Hugging Face cache | `model-load`時だけ必要 | 通常運用、将来の公開候補 |
| `full` | PyInstaller one-fileへ固定modelを同梱 | build時だけ必要 | air-gapped環境へ私的搬入 |

`Lite`は機能制限版ではありません。Python runtime、PyTorch、Transformers、detector、Gatewayは
Full版と同一で、標準NER model weightを実行ファイルの外へ置く点だけが異なります。このため文書では
「モデル非同梱版」と必ず補足します。

buildの正は次の共通interfaceです。

```console
./scripts/build-binary --profile lite
./scripts/build-binary --profile full
```

短いwrapperを用意しても、profile固有のbuild処理を複製しません。成果物名は
`securitymasker-lite`と`securitymasker-full`に分け、profile不明の`securitymasker`を生成しません。

## Runtime model探索

runtimeは次の順序でmodelを解決します。

1. 実行中のone-fileに固定modelが同梱されていれば、そのdirectoryを使用する。
2. 同梱modelがなければ、固定model IDとrevisionに対応する利用者のlocal cacheを使用する。
3. どちらも存在しなければ、上流へrequestを送らず、`securitymasker model-load`を案内して
   fail-closedで拒否する。

どの配置でも、load前にADR-0010の完全manifest、size、SHA-256、拒否weight形式を同じcodeで再検査
します。Lite版がprompt処理を契機にdownloadすること、未検証modelへ黙ってdowngradeすること、
modelなしでNERを無効化して成功することは禁止します。

`model-load`は利用者が明示的に実行する準備commandのままとし、自動downloadやfirst-request download
へ変更しません。取得元、固定revision、manifestもsource版と共有します。

## Build metadata

one-fileへsecretを含まないbuild metadataを埋め込み、`--version`から次を識別可能にします。

- distribution: `source`または`binary`
- binary profile: `lite`または`full`

metadataの欠落や未知profileを既知profileとして推測しません。成果物名、表示metadata、指定profileが
一致することをbinary gateで検査します。

## Profile別gate

Lite版はclean環境で次を検証します。

1. model cacheなしでは`preview`とGateway起動を安全に拒否する。
2. binary自身の`model-load`が固定artifactを取得し、完全manifestを検証する。
3. 取得後は外部networkなしで`preview`、両mode Gateway E2E、停止、再起動を完了する。
4. model cacheをartifactへ混入させていないことを確認する。

Full版はclean環境で次を検証します。

1. local model cacheと外部networkなしで、同梱modelを検証する。
2. `preview`、両mode Gateway E2E、停止、再起動を完了する。
3. 同梱modelの全artifactがbuild時とruntimeの両方でmanifestに合格する。

両profileとも対象OS／architectureでnative buildし、Pythonのないruntime、read-only root filesystem、
一時展開directoryのcleanup、version、checksum、source tagとの対応を検証します。

## 公開判断

Lite版はSecurityMaskerのrelease artifactへmodel weightを含めません。これによりweight同梱再配布の
blockerをLite版から分離できます。ただし、利用者が取得するmodel、base model、学習datasetの出典と
licenseは引き続き表示します。PyTorch等のruntime dependencyはbinaryへ同梱するため、transitive
componentのlicense inventory、NOTICE、署名、notarization、対象platform別gateはLite版にも必要です。

Full版はmodel weightを再配布するため、model作者・dataset権利者への確認または適切な法務確認が
終わるまで外部公開しません。私的buildが技術的に成功することを公開許可とは扱いません。

## 検討した代替案

- **Full版だけを維持する**：offline搬入は容易ですが、通常運用まで再配布判断と巨大artifactへ
  結合するため不採用。
- **Lite版だけにする**：公開境界は単純ですが、完全air-gapped搬入という測定済み用途を失うため
  不採用。
- **modelを別のSecurityMasker Release artifactとして配布する**：別fileでもSecurityMaskerが
  weightを再配布する事実は変わらず、主要blockerを解消しないため不採用。
- **初回requestで自動downloadする**：user textを処理するsecurity boundaryの挙動をnetwork取得へ
  依存させ、失敗時の意味も曖昧にするため不採用。
- **Lite／Fullを別々のbuild scriptへ複製する**：toolchainやhidden importがdriftするため不採用。

## 影響

- binary build、test、文書、artifact名はprofileを明示する。
- Lite版の初回準備にはnetwork接続、約1.1 GBのdownload、永続model cacheが必要になる。
- Full版は従来どおり大きく、one-file起動時の一時展開costを持つ。
- binary公開全体のblockを、Lite版に残るdependency再配布・署名・native gateと、Full版だけに残る
  model weight再配布へ分解して追跡できる。
