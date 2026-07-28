# ADR-0016 — 未公開の旧形式を廃止し、現行configをschema v1とする

- 状態：採用
- 日付：2026-07-28
- 置換・修正する決定：
  [ADR-0012](0012-renew-package-design.md) のconfig schema v2という番号、および
  [ADR-0014](0014-reject-non-python-port-for-current-product.md) の「現行config v2を変更しない」
  という記述

## 背景

ADR-0012以前のフラットなconfig v1は、現行製品の公開releaseとして配布されていない。
現行実装には旧v1を受け付ける互換経路が残っていたため、最初の公開契約がschema v2から始まる
状態になっていた。

未公開形式との互換性を理由にversion番号を消費すると、利用者には存在しないmigration経路を
保守しているように見える。最初の公開前であれば、現行形式を最初のschemaとして定義し直す方が
契約を正確かつ単純にできる。

config schema versionとapplication versionは別の契約である。configの`version: 1`は、
applicationを直ちに`1.0.0`とする根拠にも、その妨げにもならない。

## 決定

- 現行のruntime、state、単一dictionary参照、detector設定を持つ
  `securitymasker.config`をschema v1とする。
- 旧フラット形式の読み込みと変換を削除する。同じ`version: 1`で形状を推測して分岐しない。
- `version`が1以外、または現行v1の必須fieldがないconfigはfail-closedで拒否する。
- `securitymasker.dict`も従来どおり独立したschema v1とする。二つのfileのversionはそれぞれの
  schemaを表し、同時に変更する必要はない。
- application versionは当面`0.1.0`のままとし、最初の公開versionを決める際に別途判断する。

## application 1.0.0の判断基準

最初の公開releaseをsource版に限定するなら、binary公開のblockerはapplication `1.0.0`の
blockerにしない。次を満たした時点で`1.0.0`を採用できる。

1. schema v1への変更後にsource release gateを再実行し、対応対象のmacOS arm64 / Linux
   arm64で合格する。
2. CLI、config schema、DB永続化、対応protocolを、互換性を管理する公開契約として維持する。
3. release noteとREADMEで、`1.0.0`がsource版の安定releaseであり、one-file binaryと
   Windowsは公開対応範囲外だと明記する。
4. 公開済みの`0.1.0` tagやartifactがないことをrelease前に確認する。存在する場合は、
   schema非互換とmigration非提供をrelease noteへ明記する。

これらを満たす前にversion値だけを変更しない。

## 結果

- 最初の公開config契約をv1から開始できる。
- 存在しない旧形式のcompatibility codeとtestを保守しない。
- 旧フラットconfigは自動変換されず、現行形式へ書き直す必要がある。
- application versionとconfig schema versionを独立して更新できる。
