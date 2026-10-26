# Public README demo

The public screenshots and walkthrough use the current native app with **entirely invented English data**. The scenario is an API retry fix, test and documentation reviews, and two other everyday development tasks. Projects under `/demo/`, conversations, commands, outputs, model summaries, agent identities, timestamps, speeds, and usage values are fixtures. They are not evidence of a real Codex run or a performance benchmark.

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

## Capture and export

The 2026-10-26 capture used Codex Computer Use's `cua_repl` to select the **Preview app**, operate its controls, and save its native-window screenshots. The opt-in native export separately captures the actual status button as a transparent bitmap. No full-desktop recording was used. Frames were sampled at up to 8 per second into ignored `.local/readme-demo/frames/`. A frame manifest is an array of `{"file": "frame-00000.jpg", "chapter": "English caption", "at": 1793000000000}` records, with `at` in wall-clock milliseconds. The opening segment lives in `.local/readme-demo/speed-frames/`; each frame also has `status` (the matching native PNG basename) and `speed_title` (the displayed value). Frames in a chapter are consecutive; gaps between chapter captures are cut out.

The walkthrough opens with seven seconds of live menu bar speed updates, followed by the overview, chat selection, turn input, timeline zoom, a test command, its result, a file patch, and an advancing response. It has no audio.

Export with FFmpeg on PATH and Pillow installed in a development environment:

```sh
.local/venv/bin/python -m pip install Pillow
.local/venv/bin/python experiments/readme-demo/export_video.py \
  --frames .local/readme-demo/frames \
  --intro-frames .local/readme-demo/speed-frames \
  --status-image .local/readme-demo/status-still.png \
  --output docs/assets
```

The export preserves native product controls, crops OS capture chrome and developer-only preview footers, and combines the native status-button bitmap with the native app window. A magnified copy of the same status bitmap makes speed updates readable. The menu bar backdrop is a neutral presentation surface; no other desktop apps, private conversations, or system-menu contents are captured. English captions describe features and actions. Capture-process notes stay in this development document and the JSON record. The MP4 uses H.264, 24 output frames per second, and a fast-start container; repeated frames retain the lower source capture cadence. The GIF is a smaller 6-fps alternative. Raw capture frames remain local and are not part of the public repository.

## Public assets

See [`docs/assets/provenance.json`](../../docs/assets/provenance.json) for file hashes, sizes, build identity, fixture hashes, and export properties.

- `overview.jpg`: native menu bar button and overview, with three invented chats and the complete compact subagent timeline.
- `timeline.jpg`: a focused crop of the overview's timeline, legend, and usage.
- `activity-detail.jpg`: native menu bar button and test result; OS capture chrome and the preview footer are cropped.
- `demo-poster.jpg`, `demo.mp4`, `demo.gif`: captioned walkthrough and its first-frame poster.

Developer-only **Read-only preview** and **Synthetic preview records** footers are cropped from public media. The fixture's assertions about tests passing and code changes describe the invented story, not operations performed on a real project. When refreshing assets, recheck the scene, language, identities, and capture boundary before replacing the provenance record.
