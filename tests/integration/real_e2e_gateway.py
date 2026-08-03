"""実provider E2Eでsourceまたはone-file Gatewayを選択する。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO

REPO = Path(__file__).resolve().parents[2]
BINARY_ENV = "SM_REAL_E2E_BINARY"


def gateway_command(config: Path) -> list[str]:
    """明示されたbinaryを優先し、未指定時だけsource launcherを使う。"""
    configured = os.environ.get(BINARY_ENV)
    if configured:
        binary = Path(configured).resolve()
        if not binary.is_file():
            raise RuntimeError(f"{BINARY_ENV} is not an executable file: {binary}")
        launcher = [str(binary)]
    else:
        launcher = [sys.executable, str(REPO / "securitymasker.py")]
    return [*launcher, "gateway", "--config", str(config)]


@dataclass
class GatewayProcess:
    """起動したGatewayと、このprocess専用のstderr fileを保持する。"""

    process: subprocess.Popen[bytes]
    stderr_file: IO[str]

    def stop(self) -> str:
        """保持しているprocessだけを段階的に停止し、安全なlogを返す。"""
        if self.process.poll() is None:
            if os.name == "nt":
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
        self.stderr_file.flush()
        self.stderr_file.seek(0)
        stderr = self.stderr_file.read()
        self.stderr_file.close()
        return stderr


def start_gateway(config: Path, *, environment: Mapping[str, str]) -> GatewayProcess:
    """専用process groupとfile-backed stderrでGatewayを起動する。"""
    stderr_file = tempfile.TemporaryFile(  # noqa: SIM115 - process終了まで保持する
        mode="w+", encoding="utf-8"
    )
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        process = subprocess.Popen(  # noqa: S603
            gateway_command(config),
            cwd=REPO,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            creationflags=creation_flags,
        )
    except BaseException:
        stderr_file.close()
        raise
    return GatewayProcess(process, stderr_file)
