"""PyInstaller専用entry point。

root launcherとpackageが同名でも、解析対象のファイル名がpackageを隠さないよう分離する。
"""

from __future__ import annotations

if __name__ == "__main__":  # pragma: no cover
    # model-load時にHugging Face clientが起動するresource trackerを、PyInstaller
    # executableの通常CLIとして再実行しない。CLI moduleのimportより先に処理する。
    from multiprocessing import freeze_support

    freeze_support()

    from securitymasker.cli import main

    raise SystemExit(main())
