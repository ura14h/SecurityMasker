# ADR-0008 — caller identityとtenant／user分離境界

- 状態：適用範囲変更（[ADR-0012](0012-renew-package-design.md)）
- 日付：2026-07-25
- 関連：ADR-0005（sessionごとの鍵）、ADR-0006（専用proxy）、ADR-0012、doc/06 P0-9

> この ADR は従来の multi-tenant 構成を安全にした判断記録として残す。ADR-0012 の
> 標準製品は loopback 上の単一利用者・1 process・1 mode・1 worker に限定するため、
> `tenant` / `tenant_user` は release target から外れる。移行完了までは実装が repository に
> 残るが、新しい標準設定・SQLite schema・利用手順には持ち込まない。

## 背景

alias table は session を key とします。session ID を指定できる主体は、その session の
mapping を読み書きできるため、「誰が要求しているか」が機密性の境界です。

従来は `local`（暗黙の単一 tenant）と、共有 secret で署名した header から tenant を
得る `multitenant` の二 mode でした。監査で次の問題が見つかりました。

1. `multitenant` という名前が実際以上の分離を示していた。HMAC は tenant ID だけを
   対象とし、同一 tenant 内の二 user は alias table を共有して互いの session ID を
   提示できた。
2. 一 field だけの署名では構成全体を拘束できない。tenant と user を別々に署名すると、
   tenant A の proof と user B を組み合わせられる。

## 決定

**分離対象を名前で明示する三mode**を設けます。

| mode | 分離対象 | 用途 |
|---|---|---|
| `local` | なし（暗黙の単一caller） | 一台のworkstation、一人のuser |
| `tenant` | tenant | tenantごとに一customer |
| `tenant_user` | tenantとuser | 同一tenant内の相互に信頼しないuser |

`multitenant` は引き続き `tenant` として解決します。既存 deployment は従来どおりの
分離で動き、黙った upgrade／downgrade は行いません。

**version付きで複数fieldを一体署名したassertion**を使います。信頼済み
authenticator は、次の payload に
`HMAC-SHA256(secret, canonical_payload)` を計算します。

```text
"v2" ‖ tenant ‖ user ‖ timestamp        （各componentをlength-prefixし、␟で結合）
```

- 一体署名により、field の交換や再結合を防ぎます。
- length-prefix encoding により delimiter の曖昧性をなくし、`("a", "b:c")` と
  `("a:b", "c")` が同じ payload になることを防ぎます。
- version を含め、将来の形式を現在の verifier に replay できないようにします。
- `hmac.compare_digest` で検証し、timing oracle による byte 単位の漏えいを防ぎます。
- replay 期間を制限するため timestamp は**必須**とし、許容差は
  `SECURITYMASKER_MAX_CLOCK_SKEW_SECONDS`（既定300秒）で設定します。省略を許すには
  明示的な downgrade `SECURITYMASKER_ALLOW_UNTIMED_ASSERTIONS=1` が必要です。
- timestamp は epoch からの秒を表す**10進整数**だけを受理します。`float()` が受理する
  `nan` は比較をすり抜けるため、値の比較前に形式を検証します。`1.7e9`、`0x64`、
  `12.5`、前後空白付きの値も coercion せず拒否します。

境界は一か所ではなく**多層で強制**します。

- store key は length-prefix した identity namespace を使い、同じ session ID でも
  identity が違えば別 key にする。
- `previous_response_id` continuity の response binding も同じ namespace を使い、
  別 user の conversation 再開を防ぐ。
- 両 store は read 時に session 内の `tenant_id`／`user_id` を照合し、不一致なら
  session を返さず例外にする。

全経路で fail-closed とします。非 `local` mode で secret が未設定なら起動に失敗し、
assertion の欠落・偽造・再結合・期限切れは何も転送せず403にします。identity error
には proof、主張された identity、secret を含めません。log には namespace の
truncated SHA-256 fingerprint だけを記録します。

## 検討した代替案

- **JWT（RS256／EdDSA）**：標準化され、key rotation と expiry を持ち、多くの
  authenticator が発行できます。ただし、完全に制御できる一 header のために JWT
  library と key distribution を追加する必要があるため現時点では不採用。既存
  deployment が JWT を発行する場合は `Identity` interface に verifier を追加できます。
- **mTLS**：header ではなく channel を認証する強い方式です。一方、すべての client に
  certificate 発行・rotation を要求し、Codex／Claude Code は process 単位で対応
  しません。同じ client certificate 配下の user 識別には結局 header が必要です。
- **信頼済みreverse proxyのbare header**：単純ですが、client 自身が設定した header と
  SecurityMasker 側で区別できず、検証不能な network invariant に依存します。署名
  assertion は信頼を明示・検出可能にし、reverse proxy を signer として使えます。
- **userごとのsecret**：侵害範囲を狭めますが、未保有の secret distribution 機構が
  必要です。将来案として残します。

## 影響

- user 分離を必要とする deployment は `SECURITYMASKER_MODE=tenant_user` を設定し、
  authenticator が `X-SecurityMasker-User-ID` と両 field の assertion を送ります。
  user 分離を暗黙には有効化しません。
- 共有 secret は対称 credential であり、保持者は任意 identity を発行できます。
  authenticator だけに保持し、rotation 時は outstanding proof を即時無効化します。
- timestamp を送らない authenticator は更新が必要です。明示的 downgrade を選ぶ場合、
  `doctor` が警告します。replay 可能な既定動作を残さないための意図的な互換性変更です。

## 残存リスク

- proxy は authenticator の assertion を信頼します。侵害された authenticator は任意
  caller を詐称できます。assertion 方式の原理的限界であり、高保証環境では mTLS を
  引き続き検討します。
- `tenant` mode は同一 tenant 内の相互に信頼しない user には安全ではありません。
  その用途には `tenant_user` を使います。
