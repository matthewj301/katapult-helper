from __future__ import annotations

import os
import subprocess
from pathlib import Path

from loguru import logger

from .inventory import Board, Inventory


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    logger.info("$ {} (cwd={})", " ".join(cmd), cwd)
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    subprocess.run(cmd, cwd=cwd, env=full_env, check=True)


def build_board(inv: Inventory, board: Board, *, run_menuconfig: bool) -> Path:
    repo = inv.klipper_repo
    config_path = board.klipper_config.expanduser().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    env = {"KCONFIG_CONFIG": str(config_path)}
    logger.info("[{}] building in {} ({})", board.name, repo, inv.repo_kind)
    logger.info("[{}] using config {}", board.name, config_path)

    _run(["make", "clean"], cwd=repo, env=env)

    if run_menuconfig or not config_path.exists():
        if not config_path.exists():
            logger.warning("[{}] config not found; menuconfig will create it", board.name)
        _run(["make", "menuconfig"], cwd=repo, env=env)
    else:
        _run(["make", "olddefconfig"], cwd=repo, env=env)

    _run(["make", f"-j{os.cpu_count() or 1}"], cwd=repo, env=env)

    binary = inv.klipper_bin
    if not binary.exists():
        raise FileNotFoundError(f"build produced no {binary}")
    logger.success("[{}] built {} ({} bytes)", board.name, binary, binary.stat().st_size)
    return binary
