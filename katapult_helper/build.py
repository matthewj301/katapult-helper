from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

from ._proc import run
from .inventory import Board, Inventory


def build_board(inv: Inventory, board: Board, *, run_menuconfig: bool) -> Path:
    repo = inv.klipper_repo
    config_path = board.klipper_config.expanduser().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    env = {"KCONFIG_CONFIG": str(config_path)}
    logger.info("[{}] building in {} ({})", board.name, repo, inv.repo_kind)
    logger.info("[{}] using config {}", board.name, config_path)

    run(["make", "clean"], cwd=repo, env=env)

    cfg_exists = config_path.exists()
    if run_menuconfig or not cfg_exists:
        if not cfg_exists:
            logger.warning("[{}] config not found; menuconfig will create it", board.name)
        run(["make", "menuconfig"], cwd=repo, env=env)
    else:
        run(["make", "olddefconfig"], cwd=repo, env=env)

    run(["make", f"-j{os.cpu_count() or 1}"], cwd=repo, env=env)

    binary = inv.klipper_bin
    if not binary.exists():
        raise FileNotFoundError(f"build produced no {binary}")
    logger.success("[{}] built {} ({} bytes)", board.name, binary, binary.stat().st_size)
    return binary
