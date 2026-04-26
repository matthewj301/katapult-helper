from __future__ import annotations

import contextlib
from pathlib import Path

import click
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from ._proc import klipper_stopped
from .build import build_board
from .configure import configure_all_missing, ensure_make_available
from .discover import discover_can, discover_usb
from .flash import flash_board
from .inventory import (
    Inventory,
    build_inventory,
    load_inventory,
    load_raw,
    save_raw,
    upsert_board,
)

console = Console()


def _suggest_name(product: str, mcu_family: str, chip_uid: str) -> str:
    short = chip_uid[-6:].lower()
    return f"{product.lower()}-{mcu_family}-{short}"


def _default_config_path(name: str) -> str:
    return f"~/printer_data/firmware_configs/{name.replace('-', '_')}.config"


def _ask_nonempty(prompt: str, field: str, *, default: str | None = None) -> str:
    """Like Prompt.ask but reprompts on empty/whitespace input instead of aborting."""
    while True:
        kwargs = {"default": default} if default is not None else {}
        value = Prompt.ask(prompt, **kwargs).strip()
        if value:
            return value
        logger.warning("{} cannot be empty; please try again", field)


def _reconcile_inventory(inventory_path: Path, inv: Inventory) -> Inventory:
    """Discover MCUs and prompt the user to add unknown ones to inventory.yaml.
    Returns the (possibly updated) Inventory; persists to disk if changed."""
    raw = load_raw(inventory_path)
    changed = False

    known_uids = {b.chip_uid for b in inv.boards.values() if b.chip_uid}
    known_can = {(b.can_iface, b.canbus_uuid) for b in inv.boards.values() if b.canbus_uuid}

    usb_devices = discover_usb()
    new_usb = [d for d in usb_devices if d.chip_uid not in known_uids]
    if new_usb:
        console.print(Panel(
            f"[bold]Found {len(new_usb)} USB MCU(s) not in inventory.[/bold]\n"
            "For each, you'll be asked to give it a friendly name and a "
            ".config path; then we'll walk through menuconfig and flash it.",
            title="Discovered USB devices",
        ))
    seen_uids: set[str] = set()
    for d in new_usb:
        if d.chip_uid in seen_uids:
            continue
        seen_uids.add(d.chip_uid)
        console.print(
            f"\n[bold]USB:[/bold] {d.by_id.name}\n"
            f"  product:    {d.product}\n"
            f"  mcu_family: {d.mcu_family}\n"
            f"  chip_uid:   {d.chip_uid}"
        )
        if not Confirm.ask("Add this MCU to inventory?", default=True):
            continue
        suggested = _suggest_name(d.product, d.mcu_family, d.chip_uid)
        name = _ask_nonempty("Friendly name", "name", default=suggested)
        cfg = _ask_nonempty(
            "Path to .config (will be created)",
            "klipper_config",
            default=_default_config_path(name),
        )
        if upsert_board(
            raw, name,
            transport="usb",
            klipper_config=cfg,
            chip_uid=d.chip_uid,
            mcu_family=d.mcu_family,
        ):
            changed = True
            logger.success("[{}] added to inventory", name)

    can_devices = discover_can(inv)
    new_can = [c for c in can_devices if (c.iface, c.uuid) not in known_can]
    if new_can:
        console.print(Panel(
            f"[bold]Found {len(new_can)} CAN MCU(s) not in inventory.[/bold]",
            title="Discovered CAN devices",
        ))
    for c in new_can:
        console.print(
            f"\n[bold]CAN:[/bold] {c.iface}/{c.uuid}  (application: {c.application})"
        )
        if not Confirm.ask("Add this MCU to inventory?", default=True):
            continue
        suggested = f"can-{c.uuid[-6:]}"
        name = _ask_nonempty("Friendly name", "name", default=suggested)
        family = _ask_nonempty(
            "MCU family (e.g. stm32g0b1xx, rp2040)", "mcu_family", default="stm32g0b1xx",
        )
        cfg = _ask_nonempty(
            "Path to .config (will be created)",
            "klipper_config",
            default=_default_config_path(name),
        )
        if upsert_board(
            raw, name,
            transport="can",
            klipper_config=cfg,
            mcu_family=family,
            can_iface=c.iface,
            canbus_uuid=c.uuid,
        ):
            changed = True
            logger.success("[{}] added to inventory", name)

    if changed:
        save_raw(inventory_path, raw)
        logger.success("inventory updated: {}", inventory_path)
        return build_inventory(raw)
    return inv


def run_wizard(inventory_path: Path, *, do_flash: bool) -> None:
    """End-to-end flow: discover -> upsert -> configure missing -> build -> flash."""
    inv = load_inventory(inventory_path)
    ensure_make_available(inv)
    logger.info("klipper repo: {} ({})", inv.klipper_repo, inv.repo_kind)
    logger.info("katapult repo: {}", inv.katapult_repo)

    inv = _reconcile_inventory(inventory_path, inv)

    if not inv.boards:
        logger.warning("inventory is empty; nothing to do")
        return

    console.print(Panel(
        f"Plan: walk through menuconfig for any of {len(inv.boards)} board(s) "
        "missing a .config, then build → flash each.\n"
        "[dim]klipper.service will be stopped once at start and restarted once at the end.[/dim]",
        title="Wizard plan",
    ))
    if not Confirm.ask("Proceed?", default=True):
        raise click.Abort()

    configure_all_missing(inv)

    bracket = klipper_stopped() if do_flash else contextlib.nullcontext()
    with bracket:
        for board in inv.boards.values():
            firmware = build_board(inv, board, run_menuconfig=False)
            if do_flash:
                flash_board(inv, board, firmware)
            else:
                logger.info("[{}] skipping flash (--no-flash)", board.name)

    console.print(Panel(
        "[bold green]Done.[/bold green] All boards configured, built, "
        f"{'and flashed' if do_flash else '(flash skipped)'}.",
        title="Wizard complete",
    ))
