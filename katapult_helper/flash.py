from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Optional

import click
from loguru import logger

from ._proc import BY_ID, is_katapult_mode, run
from .inventory import Board, Inventory
from .profiles import get_app_start_addr, resolve_board_profile

# Per Katapult's flashtool.py: `f"Application Start: 0x{self.app_start_addr:4X}\n"`.
# Python's `:4X` uses *space* padding when the value is < 4 hex digits, so the
# regex tolerates whitespace between `0x` and the digits. STM32 addresses are
# always 7+ hex digits in practice, but defense-in-depth.
APP_START_RE = re.compile(r"^Application Start:\s*0x\s*([0-9A-Fa-f]+)\s*$", re.MULTILINE)


def parse_app_start(stdout: str) -> int | None:
    m = APP_START_RE.search(stdout)
    return int(m.group(1), 16) if m else None


def query_katapult_status(inv: Inventory, board: Board) -> str | None:
    """Run `flashtool.py -s` against the board and return its full stdout, or
    None if the device is not currently reachable in Katapult mode."""
    flashtool = str(inv.flashtool)
    cmd: list[str]
    if board.transport == "usb":
        assert board.chip_uid
        device = resolve_usb_path(board.chip_uid)
        if device is None or not is_katapult_mode(device):
            return None
        cmd = ["python3", flashtool, "-d", str(device), "-s"]
    else:
        assert board.canbus_uuid
        cmd = ["python3", flashtool, "-i", board.can_iface, "-u", board.canbus_uuid, "-s"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=15)
    except subprocess.TimeoutExpired:
        logger.warning("[{}] flashtool.py -s timed out", board.name)
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def verify_app_start_match(board: Board, firmware_offset: int, katapult_stdout: str) -> None:
    """Compare the build's CONFIG_FLASH_APPLICATION_ADDRESS to the address the
    Katapult on the chip is expecting. Mismatches brick the chip — abort."""
    chip_offset = parse_app_start(katapult_stdout)
    if chip_offset is None:
        logger.warning(
            "[{}] could not parse `Application Start:` from flashtool.py -s output; "
            "skipping offset verification",
            board.name,
        )
        return
    if chip_offset != firmware_offset:
        profile = resolve_board_profile(board.profile, board.mcu_family)
        known = ""
        if profile and profile.katapult_offsets:
            known = (
                f"\n  Profile '{profile.name}' lists known-good offsets: "
                + ", ".join(f"0x{o:X}" for o in profile.katapult_offsets)
            )
        raise click.ClickException(
            f"[{board.name}] BOOTLOADER OFFSET MISMATCH — refusing to flash.\n"
            f"  Katapult on the chip expects the app at 0x{chip_offset:X}.\n"
            f"  This firmware was built for 0x{firmware_offset:X}.\n"
            f"  Flashing now would brick the chip. Re-run "
            f"`katapult-helper configure {board.name}` and pick the matching "
            f"`Bootloader offset` in menuconfig.{known}"
        )
    logger.success(
        "[{}] bootloader offset matches: chip=0x{:X}, firmware=0x{:X}",
        board.name, chip_offset, firmware_offset,
    )


def _flash_failed(board: Board, exc: subprocess.CalledProcessError) -> click.ClickException:
    return click.ClickException(
        f"[{board.name}] flashtool.py failed (exit {exc.returncode}). See output above.\n"
        f"  Common causes:\n"
        f"    - 'device already in use': klipper.service still has the MCU open. "
        f"Run `sudo systemctl stop klipper` manually, or configure passwordless "
        f"sudo (see katapult-helper docs).\n"
        f"    - 'No such file or directory' on /dev/serial/by-id/*: MCU disconnected "
        f"or chip_uid is stale. Re-run `katapult-helper discover`.\n"
        f"    - CAN UUID not found: MCU isn't in Katapult mode. Power-cycle, or "
        f"send `flashtool.py -i can0 -u <uuid> -r` first."
    )


def resolve_usb_path(chip_uid: str) -> Optional[Path]:
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
    last: Optional[Path] = None
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


def _request_can_bootloader(inv: Inventory, board: Board) -> bool:
    """For CAN boards in Klipper-app mode, send the Katapult reboot frame so
    the offset preflight has something to query. Returns True on success."""
    flashtool = str(inv.flashtool)
    try:
        run([
            "python3", flashtool,
            "-i", board.can_iface,
            "-u", board.canbus_uuid,  # type: ignore[list-item]
            "-r",
        ])
    except subprocess.CalledProcessError:
        logger.warning("[{}] CAN bootloader request failed", board.name)
        return False
    time.sleep(2.0)  # Katapult takes a moment to come up on the bus
    return True


def _preflight_offset_check(inv: Inventory, board: Board, force: bool) -> None:
    """Before flashing, verify the build's CONFIG_FLASH_APPLICATION_ADDRESS
    matches the offset the Katapult on the chip expects.

    USB boards in app mode: skipped silently here; the main flash_board flow
    sends `-r` + `wait_for_usb` and re-runs this check.

    CAN boards in app mode: we send a CAN bootloader request here ourselves so
    the protection isn't silently bypassed for the entire CAN deployment path
    (which is exactly the EBB36 / toolboard scenario).
    """
    config_path = board.klipper_config.expanduser()
    if not config_path.exists():
        return
    firmware_offset = get_app_start_addr(config_path.read_text())
    if firmware_offset is None:
        logger.warning("[{}] no CONFIG_FLASH_APPLICATION_ADDRESS in {}; skipping",
                       board.name, config_path)
        return

    katapult_stdout = query_katapult_status(inv, board)
    if katapult_stdout is None:
        if board.transport == "can":
            logger.info("[{}] CAN board not in Katapult mode; sending -r for offset preflight",
                        board.name)
            if not _request_can_bootloader(inv, board):
                if force:
                    logger.warning("[{}] CAN preflight skipped, --force in effect", board.name)
                    return
                raise click.ClickException(
                    f"[{board.name}] could not put CAN board into Katapult mode for the "
                    f"bootloader-offset preflight. Re-run with --force to flash anyway, "
                    f"or check the CAN bus."
                )
            katapult_stdout = query_katapult_status(inv, board)
            if katapult_stdout is None:
                if force:
                    logger.warning("[{}] no Katapult response after -r; --force overrides",
                                   board.name)
                    return
                raise click.ClickException(
                    f"[{board.name}] sent CAN bootloader request but no Katapult response. "
                    f"The board may already be in Katapult but flashtool.py -s timed out; "
                    f"re-run with --force to flash anyway."
                )
        else:
            logger.debug("[{}] USB chip not in Katapult mode; offset check deferred to "
                         "post-bootloader-request retry", board.name)
            return

    try:
        verify_app_start_match(board, firmware_offset, katapult_stdout)
    except click.ClickException:
        if force:
            logger.warning("[{}] offset mismatch overridden by --force", board.name)
            return
        raise


def flash_board(inv: Inventory, board: Board, firmware: Path, *, force: bool = False) -> None:
    flashtool = str(inv.flashtool)
    logger.info("[{}] flashing via {}", board.name, board.transport)

    _preflight_offset_check(inv, board, force)

    try:
        if board.transport == "usb":
            assert board.chip_uid
            device = resolve_usb_path(board.chip_uid)
            if device is None:
                raise click.ClickException(
                    f"[{board.name}] no /dev/serial/by-id entry for "
                    f"chip_uid={board.chip_uid}. Is the MCU connected? "
                    f"Run `katapult-helper discover` to see what's currently visible."
                )
            if not is_katapult_mode(device):
                logger.info("[{}] {} is in app mode; requesting bootloader",
                            board.name, device.name)
                run(["python3", flashtool, "-d", str(device), "-r"])
                device = wait_for_usb(board.chip_uid)
                # Now in Katapult — re-run the offset preflight if it was skipped above.
                _preflight_offset_check(inv, board, force)
            run(["python3", flashtool, "-d", str(device), "-f", str(firmware)])
        else:
            assert board.canbus_uuid
            run([
                "python3", flashtool,
                "-i", board.can_iface,
                "-u", board.canbus_uuid,
                "-f", str(firmware),
            ])
    except subprocess.CalledProcessError as e:
        raise _flash_failed(board, e) from None

    logger.success("[{}] flashed", board.name)
