from __future__ import annotations

from pathlib import Path

import pytest

from katapult_helper.inventory import (
    Board,
    InventoryError,
    build_inventory,
    load_inventory,
    load_raw,
    save_raw,
    upsert_board,
)

USB_YAML = """\
klipper_repo: ~/git/kalico
katapult_repo: ~/katapult

boards:
  doomcube-octopus:
    transport: usb
    chip_uid: 430031000D51313339373836
    mcu_family: stm32h723xx
    klipper_config: ~/printer_data/firmware_configs/doomcube_octopus.config
"""


def test_load_inventory_usb(tmp_path: Path) -> None:
    p = tmp_path / "inv.yaml"
    p.write_text(USB_YAML)
    inv = load_inventory(p)
    assert inv.repo_kind == "Kalico"
    assert inv.klipper_bin == Path("~/git/kalico").expanduser() / "out" / "klipper.bin"
    assert "doomcube-octopus" in inv.boards
    b = inv.boards["doomcube-octopus"]
    assert b.transport == "usb"
    assert b.chip_uid == "430031000D51313339373836"
    assert b.mcu_family == "stm32h723xx"


def test_repo_kind_klipper(tmp_path: Path) -> None:
    p = tmp_path / "inv.yaml"
    p.write_text(USB_YAML.replace("kalico", "klipper"))
    assert load_inventory(p).repo_kind == "Klipper"


def test_board_post_init_rejects_missing_chip_uid() -> None:
    with pytest.raises(ValueError, match="usb transport requires chip_uid"):
        Board(name="x", transport="usb", klipper_config=Path("/tmp/x.config"))


def test_board_post_init_rejects_missing_canbus_uuid() -> None:
    with pytest.raises(ValueError, match="can transport requires canbus_uuid"):
        Board(name="x", transport="can", klipper_config=Path("/tmp/x.config"))


def test_upsert_inserts_new_board(tmp_path: Path) -> None:
    p = tmp_path / "inv.yaml"
    p.write_text(USB_YAML)
    raw = load_raw(p)
    changed = upsert_board(
        raw, "voron-ebb36",
        transport="can",
        klipper_config="~/configs/voron_ebb36.config",
        mcu_family="stm32g0b1xx",
        can_iface="can0",
        canbus_uuid="1586f2c37eaf",
    )
    assert changed is True
    save_raw(p, raw)
    inv = load_inventory(p)
    assert "voron-ebb36" in inv.boards
    assert inv.boards["voron-ebb36"].canbus_uuid == "1586f2c37eaf"


def test_upsert_returns_false_for_identical_entry(tmp_path: Path) -> None:
    p = tmp_path / "inv.yaml"
    p.write_text(USB_YAML)
    raw = load_raw(p)
    changed = upsert_board(
        raw, "doomcube-octopus",
        transport="usb",
        klipper_config="~/printer_data/firmware_configs/doomcube_octopus.config",
        chip_uid="430031000D51313339373836",
        mcu_family="stm32h723xx",
    )
    assert changed is False


def test_upsert_returns_true_when_field_changes(tmp_path: Path) -> None:
    p = tmp_path / "inv.yaml"
    p.write_text(USB_YAML)
    raw = load_raw(p)
    changed = upsert_board(
        raw, "doomcube-octopus",
        transport="usb",
        klipper_config="~/printer_data/firmware_configs/doomcube_octopus.config",
        chip_uid="430031000D51313339373836",
        mcu_family="stm32h723xx-revB",
    )
    assert changed is True


def test_upsert_rejects_empty_chip_uid(tmp_path: Path) -> None:
    raw = {"boards": {}}
    with pytest.raises(ValueError, match="usb transport requires a non-empty chip_uid"):
        upsert_board(raw, "x", transport="usb", klipper_config="x.config", chip_uid="")


def test_upsert_rejects_missing_canbus_uuid() -> None:
    raw = {"boards": {}}
    with pytest.raises(ValueError, match="can transport requires a non-empty canbus_uuid"):
        upsert_board(raw, "x", transport="can", klipper_config="x.config", canbus_uuid=None)


def test_save_raw_preserves_comments(tmp_path: Path) -> None:
    src = "# preserve me\n" + USB_YAML
    p = tmp_path / "inv.yaml"
    p.write_text(src)
    raw = load_raw(p)
    upsert_board(
        raw, "new-board",
        transport="usb",
        klipper_config="x.config",
        chip_uid="ABC123",
    )
    save_raw(p, raw)
    out = p.read_text()
    assert "# preserve me" in out
    assert "new-board" in out


def test_build_inventory_directly_from_dict() -> None:
    raw = {
        "klipper_repo": "/tmp/klipper",
        "katapult_repo": "/tmp/katapult",
        "boards": {
            "x": {
                "transport": "usb",
                "klipper_config": "/tmp/x.config",
                "chip_uid": "deadbeef",
            }
        },
    }
    inv = build_inventory(raw)
    assert inv.boards["x"].chip_uid == "deadbeef"


def test_build_inventory_coerces_unquoted_numeric_chip_uid() -> None:
    """If a YAML chip_uid happens to be all digits, ruamel may load it as int.
    build_inventory must coerce it back to str so Board / by-id lookup works."""
    raw = {
        "klipper_repo": "/tmp/klipper",
        "boards": {
            "x": {
                "transport": "usb",
                "klipper_config": "/tmp/x.config",
                "chip_uid": 1234567890,
            }
        },
    }
    inv = build_inventory(raw)
    assert inv.boards["x"].chip_uid == "1234567890"
    assert isinstance(inv.boards["x"].chip_uid, str)


def test_build_inventory_treats_empty_string_as_missing() -> None:
    """Legacy YAML written by the old upsert_board could contain mcu_family: ''.
    Empty strings should be normalized to None so Board doesn't carry junk."""
    raw = {
        "klipper_repo": "/tmp/klipper",
        "boards": {
            "x": {
                "transport": "usb",
                "klipper_config": "/tmp/x.config",
                "chip_uid": "AAAA",
                "mcu_family": "",
            }
        },
    }
    inv = build_inventory(raw)
    assert inv.boards["x"].mcu_family is None


def test_build_inventory_missing_klipper_repo_raises_inventory_error() -> None:
    with pytest.raises(InventoryError, match="`klipper_repo`"):
        build_inventory({"boards": {}})


def test_build_inventory_non_dict_raises_inventory_error() -> None:
    with pytest.raises(InventoryError):
        build_inventory("not a dict")  # type: ignore[arg-type]


def test_build_inventory_empty_yaml_raises_inventory_error() -> None:
    """Empty file or all-comments YAML loads as None via ruamel."""
    with pytest.raises(InventoryError):
        build_inventory(None)  # type: ignore[arg-type]


def test_build_inventory_null_klipper_repo_raises_inventory_error() -> None:
    """`klipper_repo: ~` (or bare `klipper_repo:`) loads as {'klipper_repo': None}.
    Must be caught — silently producing Path('None') would create a confusing error
    later when make/flashtool look for the directory."""
    with pytest.raises(InventoryError, match="non-empty top-level `klipper_repo`"):
        build_inventory({"klipper_repo": None})


def test_build_inventory_empty_string_klipper_repo_raises_inventory_error() -> None:
    with pytest.raises(InventoryError, match="non-empty top-level `klipper_repo`"):
        build_inventory({"klipper_repo": ""})


def test_upsert_strips_legacy_empty_mcu_family(tmp_path: Path) -> None:
    """An on-disk entry with mcu_family: '' should be rewritten without that field
    when the wizard re-upserts it with a real (or absent) mcu_family."""
    legacy = (
        "klipper_repo: /tmp/klipper\n"
        "boards:\n"
        "  x:\n"
        "    transport: usb\n"
        "    klipper_config: /tmp/x.config\n"
        "    chip_uid: AAAA\n"
        "    mcu_family: ''\n"
    )
    p = tmp_path / "inv.yaml"
    p.write_text(legacy)
    raw = load_raw(p)
    changed = upsert_board(
        raw, "x",
        transport="usb",
        klipper_config="/tmp/x.config",
        chip_uid="AAAA",
        mcu_family="stm32g0b1xx",
    )
    assert changed is True
    save_raw(p, raw)
    rt = load_raw(p)
    assert rt["boards"]["x"]["mcu_family"] == "stm32g0b1xx"
