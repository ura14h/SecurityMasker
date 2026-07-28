# ADR-0014 — 現行製品のPython以外への全面移植を採用しない

- 状態：却下
- 日付：2026-07-28
- 検討対象：Rust、Go、Node.js／TypeScript、C++、.NET、JVMへの全面移植、
  native inference runtimeを利用する構成、Python NERを残すhybrid構成
- 採用：現行製品ではPython実装を維持する
- 関連：[ADR-0002](0002-pip-venv-over-uv.md)、
  [ADR-0009](0009-japanese-ner-backend.md)、
  [ADR-0010](0010-model-supply-chain.md)、
  [ADR-0011](0011-bounding-model-inference.md)、
  [ADR-0012](0012-renew-package-design.md)

## 背景

現行SecurityMaskerはPython 3.11+で実装したStarlette + httpxの専用proxyであり、
暗号化SQLite、決定論的detector、固定日本語NER、OpenAI Responses／Anthropic Messages
adapter、SSE復元を一process内で構成する。

Python以外へ移植すれば、選択する言語とruntimeによって次の改善が期待できる。

- Python runtimeを必要としない配布物
- 起動時間、idle memory、dependency surfaceの削減
- 静的型付け、所有権、memory safety、race detection等による欠陥の早期検出
- HTTP server、stream、並行処理、cross compilationの実装・運用上の利点
- 一部の環境での単一executable化や署名・配布の単純化

一方、SecurityMaskerの目的は実装言語の変更ではなく、原文を外部へ送らず、sessionを混ぜず、
構造を壊さず、不明時にfail-closedとすることである。言語移植によってこれらの保証を弱めることは
できない。現行製品では標準日本語NERを既定ONかつsilent downgrade禁止としており、
Python／Transformers／Torchの利用は単なる周辺依存ではなく検出境界の一部になっている。

2026-07-28時点で、現行実装、ADR、release gateを基準に、Goに限定せず主要な非Python候補を
比較した。Gateway、暗号、deterministic detectorを別言語へ移植すること自体は可能である。
しかし、製品全体を同等のsecurity boundary、protocol互換性、NER検出能力のまま置換し、
移植費用を上回る利用者価値を得られるとの根拠は、どの候補についても得られなかった。

ここでいう「最良」は一般論としての言語の優劣ではない。現行製品、現在のmodel、既存利用者の
state、release gateを前提に、残存riskと総保守costが最小であることを意味する。

## 決定

**現行製品とrelease scopeでは、Python以外への全面移植を採用しない。Python実装を維持する。**

Rustは非Python候補の中で最も有望であり、将来の全面再設計では最初に評価する候補とする。
ただし、現行製品の置換については、他候補と同じく却下する。

次も標準製品構成として採用しない。

- 別言語のGatewayからPython NER subprocess／sidecarを呼ぶhybrid構成
- 標準日本語NERを無効化または機能縮小して成立させる別言語版
- 検証済みPython版と異なるtokenizer、offset、aggregationを未評価のまま使う実装
- 既存config、辞書、暗号化SQLiteを暗黙に非互換化する移植
- 言語移植を理由に既存release gateの一部を省略すること

現行の正は引き続きADR-0012、`docs/development/status.md`、Python実装とする。
候補言語またはinference backendのisolated technical spikeは許容するが、製品codeへの配線、
標準setupへの追加、通常運用での原文処理は、後述の再検討条件を満たす新ADRなしには行わない。

この決定は「Python以外では実装不可能」という判断ではない。期待する効果に対して、同等の
security boundaryを証明するための変更範囲と再認証costが大きく、現行製品を置換する合理性が
まだないという判断である。

## 評価基準

候補は次の順序で評価する。下位の利点によって上位の不足を相殺しない。

1. 不変条件とfail-closed性を同等以上に維持できること。
2. 固定日本語NERのspan、label、score、offset、window境界を同等にできること。
3. protocol、stream、config、辞書、暗号化stateの互換性を維持できること。
4. modelを含む供給網を固定し、offlineで再現・検証できること。
5. 対象OS／architectureでbuild、署名、配布、clean-machine E2Eを完結できること。
6. 全release gateを再認証する実装・保守costに見合う、測定済みの利用者価値があること。

比較の要点は次のとおりである。

| 候補 | NER実行経路 | 主な利点 | 現行製品での主な障壁 | 判断 |
| --- | --- | --- | --- | --- |
| Python | Transformers + Torchの現行経路 | 検証済み挙動、最小変更 | 配布容量、cold start | 維持 |
| Rust | tokenizers／safetensors、CandleまたはONNX | memory safety、小さいnative core、将来性 | 現行XLM-R token classification契約の再構築と再認証 | 現行移植は却下、将来候補 |
| Go | ONNX等のnative runtimeまたは独自binding | Gateway、並行処理、配布toolchain | 公式の一貫したNER経路、tokenizer、cgo、regex互換 | 却下 |
| Node.js／TypeScript | Transformers.js + ONNX | token-classification API、開発容易性 | model変換、UTF-16 offset、native runtimeを含む配布 | 却下 |
| C++ | LibTorchまたはONNX Runtime | inference runtimeの公式API、性能制御 | tokenizer／pipeline実装、memory safety、build matrix | 却下 |
| .NET | ONNX Runtime + tokenizer library | managed runtime、型、single-file機能 | model変換、offset同等性、native library展開、OS別配布 | 却下 |
| JVM | ONNX Runtime Java等 | managed runtime、成熟したlibrary群 | tokenizer／pipeline、runtime image、platform別package | 却下 |

## 共通の判断理由

### 1. 標準日本語NERはmodel実行以上の検証済み契約である

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

非Python runtimeで重みを読み込めることだけでは、この契約を満たさない。ONNX等へ変換する場合は
新しいmodel artifact、native runtime、tokenizer bindingまたは再実装が必要になり、現行modelと
同じ検出span、score、offsetを返すとは限らない。変換後artifactについてもADR-0010相当の完全な
manifest、load時検証、再配布判断が必要になる。

NERを外すと、ADR-0012が標準保護層として要求した未登録の日本語人名、法人名、地名の検出能力を
失う。binary sizeや移植容易性を理由にこの能力を落とす案は、既に採用した製品方針と両立しない。

### 2. Unicodeとspanの座標系が言語・runtimeごとに異なる

Pythonの文字列indexはUnicode code point単位である。GoとRustの一般的な文字列sliceはUTF-8
byte境界を扱い、JavaScript、.NET、JVMの文字列indexはUTF-16 code unitを基礎とする。
C++では採用する文字列libraryと規約を製品側で固定しなければならない。

現行実装はNFKC等で一文字が展開・結合される場合も、正規化後spanを原文のsurface spanへ戻す
offset mapを保持する。次の単位を混同すると、機密値の一部だけを置換する漏えいか、隣接構造を
壊す過剰置換になる。

- request body limitのbyte数
- deterministic detectorとNERの文字数上限
- 候補runtimeのbyte／code point／code unit index
- tokenizerが返すoffset
- combining markを含む正規化前後のspan
- SSE chunkのbyte境界

Unicode正規化libraryを置換するだけでは足りず、現行と同じoffset契約とUnicode version差を
golden testで固定する必要がある。

### 3. regexはconfig互換性と停止可能性の両方を満たす必要がある

ユーザー辞書のpatternはPython `re`の構文、capture group、matching semanticsを公開契約に
含む。Go標準`regexp`とRustで一般的な`regex` crateは、ReDoS耐性に利点がある一方、
Pythonで有効なlookaround、backreference等をすべて受理しない。JavaScript、.NET、JVM、C++の
engineも、構文、Unicode、backtracking、timeoutの挙動がPython `re`と同一ではない。

より制限されたengineへ移す場合はconfig schema変更とmigrationが必要になる。
Python互換に近いbacktracking engineを採用する場合は、現行のregex safety lint、入力上限、
停止不能なmatchへのtimeoutとfail-closed制御を別実装で再検証しなければならない。どちらも
無変更の移植ではない。

### 4. JSON、SSE、tool callの透過契約を再認証する必要がある

protocol adapterは既知のtext valueだけを変更し、未知field、schema key、ID、type、role、
usageを可能な限り透過する。各runtimeの標準JSON表現は、number精度、duplicate key、
invalid Unicode、escape、object順序、再serializeの細部が異なる。

例えばGoでJSONを`map[string]any`へ単純にdecodeすると大きな整数が`float64`化し、
JavaScriptの`number`にも整数精度の上限がある。型付きmodelで未知fieldを落とす実装も、
「未知fieldはleak guard後だけ可能な限り透過する」という不変条件に反する。

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

SQLite driver、AES-GCM API、timestamp codec、file lockの差はOS別に検証が必要である。
互換を捨てる場合も、既存sessionを黙って失効させず、専用migrationまたは明示的な非互換releaseが
必要になる。

### 6. config、CLI、file securityも公開契約である

Pydantic／PyYAMLは、strict field、enum、range、duration、相対path、辞書ID重複、
`value_from_env`、key length、危険なfile permissionを起動前に検証する。候補言語の
YAML／validation libraryは同じ入力を同じように受理・拒否するとは限らない。errorへ入力値や
secretを再表示しない性質も含めて移植対象となる。

`init`、`config-check`、`entities`、`preview`、`client-config`、`doctor`、`gateway`、
`model-load`のoption、exit code、stdout／stderrも利用者向け契約である。

### 7. 言語移植だけではone-fileの主要costを解決しない

現行one-fileの容量と起動costの大部分は、標準NERのmodel weightとinference runtimeに由来する。
Gatewayを別言語へ移してもmodel weightは消えない。ONNX等のnative runtimeを使えばnative
library、OS／architecture別build、署名、load pathが必要となる。C ABI、cgo、FFI、native addon
等を使う候補では、言語単体の容易な配布という利点をそのまま得られない。

modelをbinaryへembedする場合は安全な一時展開、permission、cleanup、tamper検証が必要になり、
adjacent artifactとして配布する場合はADR-0012のone-file契約を変更する。

したがって、binary容量やcold startを改善する目的なら、最初にPythonのままinference backend
だけを隔離して比較する方が、言語移植の効果とmodel／runtime変更の効果を区別できる。

### 8. 同等性の証明costが実装costを上回る

現行release gateは、固定NER必須unit／evaluation 586件、mock Gateway、実Codex CLI／
Claude Code CLI、binary E2E、外向きnetworkのない環境での漏えいゼロを要求する。

移植では既存testを別言語のsyntaxへ書き換えるだけでなく、少なくとも次をPython版との
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

## 候補別の検討

### A. Rust

Rustは非Python候補の中で最も有望である。Hugging Face TokenizersはRust実装を中核とし、
safetensorsにもnative Rust APIがある。CandleはRust-nativeのML frameworkで、Pythonを
必須とせずsafetensorsとTransformer系modelを扱える。memory safety、所有権、明示的な並行性は、
機密情報とsession stateを扱うcoreにも適している。

しかし、必要なのはXLM-RoBERTaの重みをloadできることではない。現行modelについて、
token-classification head、tokenizer設定、special token、offset、`simple` aggregation、
score threshold、448／64のwindowing、span重複排除までを一つのbackendとして再構築し、
Python版との差を説明する必要がある。Candle等に現行Transformers pipelineと交換可能な完成済み
経路があることは確認できず、ONNXを使う場合はmodel変換とnative runtimeの問題が残る。

現行製品の移植先としては却下する。ただし、全面再設計する次世代版では第一候補とし、
合成corpusによるNER differential spikeを最初の判定gateにする。

### B. Go

Gateway、deterministic detector、alias、cryptoは移植可能である。HTTP、stream、並行処理、
race detector、fuzzing、cross compilationには明確な利点がある。

一方、Go標準libraryだけでは現行model、tokenizer、aggregationを実行できない。純Goで同等に
する成熟した経路を確認できず、独自ML runtimeに近い開発範囲になる。ONNX等を使う場合も、
model exportによる数値差、Go向け公式bindingがないruntimeの採用、tokenizer、cgo、native
artifact、OS別署名と配布が新しいsecurity／supply-chain boundaryになる。

さらに、UTF-8 byte／rune offset、Python `re`とRE2の非互換、`map[string]any`のJSON number
変換が既存契約に直接影響する。純Go版、Go + ONNX版のいずれも現行製品では却下する。

### C. Node.js／TypeScript

Transformers.jsにはtoken-classification／NER pipelineがあり、Web／Node.jsから利用できる。
ただし実行対象はONNX weightであり、現行safetensors版のmodel artifactとPython Transformersの
実行経路をそのまま利用するものではない。model export、runtime version、quantization有無、
tokenizerとaggregationの差を新しいbackendとして固定・評価する必要がある。

JavaScriptの文字列offsetはUTF-16 code unit、標準numberはIEEE 754倍精度であり、spanとJSON
透過には明示的な変換規約が必要になる。native ONNX runtimeを含むNode配布物は、JavaScriptだけの
単純なsingle executableにはならない。TypeScriptの型はruntime validationや秘密の消去を保証せず、
event loop上でCPU-bound inferenceとstreamingを同居させるresource制御も再設計になる。

現行Python版を置換する安全性・配布上の純利益が確認できないため却下する。

### D. C++

PyTorchはLibTorchとしてC++ frontendを提供し、ONNX Runtimeにも公式C／C++ APIがあるため、
inference engineへの到達性は高い。性能、memory layout、threading、native配布を細かく制御できる。

一方、Transformersのtokenizer、pipeline、aggregation、offset契約は別途構築する必要がある。
さらに、request body、復元前の秘密、alias対応表、SSE bufferを扱う境界で、use-after-free、
buffer overrun、data race等のmemory safety riskを新たに負う。sanitizer、fuzzing、RAIIを徹底しても、
現行Python実装からの全面移植はsecurity boundaryの単純化にならない。

高い実装・監査costに見合う測定済み要件がないため却下する。

### E. .NET

ONNX Runtimeには公式C# APIがあり、`Microsoft.ML.Tokenizers`にはSentencePiece tokenizerが
ある。managed runtime、型、async I/O、単一file配置機能はGateway実装の候補になり得る。

ただし、一般的なSentencePiece対応だけでは現行XLM-R fast tokenizerとoffset契約の同等性を
証明できない。現行modelはONNXへの変換と再評価が必要である。.NET single-fileはOS／architecture
固有で、native libraryを含む場合は自己展開が必要になることがあり、model artifactも残る。
UTF-16 offset、config／regex semantics、既存暗号stateも再認証対象である。

現行製品を置換する決定的な利点がないため却下する。

### F. JVM

ONNX RuntimeにはJava APIがあり、HTTP、SQLite、暗号、並行処理の成熟したlibraryも存在する。
一方、現行XLM-R tokenizer／pipeline／offsetを一貫して置換する経路は別途選定・実装が必要である。

`jpackage`はplatform別のapplication imageまたはnative packageを生成する仕組みであり、
modelとruntimeを含む小さな単一executableを自動的に得るものではない。JVM runtime image、
native inference library、model artifactを含む配布・署名matrixは残る。UTF-16 offsetと
Java regex／JSONの互換性も再認証対象になる。

現行製品では配布とsecurity boundaryを単純化しないため却下する。

### G. 別言語Gateway + Python NER sidecar

HTTP／streaming層だけを別言語へ移し、検証済みNERをPython subprocessへ残す案である。
model挙動を維持しやすい一方、原文をprocess間で渡すIPC、認証、permission、message framing、
size limit、timeout、crash recovery、network無効化、version skewを新たに管理する必要がある。
Python runtimeとmodel配布costも残り、単一processというADR-0012の単純な運用契約を失うため
却下する。

### H. NERを無効化した小さい別言語版

実装・配布は最も容易になるが、未登録の日本語固有表現を標準で保護するというADR-0009／0012の
決定を撤回する。機能低下を実装言語変更の便益と交換できないため却下する。

### I. Gatewayだけを段階的に別言語へ置き換える

protocol adapterとmasking coreを分離する設計には合うが、二つのproduction runtime、
cross-language interface、二重のrelease gateを長期間保守することになる。測定済みの性能問題や
現行Gatewayで解決不能な互換問題がなく、移行中の複雑性に見合わないため現行scopeでは却下する。

### J. Python実装を維持する

既存の検証済みpipeline、source release candidate、one-file spike、固定dependencyを維持できる。
binary公開の未解決事項は残るが、それらは言語移植だけでは解消しない。現在の制約に対する
残存risk、変更量、再認証costが最も小さいため、この案を継続する。

## 影響

- `python3 securitymasker.py`とPyInstaller one-fileを、引き続き同等の利用経路とする。
- pip + venv、Pydantic、Starlette、httpx、Transformers、Torchの現行固定dependencyを維持する。
- Rust／Cargo、Go、Node.js／npm、C++ toolchain、.NET、JVM、ONNX Runtime等を標準setupへ
  追加しない。
- 現行config v2、dictionary、SQLite schema、CLI、client snippetを変更しない。
- 言語移植を理由にbinary公開blockerが解消したとは扱わない。
- binary容量やcold startを改善する調査では、言語移植より先に、Python API境界を維持した
  inference backendのisolated spikeを検討する。
- Rustのmemory safety、Goのrace detection、各toolchainのfuzzing等の利点は、Python側の
  型検査、並行test、property test、process isolationを強化する動機として扱う。

## 再検討条件

次をすべて満たす具体的なproposalがある場合だけ、本ADRを置き換える新ADRで再検討する。

1. 固定NER modelをofflineでloadし、remote codeやpickle weightを使わない候補言語からの
   実行経路。
2. model、tokenizer、native runtimeを含む完全なartifact manifestと再配布方針。
3. Python版との合成corpus比較で、entity span、label、score threshold、offset、window境界に
   security上のregressionがないこと。
4. Unicode正規化、regex、JSON、SSE、tool argumentのcross-language golden test。
5. 既存config／dictionary／SQLiteを互換利用する実証、またはfail-closedなatomic migration設計。
6. session並行性、response binding、tamper、resource上限を含む全release gateの移植版。
7. 対象OS／architectureごとのclean build、署名、offline clean-machine E2E。
8. binary size、cold start、memory、保守costなど、現行実測を上回る利用者価値の測定。
9. 移行期間中の二重実装、security fixの同期、rollback、旧stateの扱いを含む保守計画。

一部だけ満たしたprototypeは技術調査として保持できるが、標準製品への採用根拠にはしない。
全面再設計を開始する場合は、まずRustで条件1から4を検証し、不成立ならGateway実装へ進まない。

## 調査根拠

- [Hugging Face Tokenizers](https://github.com/huggingface/tokenizers)：
  Rust実装、各言語binding、offset alignmentの基盤。
- [Hugging Face safetensors](https://github.com/huggingface/safetensors)：
  Rust／Python APIと安全なtensor形式。
- [Hugging Face Candle](https://github.com/huggingface/candle)：
  Rust-native ML frameworkとsafetensors／Transformer系modelの実行候補。
- [Transformers.js token classification](https://huggingface.co/docs/transformers.js/pipelines)：
  JavaScriptから利用できるNER pipelineとONNX weight要件。
- [ONNX Runtime API](https://onnxruntime.ai/docs/api/)：
  公式およびcommunityの言語binding範囲。
- [PyTorch C++ frontend](https://docs.pytorch.org/cppdocs/frontend)：
  LibTorchによるC++ inference経路。
- [.NET SentencePiece tokenizer](https://learn.microsoft.com/dotnet/api/microsoft.ml.tokenizers.sentencepiecetokenizer)：
  .NETで利用できるSentencePiece tokenizer API。
- [.NET single-file deployment](https://learn.microsoft.com/dotnet/core/deploying/single-file/overview)：
  platform固有配布とnative library自己展開の制約。
- [JDK `jpackage`](https://docs.oracle.com/en/java/javase/26/docs/specs/man/jpackage.html)：
  platform別application image／packageの性質。

これらは各言語での実装可能性を示す資料であり、SecurityMaskerの現行NER契約との同等性を
保証するものではない。同等性は上記の再検討条件に従って製品固有に実証する。
