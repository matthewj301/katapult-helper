from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click
from loguru import logger

from ._proc import run
from .inventory import Board, Inventory
from .profiles import ProfileViolations, check_profile, get_profile


def warn_profile_violations(board: Board, config_path: Path) -> ProfileViolations | None:
    """Read .config, run profile checks, log results. Returns the violations
    object so callers (flash) can decide whether to abort. Returns None when no
    profile applies."""
    profile = get_profile(board.profile or board.mcu_family)
    if profile is None:
        return None
    if not config_path.exists():
        logger.debug("[{}] no .config yet; skipping profile check", board.name)
        return None
    v = check_profile(config_path.read_text(), profile)
    if not v.has_any:
        logger.success("[{}] .config matches profile '{}'", board.name, profile.name)
        return v
    logger.warning("[{}] profile '{}' violations:", board.name, profile.name)
    for key, expected in v.missing_required:
        logger.warning("  REQUIRED  {}={} (currently unset or wrong)", key, expected)
    for key, bad in v.set_incompatible:
        logger.warning("  INCOMPATIBLE  {}={} (will brick this hardware)", key, bad)
    for key, expected in v.missing_recommended:
        logger.warning("  recommended  {}={}", key, expected)
    return v


def _build_failed(board: Board, step: str, exc: subprocess.CalledProcessError) -> click.ClickException:
    return click.ClickException(
        f"[{board.name}] `{step}` failed (exit {exc.returncode}). See make output above.\n"
        f"  Common causes:\n"
        f"    - ROM overflow: firmware too large for the chip's free flash. Drop "
        f"features in `katapult-helper configure {board.name}` (menuconfig).\n"
        f"    - Bootloader offset mismatch: the offset in {board.klipper_config} "
        f"must match the Katapult build flashed to the MCU.\n"
        f"    - Toolchain missing: install `gcc-arm-none-eabi` (and `binutils-arm-none-eabi`)."
    )


def build_board(inv: Inventory, board: Board, *, run_menuconfig: bool) -> Path:
    repo = inv.klipper_repo
    config_path = board.klipper_config.expanduser().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    env = {"KCONFIG_CONFIG": str(config_path)}
    logger.info("[{}] building in {} ({})", board.name, repo, inv.repo_kind)
    logger.info("[{}] using config {}", board.name, config_path)

    try:
        run(["make", "clean"], cwd=repo, env=env)

        cfg_exists = config_path.exists()
        if run_menuconfig or not cfg_exists:
            if not cfg_exists:
                logger.warning("[{}] config not found; menuconfig will create it", board.name)
            run(["make", "menuconfig"], cwd=repo, env=env)
        else:
            run(["make", "olddefconfig"], cwd=repo, env=env)

        run(["make", f"-j{os.cpu_count() or 1}"], cwd=repo, env=env)
    except subprocess.CalledProcessError as e:
        step = " ".join(e.cmd) if isinstance(e.cmd, list) else str(e.cmd)
        raise _build_failed(board, step, e) from None

    binary = inv.klipper_bin
    if not binary.exists():
        raise click.ClickException(
            f"[{board.name}] build succeeded but {binary} does not exist — "
            f"check {repo}'s output layout"
        )
    warn_profile_violations(board, config_path)
    logger.success("[{}] built {} ({} bytes)", board.name, binary, binary.stat().st_size)
    return binary
