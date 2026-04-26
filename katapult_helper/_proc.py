from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path
from typing import Optional

import click
from loguru import logger

KLIPPER_SERVICE = "klipper"
KATAPULT_MARKER = "katapult"
BY_ID = Path("/dev/serial/by-id")
CAN_SYSFS = Path("/sys/class/net")


def run(
    cmd: "list[str]",
    *,
    cwd: Optional[Path] = None,
    env: Optional["dict[str, str]"] = None,
    check: bool = True,
) -> "subprocess.CompletedProcess[str]":
    suffix = f" (cwd={cwd})" if cwd else ""
    logger.info("$ {}{}", " ".join(cmd), suffix)
    full_env = {**os.environ, **env} if env else None
    return subprocess.run(cmd, cwd=cwd, env=full_env, check=check)


def is_katapult_mode(path: Path) -> bool:
    return KATAPULT_MARKER in path.name.lower()


def can_iface_present(iface: str) -> bool:
    return (CAN_SYSFS / iface).exists()


def _systemctl_stop(unit: str) -> None:
    """Stop a systemd unit, raising a friendly ClickException if sudo blocks us.

    On RaspberryPi OS the `pi` user typically does not have NOPASSWD for
    systemctl, so a non-interactive `sudo systemctl stop klipper` will fail and
    leave the unit running — meaning the MCU's /dev/ttyACM* stays open and the
    subsequent flashtool.py call hits "device already in use".
    """
    cmd = ["sudo", "-n", "systemctl", "stop", unit]
    logger.info("$ {}", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return
    stderr = (result.stderr or "").strip()
    if "password is required" in stderr or "askpass" in stderr or "a terminal is required" in stderr:
        raise click.ClickException(
            f"Cannot stop {unit}.service: passwordless sudo is not configured.\n"
            f"  Either:\n"
            f"    1. Run `sudo systemctl stop {unit}` manually before katapult-helper, or\n"
            f"    2. Add a sudoers entry granting NOPASSWD for systemctl, e.g.:\n"
            f"       echo '{os.environ.get('USER', 'pi')} ALL=NOPASSWD: /bin/systemctl' "
            f"| sudo tee /etc/sudoers.d/katapult-helper"
        )
    raise click.ClickException(
        f"`sudo systemctl stop {unit}` failed (exit {result.returncode}): {stderr}"
    )


def _systemctl_start(unit: str) -> None:
    """Best-effort restart; warn but don't fail (we run in finally:)."""
    cmd = ["sudo", "-n", "systemctl", "start", unit]
    logger.info("$ {}", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.warning(
            "could not restart {}.service ({}). Run `sudo systemctl start {}` manually.",
            unit, (result.stderr or "").strip(), unit,
        )


@contextlib.contextmanager
def klipper_stopped(unit: str = KLIPPER_SERVICE):
    _systemctl_stop(unit)
    try:
        yield
    finally:
        _systemctl_start(unit)
