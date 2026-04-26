from __future__ import annotations

import sys
from pathlib import Path

import click
from loguru import logger
from rich.console import Console
from rich.table import Table

from ._proc import klipper_stopped
from .build import build_board
from .configure import configure_all_missing, configure_board, ensure_make_available
from .discover import discover_can, discover_usb
from .flash import flash_board
from .inventory import load_inventory
from .wizard import run_wizard

console = Console()


def _configure_logging(verbose: bool) -> None:
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level, format="<level>{level: <7}</level> | {message}")


@click.group()
@click.option(
    "--inventory", "-c", "inventory_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default="inventory.yaml", show_default=True,
)
@click.option("--verbose", "-v", is_flag=True)
@click.pass_context
def cli(ctx: click.Context, inventory_path: Path, verbose: bool) -> None:
    """Batch build and flash Klipper firmware via Katapult."""
    _configure_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["inventory_path"] = inventory_path


@cli.command(name="list")
@click.pass_context
def list_boards(ctx: click.Context) -> None:
    """List boards defined in the inventory."""
    inv = load_inventory(ctx.obj["inventory_path"])
    logger.info("klipper repo: {} ({})", inv.klipper_repo, inv.repo_kind)
    table = Table(title=f"Boards ({len(inv.boards)})")
    table.add_column("name", style="bold")
    table.add_column("transport")
    table.add_column("identifier")
    table.add_column("config")
    for b in inv.boards.values():
        ident = b.chip_uid if b.transport == "usb" else f"{b.can_iface}/{b.canbus_uuid}"
        table.add_row(b.name, b.transport, ident or "-", str(b.klipper_config))
    console.print(table)


@cli.command()
@click.pass_context
def discover(ctx: click.Context) -> None:
    """Scan /dev/serial/by-id and CAN bus for MCUs."""
    inv = load_inventory(ctx.obj["inventory_path"])
    usb = discover_usb()
    table = Table(title="USB devices (/dev/serial/by-id)")
    table.add_column("product")
    table.add_column("mcu_family")
    table.add_column("chip_uid")
    table.add_column("by-id")
    for d in usb:
        table.add_row(d.product, d.mcu_family, d.chip_uid, d.by_id.name)
    console.print(table)
    can = discover_can(inv)
    table = Table(title="CAN devices")
    table.add_column("iface"); table.add_column("uuid"); table.add_column("application")
    for c in can:
        table.add_row(c.iface, c.uuid, c.application)
    console.print(table)


@cli.command()
@click.argument("names", nargs=-1)
@click.option("--all-missing", is_flag=True,
              help="Walk through every board whose config file does not yet exist.")
@click.option("--force", is_flag=True,
              help="Edit configs that already exist without prompting.")
@click.pass_context
def configure(ctx: click.Context, names: tuple[str, ...], all_missing: bool, force: bool) -> None:
    """Walk through `make menuconfig` for one or more boards to create/edit their .config files."""
    inv = load_inventory(ctx.obj["inventory_path"])
    ensure_make_available(inv)
    logger.info("klipper repo: {} ({})", inv.klipper_repo, inv.repo_kind)
    if all_missing and names:
        raise click.UsageError("--all-missing is mutually exclusive with named boards")
    if all_missing:
        configure_all_missing(inv)
        return
    boards = inv.select(names)
    for board in boards:
        configure_board(inv, board, force=force)


@cli.command()
@click.argument("names", nargs=-1)
@click.option("--menuconfig/--no-menuconfig", default=False,
              help="Run interactive menuconfig before build (auto-on if config missing).")
@click.pass_context
def build(ctx: click.Context, names: tuple[str, ...], menuconfig: bool) -> None:
    """Build firmware for one or more boards (or all if none given)."""
    inv = load_inventory(ctx.obj["inventory_path"])
    logger.info("building in {} ({})", inv.klipper_repo, inv.repo_kind)
    boards = inv.select(names)
    for board in boards:
        build_board(inv, board, run_menuconfig=menuconfig)


@cli.command()
@click.argument("names", nargs=-1)
@click.option("--firmware", "-f", type=click.Path(exists=True, path_type=Path),
              help="Use this prebuilt klipper.bin instead of inv.klipper_repo/out/klipper.bin.")
@click.pass_context
def flash(ctx: click.Context, names: tuple[str, ...], firmware: Path | None) -> None:
    """Flash firmware to one or more boards. Does not rebuild."""
    inv = load_inventory(ctx.obj["inventory_path"])
    boards = inv.select(names)
    fw = firmware or inv.klipper_bin
    if not fw.exists():
        raise click.UsageError(f"firmware not found: {fw} (run `build` first or pass --firmware)")
    with klipper_stopped():
        for board in boards:
            flash_board(inv, board, fw)


@cli.command()
@click.argument("names", nargs=-1)
@click.option("--menuconfig/--no-menuconfig", default=False)
@click.pass_context
def run(ctx: click.Context, names: tuple[str, ...], menuconfig: bool) -> None:
    """Full pipeline: build then flash, board-by-board. Klipper restarts once at end."""
    inv = load_inventory(ctx.obj["inventory_path"])
    logger.info("klipper repo: {} ({})", inv.klipper_repo, inv.repo_kind)
    boards = inv.select(names)
    with klipper_stopped():
        for board in boards:
            firmware = build_board(inv, board, run_menuconfig=menuconfig)
            flash_board(inv, board, firmware)


@cli.command()
@click.option("--no-flash", is_flag=True,
              help="Do everything except the actual flash (build only, useful for dry runs).")
@click.pass_context
def wizard(ctx: click.Context, no_flash: bool) -> None:
    """One-shot, end-to-end: discover unknown MCUs, add them to inventory,
    walk through menuconfig for any missing .config, build, then flash all.
    Stops klipper.service once at start and restarts it once at the end."""
    run_wizard(ctx.obj["inventory_path"], do_flash=not no_flash)


if __name__ == "__main__":
    cli()
