"""protocol adapter（OpenAI Responses、Anthropic Messages）と共有walker。

adapterは利用者データを保持するfieldをprotocolごとに選び、masking engineはその値を
どのように検出・置換するかを決める。protocol固有処理をmasking coreへ混在させない。
"""
