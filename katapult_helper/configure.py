from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import click
from loguru import logger
from rich.console import Console
from rich.panel import Panel

from .inventory import Board, Inventory

console = Console()

# Suggested Kconfig answers per MCU family for Katapult-equipped boards.
# These are guidance text shown before launching menuconfig — they are NOT
# applied automatically (menuconfig drives the actual selections).
HINTS: dict[str, dict[str, str]] = {
    "stm32f103xx": {
        "Micro-controller Architecture": "STMicroelectronics STM32",
        "Processor model": "STM32F103",
        "Bootloader offset": "8KiB bootloader",
        "Communication interface": "USB (on PA11/PA12)",
    },
    "stm32f405xx": {
        "Processor model": "STM32F405",
        "Bootloader offset": "32KiB bootloader",
        "Communication interface": "USB (on PA11/PA12)",
    },
    "stm32f446xx": {
        "Processor model": "STM32F446",
        "Bootloader offset": "32KiB bootloader",
        "Communication interface": "USB (on PA11/PA12)",
    },
    "stm32g0b1xx": {
        "Processor model": "STM32G0B1",
        "Bootloader offset": "8KiB bootloader",
        "Communication interface": "CAN bus (on PB0/PB1)  — typical for EBB36/42",
        "CAN bus speed": "1000000",
    },
    "stm32h723xx": {
        "Processor model": "STM32H723",
        "Bootloader offset": "128KiB bootloader",
        "Communication interface": "USB (on PA11/PA12)",
    },
    "rp2040": {
        "Micro-controller Architecture": "Raspberry Pi RP2040/RP2350",
        "Processor model": "rp2040",
        "Bootloader offset": "16KiB bootloader",
        "Communication interface": "USB  (or CAN bus on GPIO4/5 for SB2040)",
    },
}

PRELUDE = (
    "menuconfig is about to launch in your terminal.\n\n"
    "Save and exit (Q → Y) when done; the resulting [bold].config[/bold] is "
    "written to the path shown below — your inventory entry already points at it.\n\n"
    "Required for Katapult: pick a [bold]Bootloader offset[/bold] that matches "
    "the Katapult build flashed to this MCU. Mismatched offsets will brick the "
    "boot chain and require recovery via DFU/picoboot."
)


def _hint_for(family: str | None) -> dict[str, str] | None:
    if not family:
        return None
    return HINTS.get(family.lower())


def configure_board(inv: Inventory, board: Board, *, force: bool) -> Path:
    config_path = board.klipper_config.expanduser().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    exists = config_path.exists()
    if exists and not force:
        if not click.confirm(
            f"[{board.name}] config already exists at {config_path}; edit it?",
            default=True,
        ):
            logger.info("[{}] skipped", board.name)
            return config_path

    hint = _hint_for(board.mcu_family)
    body = [PRELUDE, "", f"[bold]Board:[/bold]   {board.name}",
            f"[bold]Repo:[/bold]    {inv.klipper_repo}  ({inv.repo_kind})",
            f"[bold]Config:[/bold]  {config_path}",
            f"[bold]MCU:[/bold]     {board.mcu_family or 'unspecified'}",
            f"[bold]Transport:[/bold] {board.transport}"]
    if hint:
        body.append("")
        body.append("[bold]Suggested menuconfig answers:[/bold]")
        for k, v in hint.items():
            body.append(f"  • {k}: [cyan]{v}[/cyan]")
    else:
        body.append("")
        body.append(
            "[yellow]No hints for this mcu_family.[/yellow] See the Klipper docs "
            "or your board vendor for the correct bootloader offset."
        )
    console.print(Panel("\n".join(body), title="Katapult-helper: menuconfig walkthrough"))

    if not click.confirm("Launch menuconfig now?", default=True):
        raise click.Abort()

    env = os.environ.copy()
    env["KCONFIG_CONFIG"] = str(config_path)
    logger.info("$ make menuconfig KCONFIG_CONFIG={} (cwd={})", config_path, inv.klipper_repo)
    subprocess.run(["make", "menuconfig"], cwd=inv.klipper_repo, env=env, check=True)

    if not config_path.exists():
        raise FileNotFoundError(
            f"menuconfig exited but {config_path} was not created — did you save?"
        )
    logger.success("[{}] wrote {}", board.name, config_path)
    return config_path


def configure_all_missing(inv: Inventory) -> list[Path]:
    """Walk the user through every board whose .config does not yet exist."""
    todo = [b for b in inv.boards.values() if not b.klipper_config.expanduser().exists()]
    if not todo:
        logger.info("all inventory boards already have config files")
        return []
    logger.info("{} board(s) need configs: {}", len(todo), ", ".join(b.name for b in todo))
    written: list[Path] = []
    for board in todo:
        written.append(configure_board(inv, board, force=True))
    return written


def ensure_make_available(inv: Inventory) -> None:
    if not (inv.klipper_repo / "Makefile").exists():
        raise click.UsageError(
            f"klipper_repo {inv.klipper_repo} has no Makefile — check inventory.yaml"
        )
    if shutil.which("make") is None:
        raise click.UsageError("`make` not found on PATH; install build-essential")
