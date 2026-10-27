# Public README demo

The current screenshots and earlier walkthrough used native app captures with **entirely invented English data**. The scenario is an API retry fix, test and documentation reviews, and two other everyday development tasks. Projects under `/demo/`, conversations, commands, outputs, model summaries, agent identities, timestamps, speeds, and usage values are fixtures. They are not evidence of a real Codex run or a performance benchmark.

## Current README showcase: 2026-10-27

The bilingual README uses static images. Its lead image is source second 42 of the native desktop recording, chosen because the overview shows the token rate, three chats, and the complete main-agent and reviewer timeline together. The source was recorded without audio from the MacBook's built-in display at 3456 × 2234 using macOS `screencapture`. Computer Use operated the isolated Preview app. The static export crops the frame without adding presentation text; the native **Read-only preview** footer remains visible.

The lead image is followed by equally sized token-speed and activity-timeline crops and a wider activity-detail image. The speed image magnifies the actual menu bar item and pairs it with the overview. The tool-result image is a separate Computer Use native-window screenshot framed on the captured desktop wallpaper, rather than a frame from the desktop recording. No product controls, values, or task outcomes are synthesized during export.

The original local capture directory is ignored under `.local/readme-desktop-preview/`. Static export requires:

- `zoom-frame.png`: the full-resolution frame extracted at source second 42.
- `native-result.png`: the separate native activity viewer screenshot.
- `launch-settled.png`: the captured wallpaper used to frame screenshots.

The original `builtin-walkthrough.mov` also remains local, but is not required once the source frame has been extracted. With Pillow installed, export from the repository root:

```sh
.local/venv/bin/python -m pip install Pillow
.local/venv/bin/python experiments/readme-demo/export_desktop.py \
  --captures .local/readme-desktop-preview \
  --output docs/assets/showcase
```

Crop coordinates are specific to these captures. The exporter writes `desktop-overview.jpg`, `speed-overview.jpg`, `timeline.jpg`, `activity-detail.jpg`, and a resized copy of the repository's existing app icon. It does not generate a GIF or video. The earlier walkthrough and screenshots have been removed from `docs/assets`; their provenance remains in this experiment directory and their files remain recoverable from Git history.

[`docs/assets/showcase/provenance.json`](../../docs/assets/showcase/provenance.json) records source hashes, crop boundaries, the recording's build ID, and output hashes and sizes. The detail screenshot's build identity was not separately recorded and is left unknown. Raw desktop recordings and native captures stay local; only reviewed exports are public assets.

## Recreate the preview

Build the isolated preview as described in `CONTRIBUTING.md`, then run from the repository root:

```sh
python3 experiments/readme-demo/preview.py --output .local/readme-demo --keep-fresh
```

In another terminal:

```sh
open -n '.local/gui-preview/Codex Activity Monitor Preview.app' --args \
  --read-only --lang en --preview-light --show \
  --capture-status-to "$PWD/.local/readme-demo/status" \
  --snapshot "$PWD/.local/readme-demo/snapshot.json" \
  --timeline-fixture "$PWD/.local/readme-demo/timeline.json" \
  --data-root "$PWD/.local/readme-demo/data"
```

The Preview bundle enforces read-only mode. `--capture-status-to` is an opt-in native export of the real `NSStatusBarButton`, guarded by read-only mode plus explicit snapshot and timeline fixtures; normal daily use does not export images. The output directory contains timestamped PNG files and `latest.json` with the displayed value. The explicit snapshot, detail fixture, and data-root paths keep this capture separate from daily data and integration management. `preview.py` imports only the Python standard library; it does not import the collector, invoke Codex, read session history, or call `launchctl`.

The default scene is frozen at 54 seconds while freshness updates. Set `.local/readme-demo/control.json` to `{"seconds": 54}` to reset it. For advancing activity, add an `advance_from` Unix timestamp and `"animate_rate": true`. These controls fabricate display values; they do not measure anything.

## Full walkthrough capture and export: 2026-10-26

The 2026-10-26 capture used Codex Computer Use's `cua_repl` to select the **Preview app**, operate its controls, and save its native-window screenshots. The opt-in native export separately captures the actual status button as a transparent bitmap. No full-desktop recording was used. Frames were sampled at up to 8 per second into ignored `.local/readme-demo/frames/`. A frame manifest is an array of `{"file": "frame-00000.jpg", "chapter": "English caption", "at": 1793000000000}` records, with `at` in wall-clock milliseconds. The opening segment lives in `.local/readme-demo/speed-frames/`; each frame also has `status` (the matching native PNG basename) and `speed_title` (the displayed value). Frames in a chapter are consecutive; gaps between chapter captures are cut out.

The walkthrough opens with seven seconds of live menu bar speed updates, followed by the overview, chat selection, turn input, timeline zoom, a test command, its result, a file patch, and an advancing response. It has no audio.

Export with FFmpeg on PATH and Pillow installed in a development environment:

```sh
.local/venv/bin/python -m pip install Pillow
.local/venv/bin/python experiments/readme-demo/export_video.py \
  --frames .local/readme-demo/frames \
  --intro-frames .local/readme-demo/speed-frames \
  --status-image .local/readme-demo/status-still.png \
  --output .local/readme-demo/legacy-export
```

The export preserves native product controls, crops OS capture chrome and developer-only preview footers, and combines the native status-button bitmap with the native app window. A magnified copy of the same status bitmap makes speed updates readable. The menu bar backdrop is a neutral presentation surface; no other desktop apps, private conversations, or system-menu contents are captured. English captions describe features and actions. Capture-process notes stay in this development document and the JSON record. The MP4 uses H.264, 24 output frames per second, and a fast-start container; repeated frames retain the lower source capture cadence. The GIF is a smaller 6-fps alternative. Raw capture frames remain local and are not part of the public repository.

## Retired walkthrough and screenshots

The 2026-10-26 exports below were replaced by the current static showcase and removed from `docs/assets` on 2026-10-27. Their original provenance is preserved unchanged in [`provenance-2026-10-26.json`](provenance-2026-10-26.json), including file hashes, sizes, build identity, fixture hashes, and export properties. The old media can be recovered from Git history; the historical exporter writes to an ignored local directory in the command above.

- `overview.jpg`: native menu bar button and overview, with three invented chats and the complete compact subagent timeline.
- `timeline.jpg`: a focused crop of the overview's timeline, legend, and usage.
- `activity-detail.jpg`: native menu bar button and test result; OS capture chrome and the preview footer are cropped.
- `demo-poster.jpg`, `demo.mp4`, `demo.gif`: captioned walkthrough and its first-frame poster.

In this 2026-10-26 set, developer-only **Read-only preview** and **Synthetic preview records** footers are cropped. The fixture's assertions about tests passing and code changes describe the invented story, not operations performed on a real project. When refreshing assets, recheck the scene, language, identities, and capture boundary before replacing the corresponding provenance record.
