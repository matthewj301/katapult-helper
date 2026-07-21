from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from katapult_helper.cli import cli


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "inventory.yaml"
    p.write_text(content)
    return p


def test_list_on_malformed_inventory_emits_usage_error_not_traceback(tmp_path: Path) -> None:
    p = _write(tmp_path, "boards: {}\n")  # missing klipper_repo
    result = CliRunner().invoke(cli, ["-c", str(p), "list"])
    assert result.exit_code != 0
    assert "klipper_repo" in result.output
    assert "Traceback" not in result.output
    assert "InventoryError" not in result.output


def test_list_on_empty_inventory_yaml_emits_usage_error(tmp_path: Path) -> None:
    p = _write(tmp_path, "")
    result = CliRunner().invoke(cli, ["-c", str(p), "list"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_list_on_null_klipper_repo_emits_usage_error(tmp_path: Path) -> None:
    p = _write(tmp_path, "klipper_repo: ~\n")
    result = CliRunner().invoke(cli, ["-c", str(p), "list"])
    assert result.exit_code != 0
    assert "non-empty" in result.output
    assert "Traceback" not in result.output


def test_list_on_valid_inventory_succeeds(tmp_path: Path) -> None:
    p = _write(tmp_path, "klipper_repo: /tmp/klipper\nboards: {}\n")
    result = CliRunner().invoke(cli, ["-c", str(p), "list"])
    assert result.exit_code == 0, result.output


def test_wizard_on_malformed_inventory_emits_usage_error_not_traceback(tmp_path: Path) -> None:
    """Regression test for: wizard bypassed the _load helper and surfaced a
    raw stack trace on malformed YAML."""
    p = _write(tmp_path, "boards: {}\n")
    result = CliRunner().invoke(cli, ["-c", str(p), "wizard"], input="n\n")
    assert result.exit_code != 0
    assert "klipper_repo" in result.output
    assert "Traceback" not in result.output


def test_help_lists_all_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ("list", "discover", "configure", "build", "flash", "run", "wizard"):
        assert cmd in result.output


# --- flash approval gate -------------------------------------------------------

from contextlib import nullcontext
from unittest.mock import patch


def _usb_inventory(tmp_path: Path) -> Path:
    (tmp_path / "klipper").mkdir()
    return _write(
        tmp_path,
        "klipper_repo: {repo}\n"
        "boards:\n"
        "  toolhead:\n"
        "    transport: usb\n"
        "    chip_uid: DEADBEEF\n"
        "    klipper_config: {cfg}\n".format(
            repo=tmp_path / "klipper", cfg=tmp_path / "toolhead.config"
        ),
    )


def test_flash_declined_skips_board_and_does_not_write(tmp_path: Path) -> None:
    p = _usb_inventory(tmp_path)
    fw = tmp_path / "klipper.bin"
    fw.write_text("x")
    with (
        patch("katapult_helper.cli.klipper_stopped", return_value=nullcontext()),
        patch("katapult_helper.cli.confirm_flash", return_value=False) as cf,
        patch("katapult_helper.cli.flash_board") as fb,
    ):
        result = CliRunner().invoke(cli, ["-c", str(p), "flash", "-f", str(fw)])
    assert result.exit_code == 0, result.output
    cf.assert_called_once()
    fb.assert_not_called()


def test_flash_yes_forwards_assume_yes_and_flashes(tmp_path: Path) -> None:
    p = _usb_inventory(tmp_path)
    fw = tmp_path / "klipper.bin"
    fw.write_text("x")
    with (
        patch("katapult_helper.cli.klipper_stopped", return_value=nullcontext()),
        patch("katapult_helper.cli.confirm_flash", return_value=True) as cf,
        patch("katapult_helper.cli.flash_board") as fb,
    ):
        result = CliRunner().invoke(cli, ["-c", str(p), "flash", "-f", str(fw), "--yes"])
    assert result.exit_code == 0, result.output
    assert cf.call_args.kwargs.get("assume_yes") is True
    fb.assert_called_once()


# --- fingerprint skip (run) ----------------------------------------------------

def test_run_skips_up_to_date_board(tmp_path: Path) -> None:
    p = _usb_inventory(tmp_path)
    with (
        patch("katapult_helper.cli.klipper_stopped", return_value=nullcontext()),
        patch("katapult_helper.cli.is_up_to_date", return_value=(True, "abc123def456")),
        patch("katapult_helper.cli.build_board") as bb,
        patch("katapult_helper.cli.flash_board") as fb,
    ):
        result = CliRunner().invoke(cli, ["-c", str(p), "run"])
    assert result.exit_code == 0, result.output
    bb.assert_not_called()   # up to date => not even built
    fb.assert_not_called()


def test_run_all_flag_reflashes_up_to_date_board(tmp_path: Path) -> None:
    p = _usb_inventory(tmp_path)
    with (
        patch("katapult_helper.cli.klipper_stopped", return_value=nullcontext()),
        patch("katapult_helper.cli.is_up_to_date", return_value=(True, "abc123def456")),
        patch("katapult_helper.cli.build_board", return_value=tmp_path / "klipper.bin") as bb,
        patch("katapult_helper.cli.confirm_flash", return_value=True),
        patch("katapult_helper.cli.flash_board") as fb,
        patch("katapult_helper.cli.record_flash_fingerprint") as rec,
    ):
        result = CliRunner().invoke(cli, ["-c", str(p), "run", "--all"])
    assert result.exit_code == 0, result.output
    bb.assert_called_once()   # --all overrides the skip
    fb.assert_called_once()
    rec.assert_called_once()
