"""Per-board firmware fingerprints — the basis for skipping boards that are
already up to date.

A fingerprint is a deterministic hash of *what would be flashed* to a board:
the Klipper/Kalico source revision plus the board's `.config`. Same rev + same
config => same firmware => same fingerprint. `run` and `wizard` compare the
current fingerprint against the `last_flashed` value stored in inventory.yaml
and skip the build + flash when they match (unless `--all` is passed).

We deliberately do NOT hash the built `.bin`: that would require building
before we could decide to skip the build, and Klipper binaries aren't reliably
byte-reproducible across toolchains. The (rev, config) pair is the honest,
cheap predictor of the firmware.

`compute_fingerprint` returns None when it can't be computed reliably (repo is
not a git checkout, git is missing, or the .config doesn't exist yet). Callers
MUST treat None as "cannot know — do not skip; flash." Fail safe, never skip.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from loguru import logger

from .inventory import Board, Inventory, record_flash


def _klipper_rev(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    rev = result.stdout.strip()
    return rev or None


def compute_fingerprint(inv: Inventory, board: Board) -> str | None:
    """Fingerprint of the firmware that would be flashed to `board`, or None if
    it can't be computed reliably (missing git rev or missing .config)."""
    rev = _klipper_rev(inv.klipper_repo)
    if rev is None:
        logger.debug("[{}] no git rev for {}; cannot fingerprint (will not skip)",
                     board.name, inv.klipper_repo)
        return None
    cfg = board.klipper_config.expanduser()
    if not cfg.exists():
        return None
    h = hashlib.sha256()
    h.update(rev.encode())
    h.update(b"\0")
    h.update(hashlib.sha256(cfg.read_bytes()).hexdigest().encode())
    return h.hexdigest()


def is_up_to_date(inv: Inventory, board: Board) -> tuple[bool, str | None]:
    """Return (up_to_date, fingerprint). `up_to_date` is True only when the
    fingerprint is computable AND equals the board's recorded `last_flashed`.
    A None fingerprint always yields (False, None) — fail safe, flash."""
    fp = compute_fingerprint(inv, board)
    if fp is None:
        return (False, None)
    return (fp == board.last_flashed, fp)


def record_flash_fingerprint(inventory_path: Path, inv: Inventory, board: Board) -> None:
    """After a successful flash, persist the board's fingerprint so the next
    run/wizard can skip it. Recomputed here because the build may have mutated
    the .config (profile auto-fixes). Best-effort: a None fingerprint (no git
    rev) just means the board can't be skipped next time — never fatal. Also
    updates `board.last_flashed` in memory for the rest of this run."""
    fp = compute_fingerprint(inv, board)
    if fp is None:
        return
    record_flash(inventory_path, board.name, fp)
    board.last_flashed = fp
