from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import click
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from ._proc import BY_ID, is_katapult_mode, run
from .discover import USB_NAME_RE
from .inventory import Board, Inventory
from .profiles import get_app_start_addr, resolve_board_profile

console = Console()


class AmbiguousDeviceError(click.ClickException):
    """One chip_uid resolved to more than one distinct physical MCU on
    /dev/serial/by-id — we refuse to guess which board to write."""

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
        cmd = [sys.executable, flashtool, "-d", str(device), "-s"]
    else:
        assert board.canbus_uuid
        cmd = [sys.executable, flashtool, "-i", board.can_iface, "-u", board.canbus_uuid, "-s"]
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
    """Resolve the /dev/serial/by-id symlink for a board by its EXACT chip UID.

    Each Klipper/Katapult by-id name encodes `usb-<product>_<mcu>_<uid>-ifNN`.
    We parse every entry and compare the parsed `uid` field to `chip_uid`
    *exactly* (case-insensitively) — never a substring. The old substring glob
    (`*uid*`) let one board's UID resolve to another whose UID merely contained
    it, which cross-flashed the wrong board. The UID is the stable identity
    (invariant #1); matching it structurally is what makes that true.

    Prefers the `-if00` interface. Returns None if nothing matches. Raises
    AmbiguousDeviceError if the same UID appears on more than one distinct MCU
    family (a serial-number collision — two boards we cannot tell apart).
    """
    if not BY_ID.exists():
        return None
    target = chip_uid.strip().lower()
    matches: list[Path] = []
    mcus: set[str] = set()
    for entry in sorted(BY_ID.iterdir()):
        m = USB_NAME_RE.match(entry.name)
        if not m or m.group("uid").lower() != target:
            continue
        matches.append(entry)
        mcus.add(m.group("mcu").lower())
    if not matches:
        return None
    if len(mcus) > 1:
        raise AmbiguousDeviceError(
            f"chip_uid {chip_uid} resolves to multiple different MCUs "
            f"({', '.join(sorted(mcus))}) on {BY_ID} — refusing to flash a board "
            f"I cannot uniquely identify. Two boards likely share a non-unique "
            f"USB serial. Disconnect all but the target board and retry, or fix "
            f"the chip_uid in inventory.yaml."
        )
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
            sys.executable, flashtool,
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


def _board_identifier(board: Board) -> str:
    if board.transport == "usb":
        return f"uid {board.chip_uid}"
    return f"{board.can_iface}/{board.canbus_uuid}"


def flash_plan_table(boards: list[Board], firmware: Path) -> Table:
    """A batch overview printed before a flash/run/wizard loop touches anything,
    so the user sees the full set and order up front."""
    table = Table(title=f"Flash plan — {len(boards)} board(s), in this order")
    table.add_column("#", justify="right", style="dim")
    table.add_column("board", style="bold")
    table.add_column("transport")
    table.add_column("identifier")
    table.add_column("firmware")
    for i, b in enumerate(boards, 1):
        table.add_row(str(i), b.name, b.transport, _board_identifier(b), str(firmware))
    return table


def describe_flash(board: Board, firmware: Path) -> str:
    """Render the read-only, per-board explanation shown right before we ask for
    approval. Resolves the live device (may raise AmbiguousDeviceError — good,
    we refuse before prompting) but performs no state change."""
    lines: list[str] = [
        f"[bold]board:[/bold]      {board.name}",
        f"[bold]transport:[/bold]  {board.transport}",
    ]
    if board.transport == "usb":
        lines.append(f"[bold]chip_uid:[/bold]   {board.chip_uid}")
        dev = resolve_usb_path(board.chip_uid) if board.chip_uid else None
        if dev is None:
            lines.append("[yellow]device:     NOT currently on /dev/serial/by-id[/yellow] "
                         "(a bootloader request + wait will run at flash time)")
        elif is_katapult_mode(dev):
            lines.append(f"[bold]device:[/bold]     {dev}")
            lines.append("[bold]mode:[/bold]       Katapult (bootloader) — ready to flash")
        else:
            lines.append(f"[bold]device:[/bold]     {dev}")
            lines.append("[bold]mode:[/bold]       Klipper (app) — will be rebooted into "
                         "Katapult via `-r` first")
    else:
        lines.append(f"[bold]CAN:[/bold]        {board.can_iface} / uuid {board.canbus_uuid}")
        lines.append("[dim]if not already in Katapult mode, a CAN `-r` reboot request is "
                     "sent first[/dim]")

    if firmware.exists():
        lines.append(f"[bold]firmware:[/bold]   {firmware} ({firmware.stat().st_size} bytes)")
    else:
        lines.append(f"[bold]firmware:[/bold]   {firmware} [red](MISSING)[/red]")

    cfg = board.klipper_config.expanduser()
    fw_off = get_app_start_addr(cfg.read_text()) if cfg.exists() else None
    if fw_off is not None:
        lines.append(f"[bold]build offset:[/bold] 0x{fw_off:X}  (CONFIG_FLASH_APPLICATION_ADDRESS)")
    profile = resolve_board_profile(board.profile, board.mcu_family)
    if profile and profile.katapult_offsets:
        offs = ", ".join(f"0x{o:X}" for o in profile.katapult_offsets)
        lines.append(f"[bold]profile offsets:[/bold] {offs}  [dim](profile {profile.name})[/dim]")

    lines.append("")
    lines.append("[dim]Before writing, the offset preflight reads the chip's actual "
                 "Application Start and ABORTS on mismatch (unless --force).[/dim]")
    return "\n".join(lines)


def confirm_flash(board: Board, firmware: Path, *, assume_yes: bool) -> bool:
    """Show the per-board plan and get explicit approval. Returns True to
    proceed, False to skip this board. `assume_yes` (from --yes) auto-approves
    after still printing the panel, so a scripted run is never silent."""
    console.print(Panel(
        describe_flash(board, firmware),
        title=f"About to flash: {board.name}",
        border_style="yellow",
    ))
    if assume_yes:
        logger.info("[{}] auto-approved (--yes)", board.name)
        return True
    return Confirm.ask(f"Flash [bold]{board.name}[/bold] now?", default=False)


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
                run([sys.executable, flashtool, "-d", str(device), "-r"])
                device = wait_for_usb(board.chip_uid)
                # Now in Katapult — re-run the offset preflight if it was skipped above.
                _preflight_offset_check(inv, board, force)
            run([sys.executable, flashtool, "-d", str(device), "-f", str(firmware)])
        else:
            assert board.canbus_uuid
            run([
                sys.executable, flashtool,
                "-i", board.can_iface,
                "-u", board.canbus_uuid,
                "-f", str(firmware),
            ])
    except subprocess.CalledProcessError as e:
        raise _flash_failed(board, e) from None

    logger.success("[{}] flashed", board.name)
