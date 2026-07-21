from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import click
import pytest

from katapult_helper import flash
from katapult_helper.inventory import Board, Inventory


def _make_by_id(tmp_path: Path, names: list[str]) -> Path:
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    for n in names:
        (by_id / n).write_text("")
    return by_id


def test_resolve_usb_path_returns_none_when_dir_missing(tmp_path: Path) -> None:
    with patch.object(flash, "BY_ID", tmp_path / "does-not-exist"):
        assert flash.resolve_usb_path("DEAD") is None


def test_resolve_usb_path_returns_none_when_no_match(tmp_path: Path) -> None:
    by_id = _make_by_id(tmp_path, ["usb-Klipper_stm32f446xx_AAAA-if00"])
    with patch.object(flash, "BY_ID", by_id):
        assert flash.resolve_usb_path("DEAD") is None


def test_resolve_usb_path_prefers_if00(tmp_path: Path) -> None:
    uid = "310031000C51303032383431"
    by_id = _make_by_id(tmp_path, [
        f"usb-Klipper_stm32f446xx_{uid}-if02",
        f"usb-Klipper_stm32f446xx_{uid}-if00",
        f"usb-Klipper_stm32f446xx_{uid}-if01",
    ])
    with patch.object(flash, "BY_ID", by_id):
        result = flash.resolve_usb_path(uid)
    assert result is not None
    assert result.name.endswith("-if00")


def test_resolve_usb_path_finds_katapult_mode(tmp_path: Path) -> None:
    uid = "3F00250011504B5735313220"
    by_id = _make_by_id(tmp_path, [f"usb-katapult_stm32g0b1xx_{uid}-if00"])
    with patch.object(flash, "BY_ID", by_id):
        result = flash.resolve_usb_path(uid)
    assert result is not None
    assert "katapult" in result.name.lower()


def test_resolve_usb_path_finds_klipper_mode_for_same_uid(tmp_path: Path) -> None:
    """The chip UID is stable across bootloader/app modes — so we must find the device
    whether it's currently in Katapult or Klipper. This is the core invariant."""
    uid = "310031000C51303032383431"
    by_id = _make_by_id(tmp_path, [f"usb-Klipper_stm32f446xx_{uid}-if00"])
    with patch.object(flash, "BY_ID", by_id):
        result = flash.resolve_usb_path(uid)
    assert result is not None
    assert uid in result.name


def test_resolve_usb_path_falls_back_to_first_match_when_no_if00(tmp_path: Path) -> None:
    uid = "DEADBEEF"
    by_id = _make_by_id(tmp_path, [
        f"usb-Klipper_stm32f446xx_{uid}-if02",
        f"usb-Klipper_stm32f446xx_{uid}-if03",
    ])
    with patch.object(flash, "BY_ID", by_id):
        result = flash.resolve_usb_path(uid)
    assert result is not None
    assert uid in result.name


def test_is_katapult_mode_via_proc() -> None:
    from katapult_helper._proc import is_katapult_mode
    assert is_katapult_mode(Path("/dev/serial/by-id/usb-katapult_stm32f446xx_AAA-if00"))
    assert not is_katapult_mode(Path("/dev/serial/by-id/usb-Klipper_stm32f446xx_AAA-if00"))


def test_flash_translates_called_process_error_to_clickexception(tmp_path: Path) -> None:
    """When flashtool.py exits non-zero, users should see a friendly multi-line
    hint (covering klipper-still-holding-device, missing by-id, CAN UUID
    mismatch) — not a CalledProcessError stack trace."""
    inv = Inventory(klipper_repo=tmp_path / "klipper", katapult_repo=tmp_path / "katapult")
    board = Board(
        name="t", transport="usb",
        klipper_config=tmp_path / "x.config", chip_uid="DEADBEEF",
    )
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    (by_id / "usb-katapult_stm32_DEADBEEF-if00").write_text("")
    fake_err = subprocess.CalledProcessError(1, ["python3", "flashtool.py"])
    with (
        patch.object(flash, "BY_ID", by_id),
        patch.object(flash, "run", side_effect=fake_err),
        pytest.raises(click.ClickException) as exc_info,
    ):
        flash.flash_board(inv, board, tmp_path / "klipper.bin")
    msg = exc_info.value.message
    assert "device already in use" in msg
    assert "klipper.service" in msg


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_app_start_extracts_offset() -> None:
    stdout = (FIXTURES / "flashtool_s_katapult_ebb36.txt").read_text()
    assert flash.parse_app_start(stdout) == 0x8002000


def test_parse_app_start_returns_none_when_absent() -> None:
    assert flash.parse_app_start("") is None
    assert flash.parse_app_start("Some random output\nWithout the marker\n") is None


def test_parse_app_start_tolerates_short_address_with_space_padding() -> None:
    # Python's `:4X` left-pads with spaces, not zeros. STM32 addresses are
    # always >= 0x08000000 so this won't happen in practice, but defense.
    assert flash.parse_app_start("Application Start: 0x 800\n") == 0x800
    assert flash.parse_app_start("Application Start: 0x   8\n") == 0x8


def test_verify_app_start_match_aborts_on_mismatch() -> None:
    board = Board(
        name="t", transport="usb",
        klipper_config=Path("/tmp/x.config"), chip_uid="AAA",
    )
    stdout = "Application Start: 0x8001000\n"
    with pytest.raises(click.ClickException) as exc_info:
        flash.verify_app_start_match(board, 0x8002000, stdout)
    msg = exc_info.value.message
    assert "BOOTLOADER OFFSET MISMATCH" in msg
    assert "0x8001000" in msg
    assert "0x8002000" in msg


def test_verify_app_start_match_passes_on_match() -> None:
    board = Board(
        name="t", transport="usb",
        klipper_config=Path("/tmp/x.config"), chip_uid="AAA",
    )
    stdout = "Application Start: 0x8002000\n"
    flash.verify_app_start_match(board, 0x8002000, stdout)  # must not raise


def test_verify_app_start_match_skips_when_unparseable() -> None:
    board = Board(
        name="t", transport="usb",
        klipper_config=Path("/tmp/x.config"), chip_uid="AAA",
    )
    flash.verify_app_start_match(board, 0x8002000, "no marker line here\n")  # must not raise


def _write_config(path: Path, app_addr: int) -> None:
    path.write_text(f"CONFIG_FLASH_APPLICATION_ADDRESS=0x{app_addr:X}\n")


def test_preflight_aborts_on_offset_mismatch(tmp_path: Path) -> None:
    """Integration test: build offset 0x8002000, but chip reports 0x8001000.
    flash_board must abort with ClickException, not flash."""
    cfg = tmp_path / "board.config"
    _write_config(cfg, 0x8002000)
    by_id = _make_by_id(tmp_path, ["usb-katapult_stm32_AAA-if00"])
    inv = Inventory(klipper_repo=tmp_path / "klipper", katapult_repo=tmp_path / "katapult")
    board = Board(name="x", transport="usb", klipper_config=cfg, chip_uid="AAA")

    def fake_query(inv_, board_):
        return "Application Start: 0x8001000\n"

    with (
        patch.object(flash, "BY_ID", by_id),
        patch.object(flash, "query_katapult_status", side_effect=fake_query),
        patch.object(flash, "run") as mock_run,
        pytest.raises(click.ClickException, match="BOOTLOADER OFFSET MISMATCH"),
    ):
        flash.flash_board(inv, board, tmp_path / "klipper.bin")
    assert not mock_run.called  # never reached the actual flashtool.py -f


def test_preflight_passes_when_offsets_match(tmp_path: Path) -> None:
    cfg = tmp_path / "board.config"
    _write_config(cfg, 0x8002000)
    by_id = _make_by_id(tmp_path, ["usb-katapult_stm32_AAA-if00"])
    fw = tmp_path / "klipper.bin"
    fw.write_text("")
    inv = Inventory(klipper_repo=tmp_path / "klipper", katapult_repo=tmp_path / "katapult")
    board = Board(name="x", transport="usb", klipper_config=cfg, chip_uid="AAA")

    with (
        patch.object(flash, "BY_ID", by_id),
        patch.object(flash, "query_katapult_status", return_value="Application Start: 0x8002000\n"),
        patch.object(flash, "run"),  # flashtool.py invocation no-op
    ):
        flash.flash_board(inv, board, fw)  # must not raise


def test_force_bypasses_offset_mismatch(tmp_path: Path) -> None:
    cfg = tmp_path / "board.config"
    _write_config(cfg, 0x8002000)
    by_id = _make_by_id(tmp_path, ["usb-katapult_stm32_AAA-if00"])
    fw = tmp_path / "klipper.bin"
    fw.write_text("")
    inv = Inventory(klipper_repo=tmp_path / "klipper", katapult_repo=tmp_path / "katapult")
    board = Board(name="x", transport="usb", klipper_config=cfg, chip_uid="AAA")

    with (
        patch.object(flash, "BY_ID", by_id),
        patch.object(flash, "query_katapult_status", return_value="Application Start: 0x8001000\n"),
        patch.object(flash, "run") as mock_run,
    ):
        flash.flash_board(inv, board, fw, force=True)  # must not raise
    assert mock_run.called  # we bypassed and proceeded to flash


def test_can_preflight_aborts_when_no_katapult_response(tmp_path: Path) -> None:
    """CAN board in Klipper-app mode whose -r request appears to succeed but
    the follow-up -s still returns nothing. Without --force, abort."""
    cfg = tmp_path / "board.config"
    _write_config(cfg, 0x8002000)
    inv = Inventory(klipper_repo=tmp_path / "klipper", katapult_repo=tmp_path / "katapult")
    board = Board(
        name="x", transport="can", klipper_config=cfg,
        canbus_uuid="abc123def456", can_iface="can0",
    )

    with (
        patch.object(flash, "query_katapult_status", return_value=None),
        patch.object(flash, "_request_can_bootloader", return_value=True),
        patch.object(flash, "run"),
        pytest.raises(click.ClickException, match="no Katapult response"),
    ):
        flash.flash_board(inv, board, tmp_path / "klipper.bin")


def test_flash_missing_chip_uid_yields_friendly_error(tmp_path: Path) -> None:
    inv = Inventory(klipper_repo=tmp_path / "klipper", katapult_repo=tmp_path / "katapult")
    board = Board(
        name="ghost", transport="usb",
        klipper_config=tmp_path / "x.config", chip_uid="ABSENT",
    )
    empty = tmp_path / "by-id-empty"
    empty.mkdir()
    with patch.object(flash, "BY_ID", empty):
        with pytest.raises(click.ClickException) as exc_info:
            flash.flash_board(inv, board, tmp_path / "klipper.bin")
    assert "discover" in exc_info.value.message
    assert "ABSENT" in exc_info.value.message


# --- guardrails: exact-identity resolution -------------------------------------

def test_resolve_usb_path_requires_exact_uid_not_substring(tmp_path: Path) -> None:
    """The cross-flash bug: a substring glob let one UID resolve to another
    device whose name merely contained it. Resolution must be exact."""
    by_id = _make_by_id(tmp_path, ["usb-Klipper_stm32f446xx_AAAA-if00"])
    with patch.object(flash, "BY_ID", by_id):
        assert flash.resolve_usb_path("AAA") is None      # substring must NOT match
        assert flash.resolve_usb_path("AAAA") is not None  # exact still works


def test_resolve_usb_path_does_not_cross_resolve_to_superstring_uid(tmp_path: Path) -> None:
    """Board A's UID is a prefix of board B's. Resolving A must pick ONLY A —
    this is the exact scenario that flashed firmware onto the wrong board."""
    by_id = _make_by_id(tmp_path, [
        "usb-Klipper_stm32f446xx_AAAA-if00",
        "usb-Klipper_stm32g0b1xx_AAAABBBB-if00",
    ])
    with patch.object(flash, "BY_ID", by_id):
        res = flash.resolve_usb_path("AAAA")
    assert res is not None
    assert res.name.endswith("AAAA-if00")


def test_resolve_usb_path_raises_on_uid_collision_across_mcus(tmp_path: Path) -> None:
    """Two boards sharing a non-unique USB serial but on different MCUs: we must
    refuse rather than silently pick one."""
    uid = "0000000000000000"
    by_id = _make_by_id(tmp_path, [
        f"usb-katapult_stm32f042x6_{uid}-if00",
        f"usb-katapult_stm32g0b1xx_{uid}-if00",
    ])
    with patch.object(flash, "BY_ID", by_id):
        with pytest.raises(flash.AmbiguousDeviceError):
            flash.resolve_usb_path(uid)


def test_resolve_usb_path_uid_match_is_case_insensitive(tmp_path: Path) -> None:
    by_id = _make_by_id(tmp_path, ["usb-katapult_stm32g0b1xx_DEADBEEF-if00"])
    with patch.object(flash, "BY_ID", by_id):
        assert flash.resolve_usb_path("deadbeef") is not None


# --- guardrails: per-flash explanation + approval ------------------------------

def _usb_board(tmp_path: Path, uid: str = "DEADBEEF") -> Board:
    return Board(
        name="toolhead", transport="usb",
        klipper_config=tmp_path / "toolhead.config", chip_uid=uid,
    )


def test_confirm_flash_assume_yes_returns_true_without_prompting(tmp_path: Path) -> None:
    fw = tmp_path / "klipper.bin"
    fw.write_text("x")
    with (
        patch.object(flash, "BY_ID", tmp_path / "noexist"),
        patch.object(flash.Confirm, "ask") as ask,
    ):
        assert flash.confirm_flash(_usb_board(tmp_path), fw, assume_yes=True) is True
    ask.assert_not_called()


def test_confirm_flash_declined_returns_false(tmp_path: Path) -> None:
    fw = tmp_path / "klipper.bin"
    fw.write_text("x")
    with (
        patch.object(flash, "BY_ID", tmp_path / "noexist"),
        patch.object(flash.Confirm, "ask", return_value=False) as ask,
    ):
        assert flash.confirm_flash(_usb_board(tmp_path), fw, assume_yes=False) is False
    ask.assert_called_once()


def test_describe_flash_shows_resolved_device_mode_and_firmware(tmp_path: Path) -> None:
    uid = "DEADBEEF"
    by_id = _make_by_id(tmp_path, [f"usb-katapult_stm32g0b1xx_{uid}-if00"])
    fw = tmp_path / "klipper.bin"
    fw.write_text("x" * 10)
    with patch.object(flash, "BY_ID", by_id):
        text = flash.describe_flash(_usb_board(tmp_path, uid), fw)
    assert uid in text            # exact chip is named
    assert "Katapult" in text     # current mode is surfaced
    assert str(fw) in text        # the firmware path is shown


def test_confirm_flash_ambiguous_device_propagates(tmp_path: Path) -> None:
    """Ambiguity must surface at confirmation time — before we ever prompt or
    write — not be swallowed."""
    uid = "0000000000000000"
    by_id = _make_by_id(tmp_path, [
        f"usb-katapult_stm32f042x6_{uid}-if00",
        f"usb-katapult_stm32g0b1xx_{uid}-if00",
    ])
    fw = tmp_path / "klipper.bin"
    fw.write_text("x")
    with patch.object(flash, "BY_ID", by_id):
        with pytest.raises(flash.AmbiguousDeviceError):
            flash.confirm_flash(_usb_board(tmp_path, uid), fw, assume_yes=True)
