from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ruamel.yaml import YAML

Transport = Literal["usb", "can"]

_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.preserve_quotes = True


@dataclass
class Board:
    name: str
    transport: Transport
    klipper_config: Path
    chip_uid: str | None = None
    mcu_family: str | None = None
    can_iface: str = "can0"
    canbus_uuid: str | None = None

    def __post_init__(self) -> None:
        if self.transport == "usb" and not self.chip_uid:
            raise ValueError(f"board {self.name!r}: usb transport requires chip_uid")
        if self.transport == "can" and not self.canbus_uuid:
            raise ValueError(f"board {self.name!r}: can transport requires canbus_uuid")


@dataclass
class Inventory:
    klipper_repo: Path
    katapult_repo: Path
    boards: dict[str, Board] = field(default_factory=dict)

    @property
    def repo_kind(self) -> str:
        name = self.klipper_repo.name.lower()
        if "kalico" in name:
            return "Kalico"
        return "Klipper"

    @property
    def klipper_bin(self) -> Path:
        return self.klipper_repo / "out" / "klipper.bin"

    @property
    def klipper_dict(self) -> Path:
        return self.klipper_repo / "out" / "klipper.dict"

    @property
    def flashtool(self) -> Path:
        return self.katapult_repo / "scripts" / "flashtool.py"

    def select(self, names: tuple[str, ...]) -> list[Board]:
        if not names:
            return list(self.boards.values())
        missing = [n for n in names if n not in self.boards]
        if missing:
            raise KeyError(f"unknown board(s): {', '.join(missing)}")
        return [self.boards[n] for n in names]


def load_raw(path: Path) -> dict:
    """Load YAML preserving comments/order for round-trip writes."""
    return _yaml.load(path.read_text())


def save_raw(path: Path, data: dict) -> None:
    with path.open("w") as f:
        _yaml.dump(data, f)


def load_inventory(path: Path) -> Inventory:
    raw = load_raw(path)
    klipper_repo = Path(raw["klipper_repo"]).expanduser()
    katapult_repo = Path(raw.get("katapult_repo", "~/katapult")).expanduser()
    boards: dict[str, Board] = {}
    for name, entry in (raw.get("boards") or {}).items():
        boards[name] = Board(
            name=name,
            transport=entry["transport"],
            klipper_config=Path(entry["klipper_config"]).expanduser(),
            chip_uid=entry.get("chip_uid"),
            mcu_family=entry.get("mcu_family"),
            can_iface=entry.get("can_iface", "can0"),
            canbus_uuid=entry.get("canbus_uuid"),
        )
    return Inventory(klipper_repo=klipper_repo, katapult_repo=katapult_repo, boards=boards)


def upsert_board(
    raw: dict,
    name: str,
    *,
    transport: str,
    klipper_config: str,
    chip_uid: str | None = None,
    mcu_family: str | None = None,
    can_iface: str | None = None,
    canbus_uuid: str | None = None,
) -> bool:
    """Add or update a board entry in a round-trip-loaded YAML dict.
    Returns True if anything changed."""
    boards = raw.setdefault("boards", {})
    existing = boards.get(name) or {}
    new = {"transport": transport, "klipper_config": klipper_config}
    if mcu_family:
        new["mcu_family"] = mcu_family
    if transport == "usb":
        new["chip_uid"] = chip_uid or ""
    else:
        new["can_iface"] = can_iface or "can0"
        new["canbus_uuid"] = canbus_uuid or ""
    if dict(existing) == new:
        return False
    boards[name] = new
    return True
