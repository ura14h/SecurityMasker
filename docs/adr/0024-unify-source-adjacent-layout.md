# ADR-0024 — source版の既定data配置を全OSでlauncher隣接へ統一する

- 状態：採用（Windows native検証済み）
- 日付：2026-08-02
- 関連：[ADR-0012](0012-renew-package-design.md)、
  [ADR-0021](0021-add-windows-native-source-target.md)、
  [ADR-0023](0023-support-windows-native-source.md)

## 背景

ADR-0012はsource版のconfig、辞書、stateをroot scriptへ隣接させ、追加の環境変数なしで解決する
単純な利用方法を採用した。その後のWindows対応では、source checkout全体のDACLを製品が変更しない
ため、Windowsだけmode別`%LOCALAPPDATA%`へdataを分離した。

この分岐により、Windows利用者だけが通常commandの前に`SECURITYMASKER_CONFIG`を設定する必要が生じ、
全OS共通の導入手順と日常運用を複雑にした。`SECURITYMASKER_CONFIG`はconfigの選択手段であり、dataの
暗号化やaccess controlを強化しない。

POSIX source版はsource root自体のpermissionを変更せず、config、辞書、key、DBを`0600`、state
directoryを`0700`として保護している。Windowsも同じ管理対象の境界へDACLを適用すれば、利用方法まで
OS別に分ける必要はない。

## 判断

- source版の`init`は全OSでroot scriptのdirectoryを既定の作成先とする。
- config探索は従来どおり`--config`、`SECURITYMASKER_CONFIG`、launcher隣接configの順とする。
- 通常の単一mode利用では`SECURITYMASKER_CONFIG`を要求しない。
- Windowsではsource rootのDACLを変更しない。次の管理対象だけへcurrent user、SYSTEM、
  Administratorsのprotected DACLを作成・検査する。
  - `securitymasker.config`
  - `securitymasker.dict`
  - `securitymasker.state/`と配下のkey、DB、SQLite sidecar、lease
- Windowsのlocal fixed NTFS、owner、reparse point、未知ACEのfail-closed検査は維持する。
- source rootに他の配布fileが存在することを理由に`init`を拒否しない。通常の`init`と`init --force`が
  作成・置換できる対象は従来どおり管理対象artifactだけとし、source fileを変更・削除しない。
- Lite／Full one-file binaryの既定配置は変更しない。
- 両modeを同時利用する場合は、全OSで`--directory`または別configを明示してstate、DB、key、portを
  分離する。

## Security境界

source rootを別principalが変更できる環境では、dataだけでなく実行するPython source自体も差し替え
可能である。このADRはsource配布物をcurrent userが管理するlocal directoryへ展開する既存前提を
変更しない。SecurityMaskerが強制する機密dataのaccess controlは管理対象artifactへ限定し、一般的な
source tree全体のACL管理を製品機能にしない。

config、辞書、state、key、DBのDACLまたはownerを検査できない場合は、従来どおりloadまたは起動を
fail-closedで拒否する。

## 検証

2026-08-03にWindows 11 x64、CPython 3.12 x64、local fixed NTFSでnative source gateを実行し、
次を確認した。詳細は
[Windows x64 launcher隣接source evidence](../development/release-evidence/windows-x64-launcher-adjacent-source-2026-08-03.md)
に記録する。

1. fresh source treeのrootで既定`init`が成功し、source fileを変更しない。
2. config、辞書、state、key、DBのprotected DACLを作成・検査する。
3. source rootのDACLを変更しない。
4. permissive artifact DACL、wrong owner、reparse point、非local NTFSを拒否する。
5. 両modeの明示分離、mock Gateway E2E、native process gateを完走する。

protocol、masking、NER、実provider通信、binary artifactは変更しないため、このlayout変更だけを理由に
実OpenAI／Anthropic E2Eまたはbinary gateを再実行しない。

## 置換する判断

このADRは、ADR-0021およびADR-0023のWindows source版をmode別`%LOCALAPPDATA%`へ置く部分と、source
rootへ管理対象外entryを許可しない部分を置き換える。Windowsの対応target、DACLのprincipal、local
fixed NTFS、fail-closed条件は置き換えない。
