# Threat model

## 信頼境界

信頼領域は、利用者のローカルPC、SecurityMasker Gateway、config/dictionary/state/key、
明示的に信頼したlocal toolです。

非信頼領域は、ChatGPT/OpenAI、Anthropic、その他の外部LLM、外部telemetry/log、外部MCP、
provider側hosted toolです。元の機密情報は非信頼領域へ出してはいけません。

## 対象とする脅威

| 脅威 | 主な緩和策 |
|---|---|
| 原文が外部LLMへ送られる | request mask、全文字列の最終leak guard、fail-closed、mock leakage test |
| 添付file/image内の原文が未検査で送られる | protocol-native添付、file ID/URL、provider file searchを転送前に一律block |
| session間で対応表が混ざる | session固有鍵、response binding、別mode/別DB、同一DBのsingle-writer lease |
| DBだけが漏れる | keyed lookup、session blobのAES-256-GCM、master keyをDB外へ分離 |
| DB改竄・wrong key | metadata key checkとAAD認証、起動拒否 |
| 使い捨てsessionによるstate肥大化 | TTL一括削除、response binding連動削除、incremental vacuum、WAL保持上限 |
| provider credentialが誤った上流へ出る | mode別route、provider別header allowlist、wrong-protocol route拒否 |
| 未知field/headerがegressになる | block-only payload/header guard後だけ透過 |
| model/aliasが構造を壊す | context segmentation、valueだけの置換、tool JSON再検証、完全一致復元 |
| NER/model障害で保護が低下する | revision/digest固定、offline load、silent downgrade禁止 |
| user regexによるReDoS | 危険形状をload時拒否、入力上限、detector timeout |
| log/errorから漏れる | 固定schema telemetry、session fingerprint、原文・alias対応・authを記録しない |

## 対象外

- local machine自体が侵害され、同一user権限でmemory/key/dictionaryを読まれる攻撃
- clientがSecurityMaskerを迂回してproviderへ直接接続する通信
- Web版ChatGPT、remote session、外部MCPなどlocalhost Gatewayを通らない通信
- public server、multi-user、multi-tenant、multi-worker、複数host間の共有
- 未登録の組織固有語をmodelだけで100%推測すること
- providerやmodelがaliasを改変した場合の近似復元
- file、image、audioの内容を解析・再構築してマスクすること。protocol-native添付はblockする

## 残存リスク

Python process memoryの完全消去は保証できません。日本語NERと決定論的detectorにはfalse positive/
false negativeがあります。重要な組織固有語は辞書へ登録し、`preview` と実運用前の合成promptで
確認してください。

クライアント設定は自動変更しないため、routingの正しさは利用者が確認する必要があります。
`doctor` は静的設定とGateway到達性を確認できますが、実Desktopの全通信がproxyを通ることまで
強制できません。

## 添付ファイル

SecurityMaskerが安全に扱えるのは、request JSON内へ通常のtextとして展開された内容です。
OpenAI Responsesの`input_file`／`input_image`／`input_audio`、Anthropic Messagesの
`document`／`image`／`container_upload`、base64、URL、provider上のfile IDは、内容全体を
detectorへ通して構造を保ったまま置換できません。providerのfile searchも同じ理由で対象外です。
これらを検出したrequestは上流へ転送せず、localで明示的にblockします。

CLIがlocal fileを読み、その内容を通常のprompt textとして埋め込む場合はtext maskingの対象です。
ただし「添付UIを使ったからtext化される」とは仮定せず、実際のprotocol payloadで判定します。
