# ADR-0014 — 現行製品のGo全面移植を採用しない

- 状態：却下
- 日付：2026-07-28
- 検討対象：Python実装の全面的なGo移植、Go GatewayとPython NERのhybrid構成、
  Goからnative inference runtimeを利用する構成
- 関連：[ADR-0002](0002-pip-venv-over-uv.md)、
  [ADR-0009](0009-japanese-ner-backend.md)、
  [ADR-0010](0010-model-supply-chain.md)、
  [ADR-0011](0011-bounding-model-inference.md)、
  [ADR-0012](0012-renew-package-design.md)

## 背景

現行SecurityMaskerはPython 3.11+で実装したStarlette + httpxの専用proxyであり、
暗号化SQLite、決定論的detector、固定日本語NER、OpenAI Responses／Anthropic Messages
adapter、SSE復元を一process内で構成する。

Goへ移植すれば、Gateway本体については次の改善が期待できる。

- Python runtimeを必要としない単一executable
- HTTP server、stream、並行処理の実装と運用の単純化
- 標準toolchainによるrace detection、fuzzing、cross compilation
- deterministic coreの起動時間、idle memory、dependency surfaceの削減

一方、SecurityMaskerの目的は実装言語の変更ではなく、原文を外部へ送らず、sessionを混ぜず、
構造を壊さず、不明時にfail-closedとすることである。言語移植によってこれらの保証を弱めることは
できない。現行製品では標準日本語NERを既定ONかつsilent downgrade禁止としており、
Python／Transformers／Torchの利用は単なる周辺依存ではなく検出境界の一部になっている。

2026-07-28時点で、現行実装、ADR、release gateを対象にGo移植の障壁を調査した。
Gateway、暗号、deterministic detectorは移植可能だが、製品全体を同等動作のまま置換できるとの
根拠は得られなかった。

## 決定

**現行製品とrelease scopeでは、Python実装のGo全面移植を採用しない。**

次も標準製品構成として採用しない。

- Go GatewayからPython NER subprocess／sidecarを呼ぶhybrid構成
- 標準日本語NERを無効化または機能縮小して成立させるGo版
- 検証済みPython版と異なるtokenizer、offset、aggregationを未評価のまま使うGo版
- 既存config、辞書、暗号化SQLiteを暗黙に非互換化する移植
- Go移植を理由に既存release gateの一部を省略すること

現行の正は引き続きADR-0012、`docs/development/status.md`、Python実装とする。
Goによるisolated technical spikeは許容するが、製品codeへの配線、標準setupへの追加、
通常運用での原文処理は、後述の再検討条件を満たす新ADRなしには行わない。

この決定は「Goでは実装不可能」という判断ではない。期待する効果に対して、同等のsecurity
boundaryを証明するための変更範囲と再認証costが大きく、現行製品を置換する合理性がまだない
という判断である。

## 判断理由

### 1. 標準日本語NERを同等に置換できない

現行NERは、固定した`tsmatz/xlm-roberta-ner-japanese`をTransformersの
token-classification pipelineで実行するだけではない。次を一つの検証済みbackend契約として
扱っている。

- model revisionと全artifactのsize／SHA-256
- safetensors限定、`trust_remote_code`不使用、offline load
- XLM-RoBERTa fast tokenizerの文字offset
- label schemaとoffsetの起動時probe
- `aggregation_strategy="simple"`とscore threshold
- token上限を越えないoverlap付きwindow
- window間のspan重複排除
- code-like contextの除外と曖昧人名のcontext補正
- 固定worker pool、admission limit、timeout後も残るinferenceの追跡

Go標準libraryだけでは、このmodel、tokenizer、aggregationを実行できない。ONNX等へ変換する場合は
新しいmodel artifact、native runtime、tokenizer bindingまたは再実装が必要になり、現行modelと
同じ検出span、score、offsetを返すとは限らない。変換後artifactについてもADR-0010相当の完全な
manifest、load時検証、再配布判断が必要になる。

NERを外すと、ADR-0012が標準保護層として要求した未登録の日本語人名、法人名、地名の検出能力を
失う。binary sizeや移植容易性を理由にこの能力を落とす案は、既に採用した製品方針と両立しない。

### 2. Unicodeとspanの座標系が異なる

Pythonの文字列indexはUnicode code point単位だが、Goの`string` indexはUTF-8 byte単位である。
現行実装はNFKC等で一文字が展開・結合される場合も、正規化後spanを原文のsurface spanへ戻す
offset mapを保持する。次の単位を混同すると、機密値の一部だけを置換する漏えいか、隣接構造を
壊す過剰置換になる。

- request body limitのbyte数
- deterministic detectorとNERの文字数上限
- Goのbyte／rune index
- tokenizerが返すoffset
- combining markを含む正規化前後のspan
- SSE chunkのbyte境界

Unicode正規化libraryを置換するだけでは足りず、現行と同じoffset契約とUnicode version差を
golden testで固定する必要がある。

### 3. Python regexとGo標準regexpは互換ではない

ユーザー辞書のpatternはPython `re`の構文、capture group、matching semanticsを公開契約に
含む。Go標準`regexp`はRE2系でReDoS耐性に利点がある一方、Pythonで有効なlookaround、
backreference等をすべて受理しない。

RE2へ制限する場合はconfig schema変更とmigrationが必要になる。Python互換backtracking
engineを追加する場合は、現行のregex safety lint、入力上限、停止不能なmatchへのtimeoutと
fail-closed制御を別実装で再検証しなければならない。どちらも無変更の移植ではない。

### 4. JSON、SSE、tool callの透過契約を再認証する必要がある

protocol adapterは既知のtext valueだけを変更し、未知field、schema key、ID、type、role、
usageを可能な限り透過する。GoでJSONを`map[string]any`へ単純にdecodeすると、大きな整数の
`float64`化、invalid UTF-8、escape、number表現等でPython版と異なる挙動になり得る。

streaming responseでは、UTF-8文字、SSE line、event、alias、tool argument JSONが任意のbyte
位置で分割される。OpenAIとAnthropicで異なるstate machine、tool callごとのbounded buffer、
JSON完成後の再serialize、trusted local toolだけの復元、stream開始後のerror eventを維持する
必要がある。通常のreverse proxyへの置換では満たせない。

### 5. 既存SQLiteと暗号状態の互換が必要である

既存stateを継続利用するには、SQLite schemaだけでなく次を維持する必要がある。

- master keyによるkey checkとHMAC lookup
- AES-256-GCMのnonce／ciphertext／tag形式
- schema version、database ID、mode、record type、lookup keyを含むAAD
- session JSON codec、base64、timestamp
- response bindingとsessionの連動削除
- idle／absolute TTL、WAL、`synchronous=FULL`、transaction
- key fileとdatabase lock fileのwriter lease

SQLite driverやfile lockの差はOS別に検証が必要である。互換を捨てる場合も、既存sessionを
黙って失効させず、専用migrationまたは明示的な非互換releaseが必要になる。

### 6. config、CLI、file securityも公開契約である

Pydantic／PyYAMLは、strict field、enum、range、duration、相対path、辞書ID重複、
`value_from_env`、key length、危険なfile permissionを起動前に検証する。GoのYAML／validation
libraryは同じ入力を同じように受理・拒否するとは限らない。errorへ入力値やsecretを再表示しない
性質も含めて移植対象となる。

`init`、`config-check`、`entities`、`preview`、`client-config`、`doctor`、`gateway`、
`model-load`のoption、exit code、stdout／stderrも利用者向け契約である。

### 7. Go化だけではone-fileの主要costを解決しない

現行one-fileの容量と起動costの大部分は、標準NERのmodel weightとinference runtimeに由来する。
GatewayをGoへ移してもmodel weightは消えない。ONNX等のnative runtimeを使えばcgo、native
library、OS／architecture別build、署名、load pathが必要となり、Goの容易なcross compilationと
小さい単一binaryという利点をそのまま得られない。

modelをbinaryへembedする場合は安全な一時展開、permission、cleanup、tamper検証が必要になり、
adjacent artifactとして配布する場合はADR-0012のone-file契約を変更する。

### 8. 同等性の証明costが実装costを上回る

現行release gateは、固定NER必須unit／evaluation 586件、mock Gateway、実Codex CLI／
Claude Code CLI、binary E2E、外向きnetworkのない環境での漏えいゼロを要求する。

移植では既存testを単にGo syntaxへ書き換えるのではなく、少なくとも次をPython版との
differential testで比較する必要がある。

- detection span、priority、overlap解決、alias shape
- 正規化と構造保持
- aliasの全stream分割位置
- tool argument deltaと特殊文字
- session並行性、response binding、TTL
- configのaccept／reject matrix
- DB/key/mode/tamper
- clean inputの受理
- 最終upstream payloadの原文ゼロ
- NERのprecision／recall、score、resource上限

現行実装を保守しながら同じsecurity boundaryを別言語で再構築・比較する期間が必要であり、
現在確認できる利用者価値に対して優先度が低い。

## 検討した代替案

### A. Pythonを完全に廃止した純Go実装

Gateway、deterministic detector、alias、cryptoは実装可能である。しかし標準NERのmodel実行、
tokenizer、offset、aggregationまで純Goで同等にする成熟した経路を確認できず、独自ML runtimeに
近い開発範囲になるため却下する。

### B. Go + ONNX／native inference runtime

Python runtimeを除去できる可能性が最も高い案である。一方、model exportによる数値差、Go向け
公式bindingがないruntimeの採用、tokenizer、cgo、native artifact、OS別署名と配布が新しい
security／supply-chain boundaryになる。isolated spikeの候補としては残すが、製品移植案としては
現時点で却下する。

### C. Go Gateway + Python NER sidecar

HTTP／streaming層だけをGoへ移し、検証済みNERをPython subprocessへ残す案である。
model挙動を維持しやすい一方、原文をprocess間で渡すIPC、認証、permission、message framing、
size limit、timeout、crash recovery、network無効化、version skewを新たに管理する必要がある。
Python runtimeとmodel配布costも残り、単一processというADR-0012の単純な運用契約を失うため
却下する。

### D. NERを無効化した小さいGo版

実装・配布は最も容易になるが、未登録の日本語固有表現を標準で保護するというADR-0009／0012の
決定を撤回する。機能低下を実装言語変更の便益と交換できないため却下する。

### E. Gatewayだけを段階的にGoへ置き換える

protocol adapterとmasking coreを分離する設計には合うが、二つのproduction runtime、
cross-language interface、二重のrelease gateを長期間保守することになる。測定済みの性能問題や
現行Gatewayで解決不能な互換問題がなく、移行中の複雑性に見合わないため現行scopeでは却下する。

### F. Python実装を維持する

既存の検証済みpipeline、source release candidate、one-file spike、固定dependencyを維持できる。
binary公開の未解決事項は残るが、それらはGo化だけでは解消しない。現時点ではこの案を継続する。

## 影響

- `python3 securitymasker.py`とPyInstaller one-fileを、引き続き同等の利用経路とする。
- pip + venv、Pydantic、Starlette、httpx、Transformers、Torchの現行固定dependencyを維持する。
- Go module、Go toolchain、ONNX Runtime、tokenizer bindingを標準setupへ追加しない。
- 現行config v2、dictionary、SQLite schema、CLI、client snippetを変更しない。
- Go移植を理由にbinary公開blockerが解消したとは扱わない。
- Goの利点として挙げたrace detectionやfuzzingは、必要に応じてPython側の並行test、
  property test、process isolationを強化する動機として扱う。

## 再検討条件

次をすべて満たす具体的なproposalがある場合だけ、本ADRを置き換える新ADRで再検討する。

1. 固定NER modelをofflineでloadし、remote codeやpickle weightを使わないGoからの実行経路。
2. model、tokenizer、native runtimeを含む完全なartifact manifestと再配布方針。
3. Python版との合成corpus比較で、entity span、label、score threshold、offset、window境界に
   security上のregressionがないこと。
4. Unicode正規化、regex、JSON、SSE、tool argumentのcross-language golden test。
5. 既存config／dictionary／SQLiteを互換利用する実証、またはfail-closedなatomic migration設計。
6. session並行性、response binding、tamper、resource上限を含む全release gateのGo版。
7. 対象OS／architectureごとのclean build、署名、offline clean-machine E2E。
8. binary size、cold start、memory、保守costなど、現行実測を上回る利用者価値の測定。

一部だけ満たしたprototypeは技術調査として保持できるが、標準製品への採用根拠にはしない。
