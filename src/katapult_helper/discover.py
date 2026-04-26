from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from .inventory import Inventory

BY_ID = Path("/dev/serial/by-id")
USB_NAME_RE = re.compile(
    r"usb-(?P<product>[^_]+)_(?P<mcu>[^_]+)_(?P<uid>[0-9A-Fa-f]+)-if\d+"
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


def discover_can(inv: Inventory, iface: str = "can0") -> list[DiscoveredCan]:
    if not inv.flashtool.exists():
        logger.warning("flashtool not found at {}; skipping CAN discovery", inv.flashtool)
        return []
    try:
        result = subprocess.run(
            ["python3", str(inv.flashtool), "-i", iface, "-q"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("CAN discovery failed: {}", e)
        return []
    found: list[DiscoveredCan] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        m = re.search(r"UUID:\s*([0-9a-fA-F]+).*?Application:\s*(\S+)", line)
        if m:
            found.append(DiscoveredCan(uuid=m.group(1), application=m.group(2), iface=iface))
    return found
