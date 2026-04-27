# katapult-helper

A batch flasher for [Klipper](https://www.klipper3d.org/) (or [Kalico](https://github.com/KalicoCrew/kalico)) firmware on MCUs running the [Katapult](https://github.com/Arksine/katapult) bootloader.

If you've ever flashed a couple of toolheads, a controller, and a chamber MCU after every Klipper update — clicking through `make menuconfig` for each one, copying the right `.config`, remembering which `/dev/serial/by-id/...` is which — this is for that.

You list your boards once in a YAML file, then `katapult-helper wizard` does the whole thing: finds new MCUs, walks you through menuconfig for any new ones, builds, flashes, restarts klipper. Run it again next month and it does the same builds and flashes (no menuconfig prompts unless something's missing).

## What it does well

- Identifies boards by their **chip UID**, not their by-id path. The by-id flips between `usb-katapult_*` and `usb-Klipper_*` when an MCU reboots, but the hex suffix is the same. Resolved at flash time, so it Just Works whichever mode the device is currently in.
- Keeps each board's `.config` outside the Klipper tree, in `~/printer_data/firmware_configs/<board>.config`, and passes it to `make` via `KCONFIG_CONFIG=...`. Your in-tree `.config` is never touched. Multiple boards, one Klipper checkout.
- Stops `klipper.service` once at the start of a flash batch, restarts once at the end. Not per board.
- Talks to `flashtool.py` over the wire (subprocess), not its internals. So when upstream Katapult releases something new, behavior tracks.
- Has a **bootloader-offset preflight** that runs `flashtool.py -s` against the chip and refuses to write a firmware built for the wrong offset. This exists because someone (me) bricked an expansion board on 2026-04-26 by feeding it firmware compiled for the wrong flash address. Now that's structurally impossible without `--force`.
- Has **hardware profiles** that encode "things that will brick this chip if you toggle them in menuconfig" (USB pins on packages that don't have those pins, crystal frequencies on boards with no crystal, etc). Per-MCU; warned at build, blocked at flash.

## Install

```bash
git clone git@github.com:matthewj301/katapult-helper.git
cd katapult-helper
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Python 3.9+. The Pi running klipper is 3.9, so we are too.

You'll also need:

- A Klipper or Kalico checkout somewhere (`~/klipper`, `~/git/kalico`, wherever).
- A Katapult checkout (`~/katapult`) — we shell out to its `scripts/flashtool.py`.
- Standard build tools: `make`, `gcc-arm-none-eabi`, `python3`, `pyserial`.
- `can-utils` and `can0` up if you have CAN toolheads.
- Your user in `dialout` and able to `sudo systemctl stop klipper` without a password prompt. The cleanest way:

  ```bash
  echo "$USER ALL=NOPASSWD: /bin/systemctl" | sudo tee /etc/sudoers.d/katapult-helper
  ```

  Without this, the tool will refuse to start and tell you why — it'd otherwise leave klipper holding `/dev/ttyACM*` and the flash would fail with "device already in use".

## Quick start

```bash
cp inventory.example.yaml inventory.yaml
$EDITOR inventory.yaml          # set klipper_repo / katapult_repo paths
katapult-helper wizard
```

The wizard:

1. Scans `/dev/serial/by-id/*` and CAN bus.
2. For any MCU it doesn't recognize, asks you to name it and pick a `.config` path. Writes the new entry back into `inventory.yaml` (with comments preserved).
3. For any board missing its `.config`, opens menuconfig with the relevant hints up front (bootloader offset, what NOT to set for that chip).
4. Stops klipper.
5. For each board: `make clean` → `make olddefconfig` → `make -j` → `flashtool.py …`.
6. Restarts klipper.

`--no-flash` does everything except the actual write — useful when you just want to make sure things compile.

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
| `katapult-helper wizard` | The whole pipeline. Discover → upsert → configure → build → flash. |
| `katapult-helper list` | Show the current inventory. |
| `katapult-helper discover [--raw] [--can-iface IFACE]` | Scan USB and CAN. `--raw` dumps the unparsed `flashtool.py -q` output, useful for spotting upstream format drift. |
| `katapult-helper configure [NAMES…] [--all-missing] [--force]` | Run menuconfig for selected boards. Backs up the existing `.config` first. Shows the relevant profile hints up front. |
| `katapult-helper build [NAMES…] [--menuconfig]` | Build only. Auto-runs menuconfig if there's no `.config` yet. |
| `katapult-helper flash [NAMES…] [-f path/to/klipper.bin] [--force]` | Flash only. `--force` skips the bootloader-offset preflight (don't use this unless you know what you're doing). |
| `katapult-helper run [NAMES…] [--force]` | Build + flash. |

`NAMES` is optional — leave it off to operate on all boards in the inventory.

Global flags:

- `-c, --inventory PATH` — alternate inventory file (default `./inventory.yaml`).
- `-v, --verbose` — DEBUG-level logging.

## Hardware profiles

Each board can name a `profile:` (defined in `katapult_helper/profiles.py`). Profiles encode hardware-truth — clock reference, USB pin choices, packaging quirks, the typical Katapult offset for the family. They drive:

- **Pre-flash offset check.** The tool reads `Application Start: 0xXXX` from `flashtool.py -s` and compares to your build's `CONFIG_FLASH_APPLICATION_ADDRESS`. Mismatch → abort, clean error, no write.
- **Post-build sanity warnings.** Parses your `.config` against the profile and yells about settings that will brick the chip (e.g. `STM32_USB_PA11_PA12=y` on an stm32f042x6 in TSSOP-20 — the package has no PA11/PA12 pins).
- **Better menuconfig hints.** When you run `configure`, the panel shows the required and forbidden settings for that MCU, so you don't accidentally toggle yourself into a brick.

Current profiles cover stm32f042x6 TSSOP-20, stm32g0b1xx with 8 MHz crystal (BTT MMB / EBB36 / EBB42), and stm32h723xx with 25 MHz crystal (Octopus Max EZ class). Adding a new board = adding one entry to that file.

If your board's MCU has a profile, validation runs automatically. If not (e.g. stm32f446xx — common but not yet covered), nothing breaks; you just don't get the extra layer of protection. PRs welcome.

## A note on bootloader offsets

| MCU family | Typical Katapult offset |
|---|---|
| `stm32f042x6` (32KB flash) | 8 KiB |
| `stm32f103xx` | 8 KiB |
| `stm32f405xx` / `stm32f446xx` | 32 KiB |
| `stm32g0b1xx` | 8 KiB |
| `stm32h723xx` | 128 KiB |
| `rp2040` | 16 KiB |

These match what most people build Katapult with. **The offset in your Klipper `.config` has to match the offset Katapult was built with — if Katapult sits at 0x08000000–0x08002000 and Klipper expects to start at 0x08001000, the chip will jump into the middle of Katapult and crash.** The preflight check catches this; if you see a `BOOTLOADER OFFSET MISMATCH` error, that's why.

## Development

```bash
pip install -e ".[dev]"
pytest                          # ~75 tests, no hardware needed
```

Tests live in `tests/`. Real captures from `flashtool.py -q` and `flashtool.py -s` against real boards live in `tests/fixtures/` so the parsers stay honest about upstream output. Subprocess calls to `make` and `flashtool.py` aren't mocked — the contract is the CLI invocation, and mocks drift; tests target the parsers, the YAML round-trip, the by-id resolution, and the error-translation paths.

## Status

Used in anger. Validated against live hardware (stm32f042x6 expansion board, stm32g0b1xx EBB36 on CAN, stm32h723xx Octopus). USB and CAN paths both exercised end-to-end. Recovered exactly one brick by writing the offset preflight that now prevents that brick.

Assumes systemd. Non-systemd hosts would need to swap out the `klipper_stopped()` context manager.

## License

See `LICENSE`.
