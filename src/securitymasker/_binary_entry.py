"""PyInstaller専用entry point。

root launcherとpackageが同名でも、解析対象のファイル名がpackageを隠さないよう分離する。
"""

from __future__ import annotations

from securitymasker.cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
