# katapult-helper

A small CLI that batch-flashes [Klipper](https://www.klipper3d.org/) (or [Kalico](https://github.com/KalicoCrew/kalico)) firmware to MCUs running the [Katapult](https://github.com/Arksine/katapult) bootloader.

If you have more than one or two boards in your printer, re-flashing them after every Klipper update gets repetitive. This tool reads a YAML inventory of your boards and handles the discover → menuconfig → build → flash sequence for all of them in one command. Sharing it in case it's useful to someone else.

## What it handles

- Identifies boards by their **chip UID** rather than the by-id path, so it still works after the symlink prefix changes between `usb-katapult_*` and `usb-Klipper_*`.
- Keeps each board's `.config` outside the Klipper tree (typically `~/printer_data/firmware_configs/<board>.config`) and passes it via `KCONFIG_CONFIG=...`. One Klipper checkout, multiple boards.
- Stops `klipper.service` once at the start of a flash batch and restarts it once at the end.
- Calls `flashtool.py` as a subprocess (no Python imports of Katapult internals), so behavior tracks upstream.
- Runs a bootloader-offset preflight before flashing — checks `flashtool.py -s` against the build's `CONFIG_FLASH_APPLICATION_ADDRESS` and refuses to write a mismatch.
- Has per-MCU hardware profiles that warn at build time about settings that won't work for that chip (wrong USB pin choice for a small package, wrong clock reference for a board with no crystal, etc.).

## Install

```bash
git clone git@github.com:matthewj301/katapult-helper.git
cd katapult-helper
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Python 3.9+ (so it works on Raspberry Pi OS).

You'll also need:

- A Klipper or Kalico checkout somewhere (`~/klipper`, `~/git/kalico`, etc.).
- A Katapult checkout (`~/katapult`) — the tool shells out to its `scripts/flashtool.py`.
- Standard build tools: `make`, `gcc-arm-none-eabi`, `python3`, `pyserial`.
- `can-utils` and a configured `can0` interface if you have CAN toolheads.
- Your user in `dialout`, and able to `sudo systemctl stop klipper` without a password prompt:

  ```bash
  echo "$USER ALL=NOPASSWD: /bin/systemctl" | sudo tee /etc/sudoers.d/katapult-helper
  ```

  Without that, the tool will refuse to start a flash and tell you why — otherwise klipper would keep `/dev/ttyACM*` open and the flash would fail with "device already in use".

## Quick start

```bash
cp inventory.example.yaml inventory.yaml
$EDITOR inventory.yaml          # set klipper_repo / katapult_repo paths
katapult-helper wizard
```

The wizard:

1. Scans `/dev/serial/by-id/*` and the CAN bus.
2. For any MCU not yet in `inventory.yaml`, asks for a friendly name and a `.config` path, and writes the new entry back to the YAML (preserving comments).
3. For any board missing its `.config`, opens menuconfig with relevant hints up front.
4. Stops klipper.
5. For each board, runs `make clean` → `make olddefconfig` → `make -j` → `flashtool.py …`.
6. Restarts klipper.

Pass `--no-flash` to do everything except the actual write — useful for checking that things compile.

## inventory.yaml

```yaml
klipper_repo: ~/git/kalico       # or ~/klipper
katapult_repo: ~/katapult

boards:
  doomcube-octopus:
    transport: usb
    chip_uid: 430031000D51313339373836      # the hex suffix from /dev/serial/by-id/usb-*_<mcu>_<UID>-if00
    mcu_family: stm32h723xx
    profile: stm32h723xx-25mhz              # optional; auto-derived from mcu_family if omitted
    klipper_config: ~/printer_data/firmware_configs/doomcube_octopus.config

  voron-ebb36:
    transport: can
    can_iface: can0
    canbus_uuid: 1586f2c37eaf
    mcu_family: stm32g0b1xx
    profile: stm32g0b1xx-8mhz
    klipper_config: ~/printer_data/firmware_configs/voron_ebb36.config
```

`inventory.yaml` is gitignored; `inventory.example.yaml` is the seed.

## Commands

| Command | What it does |
|---|---|
| `katapult-helper wizard` | The whole pipeline. |
| `katapult-helper list` | Show the current inventory. |
| `katapult-helper discover [--raw] [--can-iface IFACE]` | Scan USB and CAN. `--raw` shows unparsed `flashtool.py -q` output. |
| `katapult-helper configure [NAMES…] [--all-missing] [--force]` | Run menuconfig for selected boards. Backs up the existing `.config` first. |
| `katapult-helper build [NAMES…] [--menuconfig]` | Build only. |
| `katapult-helper flash [NAMES…] [-f path/to/klipper.bin] [--force]` | Flash only. `--force` skips the bootloader-offset preflight. |
| `katapult-helper run [NAMES…] [--force]` | Build + flash. |

`NAMES` is optional everywhere — leave it off to operate on all boards.

Global flags:

- `-c, --inventory PATH` — alternate inventory file (default `./inventory.yaml`).
- `-v, --verbose` — DEBUG-level logging.

## Hardware profiles

Each board can name a `profile:` (defined in `katapult_helper/profiles.py`). Profiles encode hardware truth — clock reference, USB pin choices, packaging quirks, the typical Katapult offset for the family. They're used for:

- The pre-flash offset check.
- Post-build warnings about settings that won't work on the chip.
- Hints shown in menuconfig before you run it.

Currently included: `stm32f042x6-tssop20`, `stm32g0b1xx-8mhz` (BTT MMB / EBB36 / EBB42), and `stm32h723xx-25mhz` (Octopus Max EZ class). If your board's MCU has a profile, the extra checks run automatically; if not, they're skipped (nothing breaks). PRs welcome for new boards.

## Bootloader offsets

| MCU family | Typical Katapult offset |
|---|---|
| `stm32f042x6` (32KB flash) | 8 KiB |
| `stm32f103xx` | 8 KiB |
| `stm32f405xx` / `stm32f446xx` | 32 KiB |
| `stm32g0b1xx` | 8 KiB |
| `stm32h723xx` | 128 KiB |
| `rp2040` | 16 KiB |

The offset in your Klipper `.config` has to match the offset Katapult was built with on the chip. If they don't match, the chip jumps to the wrong address and won't boot. The pre-flash offset check catches this — if you see a `BOOTLOADER OFFSET MISMATCH` error, that's what it means.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Subprocess calls to `make` and `flashtool.py` aren't mocked; tests target the parsers, the YAML round-trip, the by-id resolution, and the error-translation paths. Real captures from `flashtool.py -q` and `flashtool.py -s` live in `tests/fixtures/`.

## Notes

Assumes systemd for the klipper service. Non-systemd hosts would need to swap out the service stop/start helper.

This was put together quickly, so expect rough edges. Issues and PRs welcome.

## License

See `LICENSE`.
