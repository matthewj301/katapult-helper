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
└── katapult_helper/
    ├── __init__.py
    ├── cli.py              # click group + commands: list, discover, configure, build, flash, run, wizard
    ├── inventory.py        # ruamel.yaml round-trip; Board / Inventory dataclasses; load_raw / save_raw / upsert_board
    ├── discover.py         # /dev/serial/by-id parser + flashtool.py -q wrapper
    ├── configure.py        # menuconfig walkthrough w/ MCU-family hint panel
    ├── build.py            # make clean → olddefconfig|menuconfig → make -j with KCONFIG_CONFIG
    ├── flash.py            # USB by-id resolution + flashtool.py invocation
    └── wizard.py           # one-shot: discover → upsert → configure missing → build → flash
```

## Tech stack

- Python ≥ 3.11. Use modern stdlib (`tomllib`, `Path`, `match` where it helps).
- **click** for the CLI (groups, `pass_context`, `type=click.Path(path_type=Path)`).
- **loguru** for logging (single `logger.add(sys.stderr, ...)` in `cli._configure_logging`).
- **ruamel.yaml** in default (round-trip) mode — preserves comments when the wizard appends entries.
- **rich** for tables (`list`, `discover`) and panels (`configure` walkthrough).
- **No** `python-can`, no `pyyaml`, no `pydantic` — keep deps small.

## Non-obvious invariants (don't break these)

1. **Stable identity is the chip UID, not the by-id path.** The `/dev/serial/by-id/*` symlink prefix flips between `usb-katapult_*` and `usb-Klipper_*` when an MCU reboots. Inventory keys USB boards by `chip_uid` and resolves the live path at flash time via `flash.resolve_usb_path`. Never store the full by-id path as the primary key.
2. **Klipper builds are in-tree.** `.config` and `out/` live at the repo root; the Makefile has no `O=` override. We isolate per-board state by passing `KCONFIG_CONFIG=<path>` so the in-tree `.config` is never mutated. RP2040 + Katapult emits `out/klipper.bin` (not `.uf2`) — Katapult flashes the `.bin`.
3. **Sequential build → flash.** Don't try to parallelize. If the user ever asks for parallelism, do it via `git worktree add` per board, not by trying to multiplex the in-tree build.
4. **`klipper.service` lifecycle is bracketed once.** Stop at the start of any flash-touching command, restart in `finally:` at the end of the whole batch — never per board. The `wizard` and `flash` and `run` commands all follow this; the helper is `_systemctl` in `cli.py`.
5. **Subprocess-only contract with `flashtool.py` and `make`.** Don't import Katapult internals — that file is not a package and has no version pinning. CLI invocation tracks upstream releases. stdout/stderr stream straight to the terminal (no capture) so menuconfig and progress bars Just Work.
6. **`make olddefconfig`, never `make defconfig` against an existing file.** When `.config` exists, `build.py` runs `olddefconfig` (validates + fills defaults, preserves user choices). Only `configure` may invoke `menuconfig`.
7. **Inventory round-trip.** When the wizard adds a discovered MCU, it loads via `load_raw` → mutates via `upsert_board` → writes via `save_raw`. This preserves the user's comments and ordering. Don't switch the YAML loader to `safe` mode for writes.

## Commands

Entry point is `katapult-helper` (defined in `pyproject.toml [project.scripts]`).

- `wizard` — full-circle: discover unknown MCUs, prompt to register them, walk through menuconfig for any missing `.config`, build, flash. The intended "run it once and shit gets updated" UX.
- `list`, `discover`, `configure`, `build`, `flash`, `run` — individual phases for surgical use.

`-c/--inventory` defaults to `./inventory.yaml` and uses `exists=True`, so users must `cp inventory.example.yaml inventory.yaml` first. This is intentional; don't relax it.

## Conventions

- All paths use `Path` and `.expanduser()` at the I/O boundary; internal code assumes already-expanded paths.
- Logging style: `logger.info("[{}] doing thing", board.name)` so every line is greppable by board.
- Don't write tests that mock `subprocess.run` against `make` or `flashtool.py` — the contract is the actual command-line, and mocks drift. If we add tests, target `inventory.py` (load/save/upsert) and the regex parsers in `discover.py`.
- Keep CLI commands thin; logic lives in module-level functions so tests can call them without invoking click.

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
