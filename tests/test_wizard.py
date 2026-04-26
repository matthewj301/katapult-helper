from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from katapult_helper import wizard
from katapult_helper.discover import DiscoveredCan, DiscoveredUsb
from katapult_helper.inventory import load_inventory

EMPTY_YAML = """\
klipper_repo: /tmp/klipper
katapult_repo: /tmp/katapult
boards: {}
"""


def _seed(tmp_path: Path) -> Path:
    p = tmp_path / "inv.yaml"
    p.write_text(EMPTY_YAML)
    return p


def test_reconcile_no_devices_returns_same_inventory(tmp_path: Path) -> None:
    p = _seed(tmp_path)
    inv = load_inventory(p)
    with (
        patch.object(wizard, "discover_usb", return_value=[]),
        patch.object(wizard, "discover_can", return_value=[]),
    ):
        out = wizard._reconcile_inventory(p, inv)
    assert out is inv  # untouched in-memory inventory returned
    assert p.read_text() == EMPTY_YAML  # file untouched


def test_reconcile_adds_new_usb_device(tmp_path: Path) -> None:
    p = _seed(tmp_path)
    inv = load_inventory(p)
    discovered = DiscoveredUsb(
        by_id=Path("/dev/serial/by-id/usb-Klipper_stm32f446xx_AAAA-if00"),
        product="Klipper",
        mcu_family="stm32f446xx",
        chip_uid="AAAA",
    )
    answers = iter(["voron-mainboard", "/tmp/voron.config"])
    with (
        patch.object(wizard, "discover_usb", return_value=[discovered]),
        patch.object(wizard, "discover_can", return_value=[]),
        patch.object(wizard.Confirm, "ask", side_effect=lambda *a, **kw: True),
        patch.object(wizard.Prompt, "ask", side_effect=lambda *a, **kw: next(answers)),
    ):
        out = wizard._reconcile_inventory(p, inv)
    assert "voron-mainboard" in out.boards
    assert out.boards["voron-mainboard"].chip_uid == "AAAA"
    assert out is not inv  # rebuilt
    persisted = load_inventory(p)
    assert "voron-mainboard" in persisted.boards


def test_reconcile_skips_known_uid(tmp_path: Path) -> None:
    p = tmp_path / "inv.yaml"
    p.write_text(
        "klipper_repo: /tmp/klipper\n"
        "boards:\n"
        "  existing:\n"
        "    transport: usb\n"
        "    klipper_config: /tmp/x.config\n"
        "    chip_uid: KNOWN\n"
    )
    inv = load_inventory(p)
    discovered = DiscoveredUsb(
        by_id=Path("/dev/serial/by-id/usb-Klipper_stm32_KNOWN-if00"),
        product="Klipper",
        mcu_family="stm32",
        chip_uid="KNOWN",
    )
    confirm_calls: list = []
    with (
        patch.object(wizard, "discover_usb", return_value=[discovered]),
        patch.object(wizard, "discover_can", return_value=[]),
        patch.object(wizard.Confirm, "ask",
                     side_effect=lambda *a, **kw: confirm_calls.append(a) or True),
    ):
        out = wizard._reconcile_inventory(p, inv)
    assert out is inv
    assert confirm_calls == []  # never prompted to add the known device


def test_reconcile_adds_new_can_device(tmp_path: Path) -> None:
    p = _seed(tmp_path)
    inv = load_inventory(p)
    discovered = DiscoveredCan(uuid="1586f2c37eaf", application="Katapult", iface="can0")
    answers = iter(["voron-ebb36", "stm32g0b1xx", "/tmp/ebb36.config"])
    with (
        patch.object(wizard, "discover_usb", return_value=[]),
        patch.object(wizard, "discover_can", return_value=[discovered]),
        patch.object(wizard.Confirm, "ask", side_effect=lambda *a, **kw: True),
        patch.object(wizard.Prompt, "ask", side_effect=lambda *a, **kw: next(answers)),
    ):
        out = wizard._reconcile_inventory(p, inv)
    assert "voron-ebb36" in out.boards
    b = out.boards["voron-ebb36"]
    assert b.canbus_uuid == "1586f2c37eaf"
    assert b.transport == "can"
    assert b.mcu_family == "stm32g0b1xx"


def test_ask_nonempty_reprompts_on_empty(tmp_path: Path) -> None:
    answers = iter(["", "  ", "real-name"])
    with patch.object(wizard.Prompt, "ask", side_effect=lambda *a, **kw: next(answers)):
        result = wizard._ask_nonempty("Name", "name", default="suggested")
    assert result == "real-name"
