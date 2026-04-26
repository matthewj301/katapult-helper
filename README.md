# katapult-helper

Batch build and flash [Klipper](https://www.klipper3d.org/) (or [Kalico](https://github.com/KalicoCrew/kalico)) firmware to a fleet of MCUs running the [Katapult](https://github.com/Arksine/katapult) bootloader.

Drives an inventory file of human-readable names → device identifiers + per-board `.config` paths, runs `make` against each, and flashes via `flashtool.py`. One command (`wizard`) walks you through the whole pipeline: discover unknown MCUs, register them in the inventory, walk through `make menuconfig` for any missing `.config`, build, and flash.

## Why

KIAUH and the upstream Klipper docs assume one MCU at a time and an interactive operator. If you have several printers — a couple of toolheads, a controller, a chamber MCU — repeating `make menuconfig` → `make` → `make flash` per MCU after every Klipper update is tedious and error-prone. `katapult-helper` makes the per-board inputs (board, transport, config path, identifier) data, not memory.

## How it works

- **Stable identity is the chip UID.** When an MCU reboots between Katapult and Klipper, its `/dev/serial/by-id/*` symlink prefix flips (`usb-katapult_*` ↔ `usb-Klipper_*`) but the chip-UID hex suffix is identical. The inventory keys USB boards by `chip_uid` and resolves the live by-id path at flash time.
- **One Klipper checkout, per-board `.config`.** Each board's Kconfig file lives outside the Klipper tree (typically `~/printer_data/firmware_configs/<board>.config`). We pass it via `KCONFIG_CONFIG=<path>` so the in-tree `.config` is never mutated.
- **Sequential build → flash.** Klipper's build is in-tree, so we build one board, flash it, then move to the next. `klipper.service` is stopped once at the start and restarted once at the end.
- **CLI subprocess to `flashtool.py`.** No Python imports of Katapult internals — calls match the upstream CLI, so behavior tracks Katapult releases.

## Install

```bash
git clone git@github.com:matthewj301/katapult-helper.git
cd katapult-helper
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Prerequisites on the host:

- A Klipper or Kalico checkout (e.g. `~/git/kalico` or `~/klipper`).
- A Katapult checkout (e.g. `~/katapult`) — the package uses `<katapult_repo>/scripts/flashtool.py`.
- Standard build tools: `make`, ARM toolchain (`gcc-arm-none-eabi`), `python3`, `pyserial`.
- For CAN: `can-utils` and an `up` `can0` interface.
- The user must be in `dialout` and have `sudo` rights for `systemctl stop/start klipper`.

## Quick start (the one-shot wizard)

```bash
cp inventory.example.yaml inventory.yaml
$EDITOR inventory.yaml          # set klipper_repo / katapult_repo paths
katapult-helper wizard
```

The wizard will:

1. Scan `/dev/serial/by-id/*` and `flashtool.py -i can0 -q`.
2. For every MCU not yet in `inventory.yaml`, prompt for a friendly name and a `.config` path, and write the new entry back to the YAML (round-trip, comments preserved).
3. For every board whose `.config` does not yet exist, show a Rich panel with bootloader-offset hints for that MCU family, then launch `make menuconfig KCONFIG_CONFIG=<path>` so you fill it in once.
4. Stop `klipper.service`.
5. Loop per board: `make clean` → `make olddefconfig` → `make -j$(nproc)` → `flashtool.py …`.
6. Restart `klipper.service` (always, in `finally`).

`--no-flash` does the discover/configure/build phases but skips the flash step — useful for dry runs.

## Inventory schema

```yaml
klipper_repo: ~/git/kalico       # or ~/klipper; logged as "Kalico" or "Klipper"
katapult_repo: ~/katapult        # provides scripts/flashtool.py

boards:
  doomcube-octopus:
    transport: usb
    chip_uid: 430031000D51313339373836      # the hex suffix from /dev/serial/by-id/usb-*_<mcu>_<UID>-if00
    mcu_family: stm32h723xx                  # informational; drives menuconfig hints
    klipper_config: ~/printer_data/firmware_configs/doomcube_octopus.config

  voron-ebb36:
    transport: can
    can_iface: can0
    canbus_uuid: 1586f2c37eaf
    mcu_family: stm32g0b1xx
    klipper_config: ~/printer_data/firmware_configs/voron_ebb36.config
```

`inventory.yaml` is gitignored; `inventory.example.yaml` is the seed.

## Commands

| Command | Purpose |
|---|---|
| `katapult-helper wizard` | Full pipeline: discover → upsert → configure → build → flash. |
| `katapult-helper list` | Print the current inventory as a table. |
| `katapult-helper discover` | Scan USB and CAN; show a table of what's currently visible. |
| `katapult-helper configure [NAMES…] [--all-missing] [--force]` | Walk through `make menuconfig` for selected boards. Shows MCU-family hints (bootloader offset, recommended interface) before launching. |
| `katapult-helper build [NAMES…] [--menuconfig]` | Build firmware for one or more boards. Auto-runs menuconfig if `.config` is missing. |
| `katapult-helper flash [NAMES…] [-f path/to/klipper.bin]` | Flash without rebuilding. Stops/starts klipper.service around the run. |
| `katapult-helper run [NAMES…]` | Build + flash, board-by-board, with klipper restart at the end. |

`NAMES` is optional everywhere — omit it to operate on all inventory entries.

Global flags:

- `-c, --inventory PATH` — alternate inventory file (default: `./inventory.yaml`).
- `-v, --verbose` — DEBUG-level logging.

## Bootloader offset hints (used by `configure`)

| MCU family | Suggested Katapult offset |
|---|---|
| `stm32f103xx` | 8 KiB |
| `stm32f405xx` | 32 KiB |
| `stm32f446xx` | 32 KiB |
| `stm32g0b1xx` | 8 KiB |
| `stm32h723xx` | 128 KiB |
| `rp2040`      | 16 KiB |

These are guidance shown before menuconfig launches — menuconfig still drives the actual selection. **The offset must match the Katapult build flashed to the chip; mismatches will brick the boot chain and require recovery via DFU/picoboot.**

## Design notes

- **YAML round-trip via `ruamel.yaml`** so the wizard can append entries without nuking your comments.
- **Subprocess everything** (`make`, `flashtool.py`, `systemctl`) — stdout/stderr stream straight to the terminal so menuconfig and progress bars Just Work.
- **No silent flag-flipping in `.config`.** When `.config` already exists, `build` runs `make olddefconfig` (validates and fills defaults, never overrides your choices).
- **CAN discovery uses the upstream tool** (`flashtool.py -i can0 -q`) so we inherit whatever protocol Katapult ships with.

## Status

Early — not yet exercised against a real fleet. Likely rough edges around CAN UUID parsing (the regex matches the upstream `flashtool.py -q` output as observed at the time of writing, but newer formats may differ) and around `sudo systemctl` semantics on non-systemd hosts.

## License

See `LICENSE`.
