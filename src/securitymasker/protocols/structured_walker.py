"""構造を保持するJSON walker。

payload全体を文字列化して一括置換せず、構造を辿って文字列値だけを変換する。dict key、
ID、typeやroleなどの構造fieldは変更しない。

``transform_all_string_values``はtool引数など自由形式JSONの全文字列値を対象にする。
``transform_field``と``transform_text_fields``は既知のenvelopeで指定fieldだけを対象にする。
masking engineをinlineでawaitできるよう変換は非同期である。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Transform = Callable[[str], Awaitable[str]]


async def transform_all_string_values(node: Any, transform: Transform) -> Any:
    """全string valueを再帰変換し、dict keyとstring以外は変更しない。"""
    if isinstance(node, str):
        return await transform(node)
    if isinstance(node, list):
        return [await transform_all_string_values(item, transform) for item in node]
    if isinstance(node, dict):
        return {key: await transform_all_string_values(val, transform) for key, val in node.items()}
    return node


def transform_all_string_values_sync(node: Any, transform: Callable[[str], str]) -> Any:
    """tool argument復元用の``transform_all_string_values``同期版。"""
    if isinstance(node, str):
        return transform(node)
    if isinstance(node, list):
        return [transform_all_string_values_sync(item, transform) for item in node]
    if isinstance(node, dict):
        return {key: transform_all_string_values_sync(val, transform) for key, val in node.items()}
    return node


async def transform_field(obj: Any, key: str, transform: Transform) -> None:
    """``obj[key]``が文字列なら変換結果へin-placeで置換する。"""
    if isinstance(obj, dict) and isinstance(obj.get(key), str):
        obj[key] = await transform(obj[key])


async def transform_text_fields(obj: Any, keys: frozenset[str], transform: Transform) -> None:
    """``keys``のいずれかに格納された文字列値をin-placeで変換する。"""
    if isinstance(obj, dict):
        for key in keys:
            await transform_field(obj, key, transform)
