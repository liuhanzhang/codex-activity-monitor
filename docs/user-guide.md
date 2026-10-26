# User guide

[English README](../README.md) · [中文 README](../README.zh-CN.md) · [Architecture](architecture.md)

The current app supports local Codex Desktop observation on macOS 26+. See the README for build requirements and first-launch instructions. Interface labels below use English; the gear menu can switch the app to Simplified Chinese.

## Read the menu bar

| Display | Meaning |
| --- | --- |
| Number in `T/s` | Token generation speed, calculated from text and tool input received during the latest one-second window |
| Animated thinking dots | One active root chat is thinking |
| Tool animation | One active root chat is executing tools |
| Idle icon | A connected backend has no active chats |
| `—` | A measurement is unknown, not ready, incomplete, disconnected, or stale |

With multiple active root chats, the menu bar shows their available aggregate speed. Explicitly associated subagents belong to their parent's group. A zero is a known measurement, not proof that work has stopped. The app cannot measure hidden reasoning speed.

## Use the overview and timeline

Click the menu bar item, then select a chat. The list shows its title, project, activity, duration, and token generation speed. Recently completed chats can be expanded below the active list.

The selected chat has a turn selector, a speech-bubble button for its input, and a timeline. Each agent has a model lane above a tool lane. Activities are positioned by recorded times; colored blocks distinguish reasoning, responses, tool input, edits, and tool execution. Short or overlapping activities remain selectable; the viewer can open a group of overlapping tools.

Use `−` and `+` to change the reference scale between 30, 60, and 120 seconds. The displayed visible range can be shorter because agent labels and the window occupy space. Horizontal scrolling pauses automatic following; **Back to latest** resumes it.

Click an activity to open **Activity viewer**. Its tabs expose the source content available for that activity: input, output, arguments, results, or a file change. **Copy** copies the selected section; for paged content it copies what has been loaded. Unknown, partial, truncated, changed, and unavailable content have explicit states. A reasoning block without a visible summary can have an empty content area.

Output and Reasoning counts belong to the selected turn when its identity is known. **Output includes Reasoning: do not add them together.** Counts are separate from locally measured generation speed. The statistics information button explains their scope.

## Settings

Open the gear menu at the top of the overview:

| Setting | Effect |
| --- | --- |
| **Language** | Switch between English and 简体中文 immediately; original conversation content is unchanged |
| **Configure** | Enable or repair the local integration; finish active tasks and reopen Codex to use the new process configuration |
| **Launch at login** | Register or unregister the monitor's macOS login item; initially enabled |
| **Restore configuration…** | Undo integration, optionally removing the monitor's local data for uninstall |
| **Quit Codex Activity Monitor** | Stop observation and keep the integration configuration |

The monitor configures the macOS GUI-session environment and saves the values it replaces. It does not require you to edit a shell startup file. The [architecture guide](architecture.md#integration-changes) lists the affected variables. Changing these variables yourself while integrated can change what restoration leaves in place.

## Disconnect without uninstalling

1. Open **Settings → Restore configuration…**.
2. Leave **Remove local data and quit to uninstall** unchecked.
3. Choose **Restore configuration**.
4. Finish active tasks and quit Codex Desktop when the monitor asks it to exit. Leave the monitor open until cleanup completes.
5. Reopen Codex Desktop to use the restored configuration.

App preferences remain. Reopening the monitor keeps integration disabled; choose **Configure** when you want to enable it again. Restoration only reverts environment values still owned by this integration; later user changes are preserved.

## Uninstall

1. Open **Settings → Restore configuration…**.
2. Check **Remove local data and quit to uninstall** and choose **Restore configuration**.
3. Finish tasks and fully quit Codex Desktop. The monitor restores configuration first, then waits until it can safely stop its owned runtime and clean up.
4. Wait for the monitor to finish cleaning up and quit itself.
5. Move **Codex Activity Monitor.app** to Trash.

Cleanup removes owned runtime state, derived timeline data, integration records, preferences, and the login item. It does not remove Codex-owned conversations, source checkouts, or development virtual environments. Unknown files or active processes can prevent cleanup; follow the displayed error instead of deleting the configuration backup.

If the monitor exits before cleanup completes, reopen the same app to continue. If you already deleted it, restore or rebuild the app and use the restoration flow. Deleting the `.app` alone is not an uninstall: an external launcher can fall back to the original CLI, but configuration and data still need cleanup.

## Local data

The default GUI data directory is:

```text
~/Library/Application Support/CodexActivityMonitor/
```

It contains preferences, integration backups and management state, a launcher, runtime files, and derived timeline metadata. A migrated terminal installation may retain its previous runtime path; do not move it while Codex processes still use it.

The source build also uses the ignored `.local/` directory for its virtual environment, build products, and tokenizer cache. The standalone app bundles its runtime dependencies. Neither installation changes ownership of Codex's own session history.

Local files can include titles, paths, command previews, and metrics. Backups can contain environment values. Review them before sharing; do not attach an entire data directory to a bug report.

## Troubleshooting

### The overview has no chats

Confirm that the monitor is configured and running. Finish active Codex work, fully quit Codex Desktop, reopen it, and start a new local task. Already running processes do not inherit later environment changes. Cloud or remote sessions outside the configured backend are not covered by the quick start.

### Speed displays `—` or `0`

`—` indicates unavailable information. Read the connection indicator or observation notice for the specific cause. `0` is a known zero in the most recent window; reasoning, waiting, and tool work can continue without incoming visible text. A historical gap can coexist with a valid current per-stream rate.

### A tool result or reasoning summary is missing

Only source content that Codex exposes and the monitor can read is available. Some reasoning activities have no visible summary. Tool results may not yet be persisted, may be source-truncated, or may be unavailable; the activity viewer distinguishes these states where evidence permits.

### Updates or moving the app have not taken effect

Use **Configure** from the app's current location. Finish tasks and fully restart Codex Desktop so its new wrappers use the updated integration. A source build still depends on its original checkout and virtual environment.

### Restoration is waiting for Codex to exit

Finish work, then fully quit Codex Desktop. Leave the monitor running so it can complete cleanup. It does not force-quit your tasks. Reopening the monitor continues pending cleanup after an interruption.

### Codex is installed somewhere else

The current GUI assumes the original CLI path shown in the README and has no path picker. The command-line management layer has a `--real-cli` option, but that is a custom integration path, not a supported GUI quick-start recipe. Include the installation layout in a bug report or contribution proposal.

## Optional terminal view

With the daily GUI running, a source checkout can read the same collector:

```sh
bin/codex-activity-monitor watch --lang en
bin/codex-activity-monitor status
```

For a custom or migrated runtime, pass `--runtime /absolute/path/to/runtime` before the subcommand. Once the GUI manages integration, use the app for configuration and restoration; the legacy `setup` / `uninstall` commands refuse to overwrite GUI-managed state. The terminal view does not keep GUI-managed observation running after the GUI quits.
