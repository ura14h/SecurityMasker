# トラブルシューティング

最初に次を実行してください。

```console
python3 securitymasker.py doctor
python3 securitymasker.py config-check
```

`doctor --json` はticketへ貼れるsecret非表示の形式です。実providerには接続しません。

## configが見つからない

探索順は `--config`、`SECURITYMASKER_CONFIG`、実行ファイル隣接の
`securitymasker.config` です。current working directoryは探索しません。別名configを使う場合は
`--config` を必ず指定してください。

## unsafe permissions

config、辞書、keyはownerだけが読める `0600`、state directoryは `0700` が標準です。

```console
chmod 600 securitymasker.config securitymasker.dict \
  securitymasker.state/securitymasker.key
chmod 700 securitymasker.state
```

## NER modelが無い、またはdigestが違う

source版ではnetworkを使用できるsetup時に再取得・検証します。

```console
./scripts/setup
```

実行中に自動downloadして保護能力を下げることはありません。破損したcacheを手作業で信頼済みに
するのではなく、固定revisionを再取得してください。

## portが使用中

別のSecurityMaskerが同じconfigで動いていないか確認し、停止するかconfigのportを変更します。
同じDBを2プロセスで共有することはできません。

## DB/key mismatch、key missing

新しいkeyを自動生成して既存DBを開くことはありません。対応するDB/keyのbackupを組で戻します。
復旧できない場合は古いDBを保全したうえで、新しいdirectoryへ `init` し直してください。
古い対応表は復元できなくなるため、新しい会話を開始します。

## clientがproxyを通らない

`client-config` の出力と、実際にクライアントが読む設定を比較してください。

- ChatGPT/Codex: `model_provider = "securitymasker"` と `base_url` を確認。
- Claude: Claudeを起動したprocess環境の `ANTHROPIC_BASE_URL` を確認。
- `OPENAI_BASE_URL`、`OPENAI_API_BASE`、`ANTHROPIC_API_URL` が別の直通先を指していないか確認。

SecurityMaskerはクライアント設定を自動更新しません。Web会話、remote session、外部MCPなど
localhostを通らない通信は保護できません。

## one-file版の起動が遅い

標準NER、Python、torch等を含むため、macOS arm64の検証値で約917 MiB、cold helpで約25.5秒、
最初のNER previewで約46.8秒でした。one-fileは `TMPDIR` へ展開するため、空き容量と実行権限が
必要です。`noexec` filesystemを `TMPDIR` に使わず、難しい場合はsource版を使ってください。

## backup

Gatewayを停止してから、次を同じbackup単位で保存します。

- `securitymasker.config`
- `securitymasker.dict`
- `securitymasker.state/securitymasker.db`
- `securitymasker.state/securitymasker.key`

key、辞書、DBをrepository、ticket、chat、外部backupへ平文で置かないでください。backup先の
暗号化とaccess controlは利用者の責任です。
