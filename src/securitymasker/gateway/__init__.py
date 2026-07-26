"""ChatGPT/CodexとClaude Code専用の透過masking proxy。

client requestを受け取り、機密値をマスクしてから対応providerへ転送し、responseを
同じsessionの対応表で復元する。clientの認証情報は対応するproviderへだけ透過し、
保存・復号・ログ記録しない。requestとstreaming responseの両方向をGatewayが所有する。
"""
