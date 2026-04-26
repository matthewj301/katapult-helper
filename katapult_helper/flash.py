from __future__ import annotations

import time
from pathlib import Path

from loguru import logger

from ._proc import BY_ID, is_katapult_mode, run
from .inventory import Board, Inventory


def resolve_usb_path(chip_uid: str) -> Path | None:
    if not BY_ID.exists():
        return None
    matches = sorted(BY_ID.glob(f"*{chip_uid}*"))
    if not matches:
        return None
    for p in matches:
        if p.name.endswith("-if00"):
            return p
    return matches[0]


def wait_for_usb(chip_uid: str, *, timeout: float = 15.0) -> Path:
    deadline = time.monotonic() + timeout
    last: Path | None = None
    while time.monotonic() < deadline:
        path = resolve_usb_path(chip_uid)
        if path and path != last:
            logger.info("found device {} for uid {}", path, chip_uid)
            last = path
        if path and is_katapult_mode(path):
            return path
        time.sleep(0.5)
    if last is not None:
        logger.warning("timed out waiting for katapult; using {}", last)
        return last
    raise TimeoutError(f"no /dev/serial/by-id/* matched chip_uid={chip_uid} within {timeout}s")


def flash_board(inv: Inventory, board: Board, firmware: Path) -> None:
    flashtool = str(inv.flashtool)
    logger.info("[{}] flashing via {}", board.name, board.transport)

    if board.transport == "usb":
        assert board.chip_uid
        device = resolve_usb_path(board.chip_uid)
        if device is None:
            raise FileNotFoundError(
                f"[{board.name}] no /dev/serial/by-id entry for chip_uid={board.chip_uid}"
            )
        if not is_katapult_mode(device):
            logger.info("[{}] {} is in app mode; requesting bootloader", board.name, device.name)
            run(["python3", flashtool, "-d", str(device), "-r"])
            device = wait_for_usb(board.chip_uid)
        run(["python3", flashtool, "-d", str(device), "-f", str(firmware)])
    else:
        assert board.canbus_uuid
        run([
            "python3", flashtool,
            "-i", board.can_iface,
            "-u", board.canbus_uuid,
            "-f", str(firmware),
        ])

    logger.success("[{}] flashed", board.name)
