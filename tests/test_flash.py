from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from katapult_helper import flash


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
