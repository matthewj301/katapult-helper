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


def _str_or_none(v: object) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


class InventoryError(ValueError):
    """Raised for malformed or incomplete inventory YAML."""


def build_inventory(raw: dict) -> Inventory:
    """Construct an Inventory from a parsed (or in-memory mutated) YAML dict.

    All string-typed fields are coerced via str() so that unquoted YAML values
    that ruamel parses as int (e.g. chip_uid: 1234567890) still arrive as the
    canonical string form Board expects.
    """
    if not isinstance(raw, dict) or not raw.get("klipper_repo"):
        raise InventoryError(
            "inventory.yaml must define a non-empty top-level `klipper_repo` "
            "(path to the Klipper or Kalico checkout to build firmware in)"
        )
    klipper_repo = Path(str(raw["klipper_repo"])).expanduser()
    katapult_repo = Path(str(raw.get("katapult_repo", "~/katapult"))).expanduser()
    boards: dict[str, Board] = {}
    for name, entry in (raw.get("boards") or {}).items():
        boards[str(name)] = Board(
            name=str(name),
            transport=str(entry["transport"]),  # type: ignore[arg-type]
            klipper_config=Path(str(entry["klipper_config"])).expanduser(),
            chip_uid=_str_or_none(entry.get("chip_uid")),
            mcu_family=_str_or_none(entry.get("mcu_family")),
            can_iface=str(entry.get("can_iface", "can0")),
            canbus_uuid=_str_or_none(entry.get("canbus_uuid")),
        )
    return Inventory(klipper_repo=klipper_repo, katapult_repo=katapult_repo, boards=boards)


def load_inventory(path: Path) -> Inventory:
    return build_inventory(load_raw(path))


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
    Returns True if anything changed.

    Required fields are validated up-front so we never write a YAML file that
    will fail Board.__post_init__ on the next load.
    """
    if transport == "usb" and not chip_uid:
        raise ValueError(f"board {name!r}: usb transport requires a non-empty chip_uid")
    if transport == "can" and not canbus_uuid:
        raise ValueError(f"board {name!r}: can transport requires a non-empty canbus_uuid")

    boards = raw.setdefault("boards", {})
    new: dict[str, str] = {"transport": transport, "klipper_config": klipper_config}
    if mcu_family:
        new["mcu_family"] = mcu_family
    if transport == "usb":
        assert chip_uid  # validated above
        new["chip_uid"] = chip_uid
    else:
        assert canbus_uuid  # validated above
        new["can_iface"] = can_iface or "can0"
        new["canbus_uuid"] = canbus_uuid

    existing = {str(k): str(v) for k, v in (boards.get(name) or {}).items()}
    if existing == new:
        return False
    boards[name] = new
    return True
