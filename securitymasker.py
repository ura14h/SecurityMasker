#!/usr/bin/env python3
"""Source checkoutからpackage CLIを呼ぶ薄いlauncher。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "src"
PACKAGE = SOURCE / "securitymasker"

# Repository rootは通常のPython import pathにも入るため、このlauncher自身が
# ``securitymasker`` packageを隠さないよう、importされた場合だけpackage pathを公開する。
if __name__ == "securitymasker":
    __path__ = [str(PACKAGE)]
    package_init = PACKAGE / "__init__.py"
    exec(compile(package_init.read_bytes(), str(package_init), "exec"), globals())


def run() -> int:
    sys.path.insert(0, str(SOURCE))
    from securitymasker.cli import main

    return main()


if __name__ == "__main__":
    raise SystemExit(run())
