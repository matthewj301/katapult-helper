"""Per-MCU hardware profiles.

A profile encodes hardware truth (what the chip / package physically requires)
plus the typical Katapult bootloader offset for boards using this MCU. It does
NOT prescribe deployment choices like "USB vs CAN" — that's the user's call.

Tonight's brick (2026-04-26): a stm32f042x6 expansion board got built with
`STM32_USB_PA11_PA12=y` and `STM32_CLOCK_REF_8M=y`. The chip has neither
PA11/PA12 pins (TSSOP-20 package) nor an external 8MHz crystal, so the
firmware loaded but the system clock never started and USB had no pins to come
out on. These profiles encode exactly that kind of fleet wisdom so the next
flash refuses to brick the board.

Add new entries when validating a new board family. Keep entries narrow:
clock, USB pin remap, CAN pin choice, flash-size hints, typical Katapult
offset. Don't add features (`WANT_NEOPIXEL` etc) — those are user choices.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Profile:
    name: str
    mcu_family: str
    package_or_board: str
    clock_freq_hz: int
    katapult_offsets: tuple[int, ...]      # known-good Katapult application offsets
    required: dict[str, str] = field(default_factory=dict)
    incompatible: dict[str, str] = field(default_factory=dict)
    recommended: dict[str, str] = field(default_factory=dict)
    notes: str = ""


# Initial fleet — extend by adding entries.
_ALL_STM32_CLOCK_REFS = (
    # Every value in upstream Klipper's STM32_CLOCK_REF choice group as of 2026-04.
    # Used to auto-build `incompatible` from the one `required` clock-ref entry.
    "CONFIG_STM32_CLOCK_REF_INTERNAL",
    "CONFIG_STM32_CLOCK_REF_8M",
    "CONFIG_STM32_CLOCK_REF_12M",
    "CONFIG_STM32_CLOCK_REF_16M",
    "CONFIG_STM32_CLOCK_REF_20M",
    "CONFIG_STM32_CLOCK_REF_24M",
    "CONFIG_STM32_CLOCK_REF_25M",
)


def _all_clock_refs_except(required_key: str) -> dict[str, str]:
    return {k: "y" for k in _ALL_STM32_CLOCK_REFS if k != required_key}


PROFILES: dict[str, Profile] = {
    "stm32f042x6-tssop20": Profile(
        name="stm32f042x6-tssop20",
        mcu_family="stm32f042x6",
        package_or_board="TSSOP-20 (Klipper expansion board, BTT U2C, similar small breakouts)",
        clock_freq_hz=48_000_000,
        katapult_offsets=(0x08002000,),
        required={
            "CONFIG_STM32_USB_PA11_PA12_REMAP": "y",  # TSSOP-20 has no PA11/PA12
            "CONFIG_STM32_CLOCK_REF_INTERNAL": "y",   # no external crystal
        },
        incompatible={
            "CONFIG_STM32_USB_PA11_PA12": "y",
            **_all_clock_refs_except("CONFIG_STM32_CLOCK_REF_INTERNAL"),
        },
        recommended={
            "CONFIG_WANT_OPTIMIZE_SIZE": "y",   # 32KB total flash
            "CONFIG_HAVE_LIMITED_CODE_SIZE": "y",
        },
        notes=(
            "32KB total flash; with 8KB Katapult, only 24KB is available for Klipper. "
            "Trim features aggressively. Uses internal HSI clock (no crystal)."
        ),
    ),
    "stm32g0b1xx-8mhz": Profile(
        name="stm32g0b1xx-8mhz",
        mcu_family="stm32g0b1xx",
        package_or_board="STM32G0B1 boards with 8 MHz crystal (BTT MMB v1, BTT EBB36/42 v1.x, similar)",
        clock_freq_hz=64_000_000,
        katapult_offsets=(0x08002000,),
        required={
            "CONFIG_STM32_CLOCK_REF_8M": "y",
        },
        incompatible=_all_clock_refs_except("CONFIG_STM32_CLOCK_REF_8M"),
        recommended={},
        notes=(
            "G0B1 boards with external 8 MHz crystal. Available comm interfaces are USB "
            "(PA11/PA12) and FDCAN1 (PB0/PB1) — pick one based on deployment. Katapult "
            "typically at 8 KiB offset (0x08002000). CANBUS_FREQUENCY is deployment-"
            "specific (only relevant if CAN selected) so it's not encoded here."
        ),
    ),
    "stm32g0b1xx-16mhz": Profile(
        name="stm32g0b1xx-16mhz",
        mcu_family="stm32g0b1xx",
        package_or_board="STM32G0B1 boards with 16 MHz crystal (Fysetc FPS rev3.x, similar)",
        clock_freq_hz=64_000_000,
        katapult_offsets=(0x08002000,),
        required={
            "CONFIG_STM32_CLOCK_REF_16M": "y",
        },
        incompatible=_all_clock_refs_except("CONFIG_STM32_CLOCK_REF_16M"),
        recommended={},
        notes=(
            "G0B1 boards with external 16 MHz crystal. Same MCU as the 8 MHz variant; "
            "only the crystal frequency differs. Comm interfaces: USB (PA11/PA12) and "
            "FDCAN1 (PB0/PB1). Katapult typically at 8 KiB offset (0x08002000)."
        ),
    ),
    "stm32h723xx-25mhz": Profile(
        name="stm32h723xx-25mhz",
        mcu_family="stm32h723xx",
        package_or_board="STM32H723 boards with 25 MHz crystal (BTT Octopus Max EZ, similar)",
        clock_freq_hz=520_000_000,
        katapult_offsets=(0x08020000,),
        required={
            "CONFIG_STM32_CLOCK_REF_25M": "y",
        },
        incompatible=_all_clock_refs_except("CONFIG_STM32_CLOCK_REF_25M"),
        recommended={},
        notes=(
            "H723-class mainboards. 25 MHz crystal. Katapult typically at 128 KiB offset "
            "(0x08020000) because H723 has 1 MB flash and Katapult itself is larger. "
            "katapult_offsets is advisory (used in error messages); the runtime offset "
            "check compares the build's CONFIG_FLASH_APPLICATION_ADDRESS to whatever "
            "the on-chip Katapult actually reports via `flashtool.py -s`."
        ),
    ),
}


# Auto-derive a profile from an MCU family when the mapping is unambiguous.
# Boards that need a more specific profile must set `profile:` explicitly in
# inventory.yaml.
_DEFAULT_BY_MCU: dict[str, str] = {
    "stm32f042x6": "stm32f042x6-tssop20",
    "stm32g0b1xx": "stm32g0b1xx-8mhz",
    "stm32h723xx": "stm32h723xx-25mhz",
}


def get_profile(name_or_mcu: str | None) -> Profile | None:
    """Look up a profile by explicit name, or by MCU family (auto-derive).
    Returns None if there's no match — callers treat this as 'no validation'.
    Use `resolve_board_profile` for the board-aware lookup that warns on typos."""
    if not name_or_mcu:
        return None
    if name_or_mcu in PROFILES:
        return PROFILES[name_or_mcu]
    derived = _DEFAULT_BY_MCU.get(name_or_mcu)
    if derived:
        return PROFILES.get(derived)
    return None


def resolve_board_profile(
    explicit: str | None,
    mcu_family: str | None,
) -> Profile | None:
    """Look up a profile for a board. Tries `explicit` (an inventory `profile:`
    field) first, then falls back to `mcu_family` auto-derivation. If `explicit`
    is set but doesn't match a known profile name (typo footgun), log a warning
    listing the known names — silently disabling all checks for that board is
    the worst possible outcome."""
    if explicit:
        p = PROFILES.get(explicit)
        if p is not None:
            return p
        # explicit doesn't match — also try as a mcu_family for forgiving lookup
        derived_name = _DEFAULT_BY_MCU.get(explicit)
        if derived_name:
            return PROFILES.get(derived_name)
        from loguru import logger as _logger
        _logger.warning(
            "unknown profile {!r}; known profiles: {}. Falling back to mcu_family auto-derive.",
            explicit, ", ".join(sorted(PROFILES.keys())),
        )
    return get_profile(mcu_family)


@dataclass
class ProfileViolations:
    missing_required: list[tuple[str, str]] = field(default_factory=list)   # (key, expected)
    set_incompatible: list[tuple[str, str]] = field(default_factory=list)   # (key, value)
    missing_recommended: list[tuple[str, str]] = field(default_factory=list)

    @property
    def has_blocking(self) -> bool:
        return bool(self.missing_required or self.set_incompatible)

    @property
    def has_any(self) -> bool:
        return self.has_blocking or bool(self.missing_recommended)


def parse_kconfig(text: str) -> dict[str, str]:
    """Parse a Klipper .config (Kconfig output) into a plain {key: value} dict.
    Lines like `# CONFIG_FOO is not set` are stored as `CONFIG_FOO -> "n"`."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            if line.startswith("# ") and line.endswith(" is not set"):
                key = line[2:-len(" is not set")].strip()
                result[key] = "n"
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"')
        result[key] = value
    return result


def check_profile(config_text: str, profile: Profile) -> ProfileViolations:
    cfg = parse_kconfig(config_text)
    v = ProfileViolations()
    for key, expected in profile.required.items():
        if cfg.get(key, "n") != expected:
            v.missing_required.append((key, expected))
    for key, bad in profile.incompatible.items():
        if cfg.get(key, "n") == bad:
            v.set_incompatible.append((key, bad))
    for key, expected in profile.recommended.items():
        if cfg.get(key, "n") != expected:
            v.missing_recommended.append((key, expected))
    return v


def _set_kconfig_value(lines: list[str], key: str, value: str | None) -> list[str]:
    """Replace or append a Kconfig key in a list of .config lines.

    value=None unsets the key (``# CONFIG_KEY is not set``).
    """
    new_line = f"# {key} is not set" if value is None else f"{key}={value}"
    set_prefix = f"{key}="
    unset_marker = f"# {key} is not set"
    found = False
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(set_prefix) or stripped == unset_marker:
            result.append(new_line)
            found = True
        else:
            result.append(line)
    if not found:
        result.append(new_line)
    return result


def apply_profile_kconfig(config_text: str, profile: Profile) -> tuple[str, list[str]]:
    """Patch required/incompatible Kconfig values in a .config per *profile*.

    Returns ``(new_config_text, changes)`` where *changes* is a list of
    human-readable descriptions of what was fixed.  An empty list means the
    config already satisfied the profile.
    """
    cfg = parse_kconfig(config_text)
    lines = config_text.splitlines()
    changes: list[str] = []

    for key, bad in profile.incompatible.items():
        if cfg.get(key, "n") == bad:
            lines = _set_kconfig_value(lines, key, None)
            changes.append(f"unset {key}={bad} (incompatible)")

    for key, expected in profile.required.items():
        if cfg.get(key, "n") != expected:
            lines = _set_kconfig_value(lines, key, expected)
            changes.append(f"set {key}={expected} (required)")

    new_text = "\n".join(lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, changes


def get_app_start_addr(config_text: str) -> int | None:
    """Read CONFIG_FLASH_APPLICATION_ADDRESS from a parsed .config, as int."""
    cfg = parse_kconfig(config_text)
    raw = cfg.get("CONFIG_FLASH_APPLICATION_ADDRESS")
    if not raw:
        return None
    try:
        return int(raw, 0)  # auto-detect 0x prefix
    except ValueError:
        return None
