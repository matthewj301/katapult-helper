# katapult-helper — project memory for Claude Code

A Python CLI that batch-builds and flashes Klipper / Kalico firmware to a fleet of MCUs running the [Katapult](https://github.com/Arksine/katapult) bootloader. Driven by an inventory YAML keyed on chip UIDs.

## Layout

Flat package, no `src/`:

```
katapult-helper/
├── README.md
├── CLAUDE.md
├── LICENSE
├── pyproject.toml          # hatch backend, packages = ["katapult_helper"]
├── inventory.example.yaml  # committed; inventory.yaml is gitignored
├── tests/                  # pytest suite, no hardware required
│   └── fixtures/           # verbatim live captures from real flashtool.py -q and -s
└── katapult_helper/
    ├── __init__.py
    ├── _proc.py            # shared run() wrapper, klipper_stopped() context mgr, BY_ID, is_katapult_mode
    ├── cli.py              # click group + commands: list, discover, configure, build, flash, run, wizard
    ├── inventory.py        # ruamel.yaml round-trip; Board / Inventory; InventoryError; load_raw / save_raw / upsert_board
    ├── discover.py         # /dev/serial/by-id parser + flashtool.py -q wrapper (parse_can_query_output)
    ├── configure.py        # menuconfig walkthrough w/ MCU-family hint panel
    ├── build.py            # make clean → olddefconfig|menuconfig → make -j with KCONFIG_CONFIG
    ├── flash.py            # USB by-id resolution + flashtool.py invocation + offset preflight
    ├── profiles.py         # hardware-truth profiles per chip/board: required/incompatible Kconfig + katapult_offsets
    └── wizard.py           # one-shot: discover → upsert → configure missing → build → flash
```

## Tech stack

- **Python ≥ 3.9** is the floor — RaspberryPi OS Bullseye/Bookworm ships 3.9.2, and Klipper installs use it. Don't introduce 3.10+ syntax (PEP 604 unions, PEP 634 match) at runtime — only in annotations, which `from __future__ import annotations` (required as the first import of every module) stringifies.
- **click** for the CLI (groups, `pass_context`, `type=click.Path(path_type=Path)`). User-facing failures use `click.ClickException` / `click.UsageError`, never raw tracebacks.
- **loguru** for logging (single `logger.add(sys.stderr, ...)` in `cli._configure_logging`).
- **ruamel.yaml** in default (round-trip) mode — preserves comments when the wizard appends entries.
- **rich** for tables (`list`, `discover`) and panels (`configure` walkthrough).
- **pyserial** is a declared dependency that our code never imports. flashtool.py runs under `sys.executable` (this venv) and imports `serial` itself for USB flashing — don't remove pyserial just because grep finds no import.
- **No** `python-can`, no `pyyaml`, no `pydantic` — keep deps small.
- Dev: `pip install -e ".[dev]"` adds `pytest`. `pip` ≥ 21.3 required for editable installs from a pure-`pyproject.toml` package; older RpiOS pip needs `python -m pip install --upgrade pip` first.

## Non-obvious invariants (don't break these)

1. **Stable identity is the chip UID, not the by-id path.** The `/dev/serial/by-id/*` symlink prefix flips between `usb-katapult_*` and `usb-Klipper_*` when an MCU reboots. Inventory keys USB boards by `chip_uid` and resolves the live path at flash time via `flash.resolve_usb_path`. Never store the full by-id path as the primary key.
2. **Hardware profiles encode bricking-prevention rules.** `profiles.py` keys on chip-or-board (e.g. `stm32f042x6-tssop20`) and lists `required` / `incompatible` / `recommended` Kconfig settings plus `katapult_offsets`. `build` parses the `.config` and warns on violations; `flash` runs `flashtool.py -s` to read the chip's actual `Application Start: 0xXXX`, compares to the build's `CONFIG_FLASH_APPLICATION_ADDRESS`, and **aborts on offset mismatch unless `--force` is passed.** This is the structural defense against the 2026-04-26 expansion-board brick. Don't loosen the abort behavior. Add new profiles as new boards are validated; profiles are *hardware truth* (clock ref, USB pin remap, CAN pin choice), never user feature choices like `WANT_NEOPIXEL`.
3. **Klipper builds are in-tree.** `.config` and `out/` live at the repo root; the Makefile has no `O=` override. We isolate per-board state by passing `KCONFIG_CONFIG=<path>` so the in-tree `.config` is never mutated. RP2040 + Katapult emits `out/klipper.bin` (not `.uf2`) — Katapult flashes the `.bin`.
4. **Sequential build → flash.** Don't try to parallelize. If the user ever asks for parallelism, do it via `git worktree add` per board, not by trying to multiplex the in-tree build.
5. **`klipper.service` lifecycle is bracketed once.** Stop at the start of any flash-touching command, restart in `finally:` at the end of the whole batch — never per board. The `wizard`, `flash`, and `run` commands all use `with klipper_stopped(): …` from `_proc.py`. `_systemctl_stop` uses `sudo -n` (non-interactive) and raises `click.ClickException` with NOPASSWD-sudoers instructions if the stop fails — silently ignoring the failure leads to "device already in use" from flashtool.py. `_systemctl_start` is best-effort (runs in `finally`, so a failed start is logged as a warning, not raised).
6. **Subprocess-only contract with `flashtool.py` and `make`.** Don't import Katapult internals — that file is not a package and has no version pinning. CLI invocation tracks upstream releases. stdout/stderr stream straight to the terminal (no capture) so menuconfig and progress bars Just Work. **All `subprocess.CalledProcessError` from `make`/`flashtool.py` is caught at module boundaries (`build_board`, `flash_board`) and translated into `click.ClickException` with hint text** — users see an actionable message, not a stack trace. Don't bypass this.
7. **`make olddefconfig`, never `make defconfig` against an existing file.** When `.config` exists, `build.py` runs `olddefconfig` (validates + fills defaults, preserves user choices). Only `configure` may invoke `menuconfig`.
8. **Inventory round-trip.** When the wizard adds a discovered MCU, it loads via `load_raw` → mutates via `upsert_board` → writes via `save_raw`. This preserves the user's comments and ordering. Don't switch the YAML loader to `safe` mode for writes.
9. **All `cli.py` commands route through `_load(ctx)`,** which translates `InventoryError` → `click.UsageError` for malformed YAML. The `wizard` command also passes the loaded `Inventory` into `run_wizard(inv=…)` to avoid a second parse. New commands must follow this — never call `load_inventory` directly from a click handler.
10. **`build_inventory` rejects null/empty `klipper_repo`.** A YAML value like `klipper_repo: ~` parses as `None`, which would otherwise produce `Path("None")` and explode later. The check is `not raw.get("klipper_repo")`; don't loosen it.

## Commands

Entry point is `katapult-helper` (defined in `pyproject.toml [project.scripts]`).

- `wizard` — full-circle: discover unknown MCUs, prompt to register them, walk through menuconfig for any missing `.config`, build, flash. The intended "run it once and shit gets updated" UX.
- `list`, `discover`, `configure`, `build`, `flash`, `run` — individual phases for surgical use.

`-c/--inventory` defaults to `./inventory.yaml` and uses `exists=True`, so users must `cp inventory.example.yaml inventory.yaml` first. This is intentional; don't relax it.

## Conventions

- All paths use `Path` and `.expanduser()` at the I/O boundary; internal code assumes already-expanded paths.
- Logging style: `logger.info("[{}] doing thing", board.name)` so every line is greppable by board.
- Tests live under `tests/`. Real-output captures (live `flashtool.py -q` stdout) live under `tests/fixtures/` and are checked in — they pin our regex against actual upstream behavior. **Don't mock `subprocess.run` against `make` or `flashtool.py`** — the contract is the CLI invocation, and mocks drift. Mock at the module-boundary helpers (`build.run`, `flash.run`) when testing the error-translation paths, but never to fake make output. Target `inventory.py` (load/save/upsert), the regex parsers in `discover.py`, and the CalledProcessError → ClickException translations in `build.py` / `flash.py`. Use `click.testing.CliRunner` for CLI-level error UX (no Traceback, friendly message).
- Keep CLI commands thin; logic lives in module-level functions so tests can call them without invoking click.
- Every module under `katapult_helper/` and `tests/` has `from __future__ import annotations` as its first import (after the docstring if there is one; trivial `__init__.py`s are exempt). This is required for 3.9 compatibility of `X | None` annotations.

## Things to avoid

- Adding a daemon mode, a web UI, or a Moonraker plugin until someone asks. v1 is the CLI.
- Fallbacks for "what if `make` isn't installed" beyond the existing `ensure_make_available` UsageError. Trust the host.
- Splitting the wizard into multiple passes — it's intentionally one command that may prompt multiple times.
- "Convenience" features that flip Kconfig values automatically. Bootloader-offset mistakes brick boards; menuconfig is the source of truth.

## Reference repos on this host

- `~/git/kalico` — Kalico (Klipper fork) checkout; canonical reference for `make olddefconfig` headless build pattern (`scripts/ci-build.sh`).
- `~/git/3d-printing-guides/docs/katapult-firmware-guide.md` — the user's existing manual workflow doc; informed the inventory schema.
- `~/git/klipper-setup-scripts` — host-side setup automation (dialout group, ModemManager disable, dfu-util install). katapult-helper assumes this has been run.
- Upstream `Arksine/katapult` is **not** cloned locally; CI/dev should treat it as an external dependency at the path in `inventory.yaml#katapult_repo`.

## Live hardware (for validation, not for casual testing)

- `pi@192.168.50.95` — doomcube Pi. Two USB-attached MCUs (stm32f042x6 expansion board, stm32g0b1xx). Klipper installed at `/home/pi/klipper`, Katapult at `/home/pi/katapult`. katapult-helper installed at `/home/pi/katapult-helper` in a venv at `.venv/`. Has NOPASSWD sudoers configured for systemctl. Real Python 3.9.2 — use this Pi to verify 3.9 compat.
- `pi@192.168.30.77` — Vcore Pi. Live `can0` with an EBB36 toolhead (UUID `1586f2c37eaf`). Useful for live `flashtool.py -q` validation, but be aware: sending `-r` to the EBB36 enters Katapult mode without an auto-return — only re-flash or power-cycle brings it back.

When validating against either Pi, default to **read-only** operations. State changes (`-r`, `make flash`, systemctl restart) need explicit per-session authorization from the user.
