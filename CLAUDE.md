# CLAUDE.md

このrepositoryでは [AGENTS.md](AGENTS.md) を現行の共通開発ルールとして読み、同じ指示に従って
ください。特に次を優先します。

- 不変条件、[ADR-0012](docs/adr/0012-renew-package-design.md)、その他の文書の順で判断する。
- 製品は単一利用者local用途、1 process・1 mode・1 loopback port・1 worker。
- 通常storeはmode別の暗号化SQLite。Redis/Docker/public bind/multi-tenantは製品範囲外。
- client設定を自動変更しない。repo外の実設定へ書き込まない。
- 日本語NERは標準・既定ONで、欠落や異常をsilent downgradeしない。
- 実providerへtest bodyを送らず、実在人物・実secretをfixtureへ入れない。
- 作業前に [development status](docs/development/status.md) を確認し、`done` を過大申告しない。
- 通常setupとtest setupを混同しない。
- ownerの依頼なしにcommit、push、PR、外部公開を行わない。

詳細なarchitecture、test、文書構成、commit規則はAGENTS.mdを正とします。
