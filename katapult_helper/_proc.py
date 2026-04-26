from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

from loguru import logger

KLIPPER_SERVICE = "klipper"
KATAPULT_MARKER = "katapult"
BY_ID = Path("/dev/serial/by-id")
CAN_SYSFS = Path("/sys/class/net")


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    suffix = f" (cwd={cwd})" if cwd else ""
    logger.info("$ {}{}", " ".join(cmd), suffix)
    full_env = {**os.environ, **env} if env else None
    return subprocess.run(cmd, cwd=cwd, env=full_env, check=check)


def is_katapult_mode(path: Path) -> bool:
    return KATAPULT_MARKER in path.name.lower()


def can_iface_present(iface: str) -> bool:
    return (CAN_SYSFS / iface).exists()


def systemctl(action: str, unit: str = KLIPPER_SERVICE) -> None:
    result = run(["sudo", "systemctl", action, unit], check=False)
    if result.returncode != 0:
        logger.warning("systemctl {} {} returned {}", action, unit, result.returncode)


@contextlib.contextmanager
def klipper_stopped(unit: str = KLIPPER_SERVICE):
    systemctl("stop", unit)
    try:
        yield
    finally:
        systemctl("start", unit)
