# Contributing

Start with the [architecture and reading guide](dev-history/18_engineering_hardening/implemented-architecture.md). The [baseline review](dev-history/17_review_and_refactor/review.md) preserves the original findings and fixes. Current engineering work is tracked in [Chapter 18](dev-history/18_engineering_hardening/README.md); [release preparation](dev-history/18_engineering_hardening/requirements-and-decisions.md#发行准备与维护者决策) remains a separate acceptance scope.

The [development history index](dev-history/README.md) organizes iteration-specific plans and evidence. These records describe their stated versions and are archived after development, rather than maintained as a current product manual. [Chapter 18](dev-history/18_engineering_hardening/README.md) records the six engineering improvements; its implementation record distinguishes validated behavior from designs and acceptance still in progress.

## Development

Use macOS 26+, Python 3.11+, and Xcode 26+ with its Swift toolchain. Create the local environment and install the package:

```sh
python3 -m venv .local/venv
.local/venv/bin/python -m pip install -e .
.local/venv/bin/python -m unittest discover -s tests -p 'test_*.py'
.local/venv/bin/python bin/export-localizations --check
swift test --package-path macos
bin/build-gui --preview
```

The default test suites use synthetic data, temporary runtimes, and injected management services. They do not require a Codex account or configure your Desktop. Tests requiring a window session are opt-in via `MONITOR_WINDOW_TESTS=1`; live experiments in `experiments/` and the other scripts in `tests/` are separate acceptance tools. Read a live script before using it.

The preview bundle is `.local/gui-preview/Codex Monitor Preview.app`. It defaults to read-only operation. Do not use the daily app or real `launchctl` environment as a development fixture.

## Changes

- Keep evidence, state, and presentation separate. Associate activity by scoped protocol identity, never by name or nearby timestamps.
- Use `—` for unknown values and preserve a known zero. Received text speed and official token usage are different measurements.
- Add a regression scenario for a behavioral bug. Test the failure mode and relevant counterexamples; avoid tests that merely mirror a helper's implementation.
- Prefer small domain modules and explicit ownership over new generic frameworks. Use normal Python / Swift formatting and explain non-obvious protocol constraints where they apply.
- Add message keys to both Python catalogs. Run `bin/export-localizations` to regenerate shared Swift resources. UI strings must cover both `en` and `zh-Hans`; source content is never translated.
- Do not commit `.local/`, login state, databases, raw event streams, conversation text, or screenshots of private chats. Synthetic fixtures should contain only invented content.

Describe the concrete behavior changed, the checks run, and any unverified integration boundaries in a pull request. Upstream protocol changes need a sanitized fixture and a stated compatibility boundary.

## Repository scope

`src/`, `macos/Sources/`, and `bin/` form the product. `tests/test_*.py` and `macos/Tests/` are regression suites. Numbered documentation records the product's evolution; experiments and historical validation reports are not required to build the app. Archived reports can reference local evidence that is intentionally absent from a public checkout.

## License

This project is licensed under the [MIT License](LICENSE), copyright (c) 2026 Hanzhang Liu. Preserve applicable copyright and license notices when reusing or redistributing the software. Third-party dependencies and externally sourced materials retain their own licenses; their provenance and redistribution scope must still be checked before public release.
