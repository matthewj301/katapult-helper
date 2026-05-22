from __future__ import annotations

from katapult_helper.profiles import (
    PROFILES,
    apply_profile_kconfig,
    check_profile,
    get_app_start_addr,
    get_profile,
    parse_kconfig,
    resolve_board_profile,
)


def test_get_profile_by_explicit_name() -> None:
    p = get_profile("stm32f042x6-tssop20")
    assert p is not None
    assert p.mcu_family == "stm32f042x6"


def test_get_profile_auto_derives_from_mcu_family() -> None:
    p = get_profile("stm32g0b1xx")
    assert p is not None
    assert p.name == "stm32g0b1xx-8mhz"


def test_get_profile_returns_none_for_unknown() -> None:
    assert get_profile("rp2350-bogus") is None
    assert get_profile(None) is None
    assert get_profile("") is None


def test_parse_kconfig_handles_unset_lines() -> None:
    text = (
        "CONFIG_MCU=\"stm32f042x6\"\n"
        "CONFIG_MACH_STM32F042=y\n"
        "# CONFIG_STM32_USB_PA11_PA12 is not set\n"
        "# this is a comment\n"
        "\n"
        "CONFIG_FLASH_APPLICATION_ADDRESS=0x8002000\n"
    )
    parsed = parse_kconfig(text)
    assert parsed["CONFIG_MCU"] == "stm32f042x6"
    assert parsed["CONFIG_MACH_STM32F042"] == "y"
    assert parsed["CONFIG_STM32_USB_PA11_PA12"] == "n"
    assert parsed["CONFIG_FLASH_APPLICATION_ADDRESS"] == "0x8002000"


def test_get_app_start_addr_parses_hex() -> None:
    text = "CONFIG_FLASH_APPLICATION_ADDRESS=0x8002000\n"
    assert get_app_start_addr(text) == 0x8002000


def test_get_app_start_addr_missing_returns_none() -> None:
    assert get_app_start_addr("CONFIG_MCU=\"stm32f042x6\"\n") is None


def test_check_profile_clean_config_has_no_violations() -> None:
    p = PROFILES["stm32f042x6-tssop20"]
    text = (
        "CONFIG_STM32_USB_PA11_PA12_REMAP=y\n"
        "CONFIG_STM32_CLOCK_REF_INTERNAL=y\n"
        "CONFIG_WANT_OPTIMIZE_SIZE=y\n"
        "CONFIG_HAVE_LIMITED_CODE_SIZE=y\n"
    )
    v = check_profile(text, p)
    assert v.has_blocking is False
    assert v.has_any is False


def test_check_profile_flags_tonights_brick() -> None:
    """Tonight's bricked config: TSSOP-20 with non-remap USB and 8MHz crystal.
    Both must be flagged as incompatible, AND the missing remap/internal must
    be flagged as missing-required."""
    p = PROFILES["stm32f042x6-tssop20"]
    text = (
        "CONFIG_STM32_USB_PA11_PA12=y\n"
        "CONFIG_STM32_CLOCK_REF_8M=y\n"
    )
    v = check_profile(text, p)
    incompat_keys = {k for k, _ in v.set_incompatible}
    assert "CONFIG_STM32_USB_PA11_PA12" in incompat_keys
    assert "CONFIG_STM32_CLOCK_REF_8M" in incompat_keys
    required_keys = {k for k, _ in v.missing_required}
    assert "CONFIG_STM32_USB_PA11_PA12_REMAP" in required_keys
    assert "CONFIG_STM32_CLOCK_REF_INTERNAL" in required_keys
    assert v.has_blocking is True


def test_check_profile_recommended_only_no_blocking() -> None:
    """Missing recommended doesn't block; missing required does."""
    p = PROFILES["stm32f042x6-tssop20"]
    text = (
        "CONFIG_STM32_USB_PA11_PA12_REMAP=y\n"
        "CONFIG_STM32_CLOCK_REF_INTERNAL=y\n"
    )
    v = check_profile(text, p)
    assert v.has_blocking is False
    assert v.has_any is True  # missing WANT_OPTIMIZE_SIZE
    rec_keys = {k for k, _ in v.missing_recommended}
    assert "CONFIG_WANT_OPTIMIZE_SIZE" in rec_keys


def test_g0b1_profile_8mhz_required() -> None:
    p = PROFILES["stm32g0b1xx-8mhz"]
    text = "CONFIG_STM32_CLOCK_REF_INTERNAL=y\n"
    v = check_profile(text, p)
    assert v.has_blocking
    assert any(k == "CONFIG_STM32_CLOCK_REF_INTERNAL" for k, _ in v.set_incompatible)


def test_h723_profile_25mhz_required() -> None:
    p = PROFILES["stm32h723xx-25mhz"]
    text = "CONFIG_STM32_CLOCK_REF_8M=y\n"
    v = check_profile(text, p)
    assert v.has_blocking
    assert any(k == "CONFIG_STM32_CLOCK_REF_8M" for k, _ in v.set_incompatible)


def test_all_profiles_have_consistent_offsets() -> None:
    """All declared katapult_offsets must be valid STM32 flash addresses."""
    for p in PROFILES.values():
        for offset in p.katapult_offsets:
            assert 0x08000000 <= offset < 0x09000000, f"{p.name}: 0x{offset:X}"


def test_clock_ref_incompatible_lists_are_complete() -> None:
    """Reviewer caught: F042 and H723 originally only listed a few of the
    crystal-frequency choices as incompatible. Now derived from the full set."""
    p = PROFILES["stm32f042x6-tssop20"]
    for freq in ("8M", "12M", "16M", "20M", "24M", "25M"):
        assert f"CONFIG_STM32_CLOCK_REF_{freq}" in p.incompatible
    p = PROFILES["stm32h723xx-25mhz"]
    for freq in ("8M", "12M", "16M", "20M", "24M"):
        assert f"CONFIG_STM32_CLOCK_REF_{freq}" in p.incompatible


def test_resolve_board_profile_explicit_wins(caplog) -> None:
    p = resolve_board_profile("stm32f042x6-tssop20", "stm32g0b1xx")
    assert p is not None and p.name == "stm32f042x6-tssop20"


def test_resolve_board_profile_falls_back_to_mcu_on_typo(capsys) -> None:
    """A typo in `profile:` must not silently disable all checks. Fall back
    to mcu_family auto-derive if the explicit name doesn't resolve."""
    p = resolve_board_profile("stm32f042-tssop", "stm32f042x6")  # typo
    assert p is not None
    assert p.name == "stm32f042x6-tssop20"


def test_resolve_board_profile_returns_none_for_unknown_everything() -> None:
    assert resolve_board_profile("nonsense", "also-nonsense") is None
    assert resolve_board_profile(None, None) is None


def test_g0b1_profile_16mhz_required() -> None:
    p = PROFILES["stm32g0b1xx-16mhz"]
    assert p.mcu_family == "stm32g0b1xx"
    assert "CONFIG_STM32_CLOCK_REF_16M" in p.required
    text = "CONFIG_STM32_CLOCK_REF_8M=y\n"
    v = check_profile(text, p)
    assert v.has_blocking
    assert any(k == "CONFIG_STM32_CLOCK_REF_8M" for k, _ in v.set_incompatible)


def test_g0b1_16mhz_incompatible_list_is_complete() -> None:
    p = PROFILES["stm32g0b1xx-16mhz"]
    for freq in ("INTERNAL", "8M", "12M", "20M", "24M", "25M"):
        assert f"CONFIG_STM32_CLOCK_REF_{freq}" in p.incompatible
    assert "CONFIG_STM32_CLOCK_REF_16M" not in p.incompatible


def test_apply_profile_kconfig_fixes_wrong_clock() -> None:
    p = PROFILES["stm32g0b1xx-16mhz"]
    text = (
        "CONFIG_MACH_STM32=y\n"
        "CONFIG_STM32_CLOCK_REF_8M=y\n"
        "# CONFIG_STM32_CLOCK_REF_16M is not set\n"
    )
    new_text, changes = apply_profile_kconfig(text, p)
    parsed = parse_kconfig(new_text)
    assert parsed["CONFIG_STM32_CLOCK_REF_16M"] == "y"
    assert parsed["CONFIG_STM32_CLOCK_REF_8M"] == "n"
    assert len(changes) == 2
    assert any("set CONFIG_STM32_CLOCK_REF_16M=y" in c for c in changes)
    assert any("unset CONFIG_STM32_CLOCK_REF_8M=y" in c for c in changes)


def test_apply_profile_kconfig_noop_when_already_correct() -> None:
    p = PROFILES["stm32g0b1xx-16mhz"]
    text = (
        "CONFIG_MACH_STM32=y\n"
        "CONFIG_STM32_CLOCK_REF_16M=y\n"
        "# CONFIG_STM32_CLOCK_REF_8M is not set\n"
    )
    new_text, changes = apply_profile_kconfig(text, p)
    assert changes == []
    assert new_text == text


def test_apply_profile_kconfig_adds_missing_required_key() -> None:
    """If the required key doesn't appear at all, it gets appended."""
    p = PROFILES["stm32f042x6-tssop20"]
    text = "CONFIG_MACH_STM32F0=y\n"
    new_text, changes = apply_profile_kconfig(text, p)
    parsed = parse_kconfig(new_text)
    assert parsed["CONFIG_STM32_USB_PA11_PA12_REMAP"] == "y"
    assert parsed["CONFIG_STM32_CLOCK_REF_INTERNAL"] == "y"
    assert any("CONFIG_STM32_USB_PA11_PA12_REMAP" in c for c in changes)
