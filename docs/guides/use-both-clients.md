# CodexとClaude Codeを同時に使う

1つのSecurityMasker processは`chatgpt`または`claude`の一方だけを扱います。両方を使う場合は、
別config、別state directory、別DB、別key、別portで2 processを起動します。

repository rootで仮想環境を有効にし、mode別directoryへ初期化します。

```console
python securitymasker.py init --directory ./chatgpt --mode chatgpt --port 4000
python securitymasker.py init --directory ./claude --mode claude --port 4001
```

```text
SecurityMasker/
├── securitymasker.py
├── chatgpt/
│   ├── securitymasker.config
│   ├── securitymasker.dict
│   └── securitymasker.state/
└── claude/
    ├── securitymasker.config
    ├── securitymasker.dict
    └── securitymasker.state/
```

それぞれのconfig、辞書、state、key、DBを分け、別terminalから起動します。

```console
python securitymasker.py gateway --config ./chatgpt/securitymasker.config
python securitymasker.py gateway --config ./claude/securitymasker.config
```

同じ辞書を共有する場合は、一方のconfigの`dictionary`をもう一方の辞書へ向けられます。Windowsでは
参照先の辞書にも有効なprivate DACLが必要です。DBとkeyは共有しません。

同じDBを2 processで開くと、writer leaseにより後から起動した側を拒否します。DBとkeyをmode間で
コピーしたり共有したりしないでください。

各Gatewayに対して`doctor --require-ready --config ...`を実行し、clientのbase URLが対応する
portを向いていることを確認します。終了と日常運用は[日常的な使い方](../operations/daily-use.md)を
参照してください。
