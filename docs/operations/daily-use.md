# 日常的な使い方

初回設定が済んだ後は、Gatewayを起動し、状態を確認してからCodexまたはClaude Codeを使います。

## 起動する

repository rootで仮想環境を有効にします。

macOS／Linux:

```console
. .venv/bin/activate
```

Windowsの`cmd.exe`:

```bat
.venv\Scripts\activate.bat
```

Gatewayを起動します。

```console
python securitymasker.py gateway
```

別名configを使う場合は`--config PATH`で明示できます。別terminalからreadinessを確認する場合も、
仮想環境を有効にします。

```console
python securitymasker.py doctor --require-ready
```

設定や辞書を変更した場合は、Gatewayを停止して`config-check`と合成値の`preview`を行い、
再起動してください。hot reloadは行いません。

## 利用中

- Gatewayを起動したterminalを閉じないでください。
- clientが対応するlocalhost portを向いていることを確認してください。
- Web版ChatGPT、remote session、外部MCPなど、Gatewayを通らない通信は保護されません。
- file、image、audioのprotocol-native添付は、検査できないためblockされます。

## 終了する

clientでの操作を終え、Gatewayを起動したterminalで`Ctrl+C`を1回入力します。shellのpromptが
戻るまで待ちます。

macOS／Linuxのbackground processには`SIGTERM`を使います。通常の終了で応答しない場合を除き、
`SIGKILL`やterminalの強制終了は避けてください。

Gateway終了後もclient設定は残ります。次回は同じconfigでGatewayを再起動します。しばらく
使わない場合や設定を元へ戻す場合は[アンインストールと設定の復旧](uninstall.md)を参照してください。
