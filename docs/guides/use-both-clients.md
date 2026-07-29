# CodexとClaude Codeを同時に使う

1つのSecurityMasker processは`chatgpt`または`claude`の一方だけを扱います。両方を使う場合は、
別config、別state directory、別DB、別key、別portで2 processを起動します。

同じ辞書を共有することはできます。

```text
SecurityMasker/
├── securitymasker.py
├── securitymasker.dict
├── chatgpt.config
├── chatgpt.state/
│   ├── securitymasker.db
│   └── securitymasker.key
├── claude.config
└── claude.state/
    ├── securitymasker.db
    └── securitymasker.key
```

それぞれのconfigでstate path、mode、portを分け、別terminalから起動します。

```console
python3 securitymasker.py gateway --config ./chatgpt.config
python3 securitymasker.py gateway --config ./claude.config
```

同じDBを2 processで開くと、writer leaseにより後から起動した側を拒否します。DBとkeyをmode間で
コピーしたり共有したりしないでください。

各Gatewayに対して`doctor --require-ready --config ...`を実行し、clientのbase URLが対応する
portを向いていることを確認します。終了と日常運用は[日常的な使い方](../operations/daily-use.md)を
参照してください。
