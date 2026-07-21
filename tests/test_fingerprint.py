from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from katapult_helper import fingerprint
from katapult_helper.inventory import Board, Inventory, load_inventory


def _inv(tmp_path: Path) -> Inventory:
    return Inventory(klipper_repo=tmp_path / "klipper", katapult_repo=tmp_path / "katapult")


def _board(tmp_path: Path, cfg_body: str = "CONFIG_FLASH_APPLICATION_ADDRESS=0x8002000\n") -> Board:
    cfg = tmp_path / "board.config"
    cfg.write_text(cfg_body)
    return Board(name="b", transport="usb", klipper_config=cfg, chip_uid="DEADBEEF")


def test_fingerprint_is_deterministic(tmp_path: Path) -> None:
    inv, board = _inv(tmp_path), _board(tmp_path)
    with patch.object(fingerprint, "_klipper_rev", return_value="abc123"):
        fp1 = fingerprint.compute_fingerprint(inv, board)
        fp2 = fingerprint.compute_fingerprint(inv, board)
    assert fp1 is not None and fp1 == fp2


def test_fingerprint_changes_when_config_changes(tmp_path: Path) -> None:
    inv, board = _inv(tmp_path), _board(tmp_path)
    with patch.object(fingerprint, "_klipper_rev", return_value="abc123"):
        fp_before = fingerprint.compute_fingerprint(inv, board)
        board.klipper_config.write_text("CONFIG_FLASH_APPLICATION_ADDRESS=0x8008000\n")
        fp_after = fingerprint.compute_fingerprint(inv, board)
    assert fp_before != fp_after


def test_fingerprint_changes_when_rev_changes(tmp_path: Path) -> None:
    inv, board = _inv(tmp_path), _board(tmp_path)
    with patch.object(fingerprint, "_klipper_rev", return_value="rev-one"):
        fp1 = fingerprint.compute_fingerprint(inv, board)
    with patch.object(fingerprint, "_klipper_rev", return_value="rev-two"):
        fp2 = fingerprint.compute_fingerprint(inv, board)
    assert fp1 != fp2


def test_fingerprint_none_without_git_rev(tmp_path: Path) -> None:
    inv, board = _inv(tmp_path), _board(tmp_path)
    with patch.object(fingerprint, "_klipper_rev", return_value=None):
        assert fingerprint.compute_fingerprint(inv, board) is None


def test_fingerprint_none_without_config(tmp_path: Path) -> None:
    inv = _inv(tmp_path)
    board = Board(name="b", transport="usb",
                  klipper_config=tmp_path / "missing.config", chip_uid="DEADBEEF")
    with patch.object(fingerprint, "_klipper_rev", return_value="abc123"):
        assert fingerprint.compute_fingerprint(inv, board) is None


def test_is_up_to_date_true_only_when_fingerprint_matches(tmp_path: Path) -> None:
    inv, board = _inv(tmp_path), _board(tmp_path)
    with patch.object(fingerprint, "_klipper_rev", return_value="abc123"):
        fp = fingerprint.compute_fingerprint(inv, board)
        board.last_flashed = fp
        assert fingerprint.is_up_to_date(inv, board) == (True, fp)
        board.last_flashed = "stale"
        up, cur = fingerprint.is_up_to_date(inv, board)
    assert up is False and cur == fp


def test_is_up_to_date_false_when_fingerprint_unknown(tmp_path: Path) -> None:
    """No git rev => cannot know => must NOT skip (fail safe)."""
    inv, board = _inv(tmp_path), _board(tmp_path)
    board.last_flashed = "whatever"
    with patch.object(fingerprint, "_klipper_rev", return_value=None):
        assert fingerprint.is_up_to_date(inv, board) == (False, None)


def test_record_flash_fingerprint_persists_and_updates_memory(tmp_path: Path) -> None:
    p = tmp_path / "inv.yaml"
    cfg = tmp_path / "board.config"
    cfg.write_text("CONFIG_FLASH_APPLICATION_ADDRESS=0x8002000\n")
    p.write_text(
        "klipper_repo: {repo}\n"
        "boards:\n"
        "  b:\n"
        "    transport: usb\n"
        "    chip_uid: DEADBEEF\n"
        "    klipper_config: {cfg}\n".format(repo=tmp_path / "klipper", cfg=cfg)
    )
    inv = load_inventory(p)
    board = inv.boards["b"]
    with patch.object(fingerprint, "_klipper_rev", return_value="abc123"):
        fingerprint.record_flash_fingerprint(p, inv, board)
        expected = fingerprint.compute_fingerprint(inv, board)
    assert board.last_flashed == expected           # in-memory updated
    assert load_inventory(p).boards["b"].last_flashed == expected  # persisted


def test_last_flashed_round_trips_through_load(tmp_path: Path) -> None:
    p = tmp_path / "inv.yaml"
    p.write_text(
        "klipper_repo: /tmp/klipper\n"
        "boards:\n"
        "  b:\n"
        "    transport: usb\n"
        "    chip_uid: DEADBEEF\n"
        "    klipper_config: /tmp/b.config\n"
        "    last_flashed: cafebabe\n"
    )
    assert load_inventory(p).boards["b"].last_flashed == "cafebabe"
