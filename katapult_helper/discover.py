from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ._proc import BY_ID, can_iface_present
from .inventory import Inventory

USB_NAME_RE = re.compile(
    r"usb-(?P<product>[^_]+)_(?P<mcu>[^_]+)_(?P<uid>[0-9A-Fa-f]+)-if\d+"
)

# Per Katapult's flashtool.py:723, each query response is printed verbatim as:
#     Detected UUID: <12 lowercase hex>, Application: <Klipper|Katapult|Unknown>
CAN_QUERY_LINE_RE = re.compile(
    r"^Detected UUID:\s*(?P<uuid>[0-9a-f]{12}),\s*Application:\s*(?P<app>\S+)\s*$"
)


@dataclass
class DiscoveredUsb:
    by_id: Path
    product: str
    mcu_family: str
    chip_uid: str


@dataclass
class DiscoveredCan:
    uuid: str
    application: str
    iface: str


def discover_usb() -> list[DiscoveredUsb]:
    if not BY_ID.exists():
        return []
    found: list[DiscoveredUsb] = []
    for entry in sorted(BY_ID.iterdir()):
        m = USB_NAME_RE.match(entry.name)
        if not m:
            continue
        found.append(DiscoveredUsb(
            by_id=entry,
            product=m.group("product"),
            mcu_family=m.group("mcu"),
            chip_uid=m.group("uid"),
        ))
    return found


def parse_can_query_output(stdout: str, iface: str) -> list[DiscoveredCan]:
    found: list[DiscoveredCan] = []
    for line in stdout.splitlines():
        m = CAN_QUERY_LINE_RE.match(line.strip())
        if m:
            found.append(DiscoveredCan(
                uuid=m.group("uuid"), application=m.group("app"), iface=iface,
            ))
    return found


def query_can_raw(inv: Inventory, iface: str = "can0") -> str | None:
    """Run `flashtool.py -i <iface> -q` and return its stdout, or None if skipped."""
    if not can_iface_present(iface):
        logger.debug("CAN interface {} not present; skipping CAN discovery", iface)
        return None
    if not inv.flashtool.exists():
        logger.warning("flashtool not found at {}; skipping CAN discovery", inv.flashtool)
        return None
    try:
        result = subprocess.run(
            [str(inv.flashtool), "-i", iface, "-q"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("CAN discovery failed: {}", e)
        return None
    return result.stdout


def discover_can(inv: Inventory, iface: str = "can0") -> list[DiscoveredCan]:
    stdout = query_can_raw(inv, iface)
    if stdout is None:
        return []
    return parse_can_query_output(stdout, iface)
