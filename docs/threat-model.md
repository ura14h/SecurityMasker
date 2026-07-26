# 脅威モデル

対象範囲は、元の機密情報を信頼領域内に留め、payload を壊さず、Codex／Claude Code
および OpenAI／Anthropic の wire protocol 更新に対して保守可能であり続けることです
（`doc/00-First-Order.md` §5、§33）。

## 信頼境界

- **信頼領域**：Codex／Claude Code を実行するローカルマシン、SecurityMasker
  Gateway、session store、明示的に信頼した local tool。
- **非信頼領域**：OpenAI／Anthropic その他の外部 LLM、外部 telemetry／log
  service、外部 MCP server、provider 側の hosted tool。

元の機密情報は非信頼領域へ一切出してはなりません。外部 MCP に対する復元は
既定で無効です（§5）。

## 脅威と緩和策

| 脅威 | 緩和策 |
|---|---|
| 元の機密情報が外部 LLM へ送られる | 呼び出し前のマスク、各テキストの再検査、dict key・未知フィールド・構造フィールドを含む全文字列に対する最終 payload-wide block-only guard、fail-closed（§18、§26、doc/06 P0-4）、live leakage test |
| セッション／テナント間の漏えい | セッションごとの HMAC 鍵、そのセッションが発行した alias だけを復元、テナント名前空間と master key で保護した Redis（§7、§8） |
| provider がセッションをまたいで利用者を相関する | 同じ秘密でもセッションごとに独立した alias を生成（§6） |
| モデルが alias を変形する（大文字小文字、分割、翻訳） | そのセッションが発行した完全一致の alias だけを復元し、近似復元は行わない（§24、doc/06 P0-7） |
| client が proxy を完全に迂回する | `securitymasker run` は `/ready` が ready であり、対象 tool を routing できる場合だけ起動する。provider 直通の環境変数は拒否（doc/06 P2-1） |
| 同一テナントの2利用者が alias table を共有する | `tenant_user` mode で tenant+user を一体として署名し、store key と response binding を両方で名前空間化し、読み取り時にも identity を照合（ADR-0008） |
| fuzzy NER が code を破壊する、または code 内の秘密を見落とす | body を segment 化し、code span を除外するのは fuzzy detector だけとする。dictionary と deterministic detector は全 context で実行（§17） |
| 固定されていない model／base image が変化する | model revision と file digest を固定し fetch 時に検証。base image は digest で固定し、固定解除を test で検出（ADR-0009、doc/06 P2-3） |
| ユーザー定義 regex の catastrophic backtracking | 既知の危険形を config load 時に拒否し、detector ごとの timeout では待ち続けず block（doc/06 P1-5） |
| 外部／MCP tool に実値を復元する | tool arguments は既定で非信頼。allowlist 登録済み local tool だけ実値を受け取り、それ以外には alias を渡す（doc/06 P0-8） |
| モデル出力による prompt injection | 出力は data として扱い、alias→原文の置換だけを行い、構造を再検証（§19） |
| alias collision | 衝突を検出して token を延長し、空間枯渇時は例外（§7） |
| log／error／telemetry への漏えい | 安全な field だけを記録し、error に秘密を含めず、verbose logging を無効化（§25） |
| cache／Redis／memory の開示 | AES-GCM で暗号化し、鍵を Redis に置かない。local／trusted network で運用し、swap と core dump を制限（§8、§33） |
| 巨大入力による DoS | scan に上限を設け、ほぼ線形の pipeline（clustered overlap resolution、重複除去済み leak scan）と size cap を使用（§32） |
| regex の catastrophic backtracking | anchor／上限付き pattern と scan length cap（§32） |
| 危険な shell command に秘密を復元する | client の tool approval を迂回せず、秘密情報には `env_reference` を優先（§27） |
| 構造破壊（JSON／code／patch） | value だけを変換し、tool JSON を再 serialize し、構造 key は変更しない（§16） |
| Codex／Claude Code が未知の field／event を追加する | 未知 field／event は、登録済み・高確度の秘密が残っていないことを最終 block-only guard が確認した後だけ透過（§22、§23、doc/06 P0-4） |

## 残存リスク

Python ではメモリの完全消去を保証できません。未登録の日本語氏名・住所を100%の
再現率で検出することもできず、heuristic recognizer には false negative が残ります。
[SECURITY.md](../SECURITY.md) の堅牢化を適用した信頼済み network で実行してください。
