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
