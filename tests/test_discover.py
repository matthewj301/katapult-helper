from __future__ import annotations

from katapult_helper.discover import (
    CAN_QUERY_LINE_RE,
    USB_NAME_RE,
    parse_can_query_output,
)


def test_usb_name_matches_klipper_app_mode() -> None:
    m = USB_NAME_RE.match("usb-Klipper_stm32f446xx_310031000C51303032383431-if00")
    assert m is not None
    assert m.group("product") == "Klipper"
    assert m.group("mcu") == "stm32f446xx"
    assert m.group("uid") == "310031000C51303032383431"


def test_usb_name_matches_katapult_bootloader_mode() -> None:
    m = USB_NAME_RE.match("usb-katapult_stm32g0b1xx_3F00250011504B5735313220-if00")
    assert m is not None
    assert m.group("product") == "katapult"
    assert m.group("mcu") == "stm32g0b1xx"
    assert m.group("uid") == "3F00250011504B5735313220"


def test_usb_name_matches_rp2040() -> None:
    m = USB_NAME_RE.match("usb-Klipper_rp2040_E4625900029E4928-if00")
    assert m is not None
    assert m.group("mcu") == "rp2040"


def test_usb_name_rejects_unrelated() -> None:
    assert USB_NAME_RE.match("usb-Beacon_Beacon_RevH_DEAD-if00") is None
    assert USB_NAME_RE.match("pci-0000:00:14.0-usb-0:1") is None


def test_can_query_parses_real_format() -> None:
    # Verbatim format from Arksine/katapult/scripts/flashtool.py:723
    stdout = (
        "Resetting all bootloader node IDs...\n"
        "Checking for Katapult nodes...\n"
        "Detected UUID: 1586f2c37eaf, Application: Katapult\n"
        "Detected UUID: aabbccddeeff, Application: Klipper\n"
        "CANBus UUID Query Complete\n"
    )
    found = parse_can_query_output(stdout, "can0")
    assert len(found) == 2
    assert found[0].uuid == "1586f2c37eaf"
    assert found[0].application == "Katapult"
    assert found[0].iface == "can0"
    assert found[1].application == "Klipper"


def test_can_query_handles_unknown_app() -> None:
    stdout = "Detected UUID: 0123456789ab, Application: Unknown\n"
    found = parse_can_query_output(stdout, "can0")
    assert len(found) == 1
    assert found[0].application == "Unknown"


def test_can_query_zero_responses() -> None:
    stdout = "Checking for Katapult nodes...\nCANBus UUID Query Complete\n"
    assert parse_can_query_output(stdout, "can0") == []


def test_can_query_line_anchor_rejects_partial_match() -> None:
    # The old loose regex would have accepted this; the anchored one must not.
    assert CAN_QUERY_LINE_RE.match("UUID: abc, Application: Klipper") is None
    assert CAN_QUERY_LINE_RE.match("Detected UUID: ZZZ, Application: Klipper") is None


def test_can_query_uppercase_hex_rejected() -> None:
    # flashtool.py uses .hex() which always emits lowercase. Don't accept uppercase.
    assert CAN_QUERY_LINE_RE.match(
        "Detected UUID: ABCDEF012345, Application: Klipper"
    ) is None
