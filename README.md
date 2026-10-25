# Codex Progress Monitor

[简体中文](README.zh-CN.md) · [Contributing](CONTRIBUTING.md) · [Architecture](docs/18_engineering_hardening/implemented-architecture.md) · [Docs](docs/README.md)

A local macOS menu bar companion for Codex Desktop. See what your connected chats are doing, inspect model and tool activity on a timeline, and view received-text token speed alongside official turn usage.

This is a developer-build project. A standalone, signed release package is not available yet. The open-source license and release checks are still pending; see the [review and release checklist](docs/17_review_and_refactor/review.md).

## What it shows

- Current chat activity, elapsed time, and received text / tool-input token speed.
- Official Output and Reasoning token usage for the selected turn. Output includes Reasoning.
- Separate model and tool timeline lanes, explicitly linked subagents, and an independent activity viewer.
- Source-preserving tool parameters and results, with explicit partial, truncated, and unavailable states.
- English and Simplified Chinese interfaces.

Token speed measures locally received visible text. It does not measure hidden reasoning or server compute speed. Unknown or incomplete measurements display `—`.

## Build and run

Requirements: macOS 26+, Python 3.11+, Xcode 26+ with the Swift toolchain, and a local Codex Desktop installation. The default CLI path is inside `/Applications/ChatGPT.app`; a different installation needs an explicit original CLI path.

```sh
python3 -m venv .local/venv
.local/venv/bin/python -m pip install -e .
bin/build-gui
bin/run-gui
```

Launching the daily app configures a local CLI wrapper through the macOS GUI-session environment and manages its collector. Finish active Codex tasks, then quit and reopen Codex Desktop to use the integration. The app's settings can restore the previous configuration, preserve later user changes, and clean up owned data after Codex exits. Closing Monitor pauses observation in GUI-managed runtimes.

For development, build the separate read-only preview instead:

```sh
bin/build-gui --preview
open '.local/gui-preview/Codex Monitor Preview.app'
```

With the daily GUI running, the terminal can read the same collector:

```sh
bin/codex-progress watch --lang en
bin/codex-progress status
```

Detailed [GUI usage](docs/06_gui_app_options_and_decisions/usage.md) and [process lifecycle](docs/02_product_mvp/startup-and-processes.md) explain integration and restoration. These historical documents are in Chinese and may reference local validation evidence.

## How it works

```text
Codex Desktop ⇄ stdio wrapper ⇄ original Codex app-server
                       │
                 bounded event copy
                       ↓
                Python collector → state.sock → Swift menu bar app
                       │                            │
                 phase metadata                read-only queries
                       ↓                            ↓
                SQLite sidecar ← timeline query → Codex session JSONL
```

The wrapper keeps the original backend and forwards its stdio. Observation uses a separate bounded queue and Unix socket. The collector combines protocol lifecycle events and selected upstream deltas; the GUI subscribes to full state and nonce-bound health through `state.sock`. A bounded worker writes disposable recovery checkpoints independently. Read-only JSONL history reconciles persisted activity. The sidecar stores phase metadata, while full historical content stays in Codex-owned files.

Monitor keeps local metadata, snapshots, and integration backups. Snapshots can contain chat titles, paths, command previews, and numeric metrics; they are not anonymous. Transient text is used for local tokenization and selected live details. Monitor does not upload conversations. Dependency installation and the tokenizer's initial asset download may require network access. Keep runtime data out of issue reports and Git.

## Development and limitations

See [CONTRIBUTING.md](CONTRIBUTING.md) for checks and safe fixtures. The product is in `src/`, `macos/Sources/`, and `bin/`; numbered `docs/` folders preserve decisions and validation history. `experiments/` and `analysis/` are research material. `.local/` is ignored machine state.

Current limits include local observed chats only, version-dependent upstream trace parsing, and no fresh-machine or distribution acceptance yet. The state transport has isolated native acceptance, and the daily GUI/collector/resident-bridge update has been verified after a full Desktop restart; new natural-use samples remain pending. Tests and local builds do not establish long-run, Remote, signing, notarization, or asset-distribution readiness. The [baseline review](docs/17_review_and_refactor/review.md), [current implementation record](docs/18_engineering_hardening/implementation-results.md), and [remaining work](docs/TODO.md) track those boundaries.
