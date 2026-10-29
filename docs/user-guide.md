# User guide

[English README](../README.md) · [中文 README](../README.zh-CN.md) · [Architecture](architecture.md)

The current app targets local Codex Desktop observation on macOS 14+. The deployment target is checked at build time; settings runtime validation on macOS 14/15 remains pending. See the README for build requirements and first-launch instructions. Interface labels below use English; Settings can switch the app to Simplified Chinese.

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

**Average speed**, at the right of the statistics row, divides official output tokens by the sum of their matched request windows in the selected turn. It updates as usage records arrive, holds between updates, and remains available after completion. Output includes reasoning; request windows can include time waiting for the first token, queueing, and network delays. This is an estimate from JSONL records. Partial coverage is explained in the statistics information popover; `—` means no valid estimate is available. Historical averages can remain readable while live observation is unavailable.

## Settings

Click the gear button at the top of the overview, or press **Command-,**, to open Settings. Its sidebar has **General**, **Runtime Status**, **Logs**, and **About**. Reopening focuses the same window. **Command-W** closes Settings and keeps observation running; **Command-Q** quits Monitor.

| Setting | Effect |
| --- | --- |
| **General → Language** | Switch between English and 简体中文 immediately; original conversation content is unchanged |
| **General → Codex** | Turn on to apply integration; turn off to restore configuration and stop observation, keeping preferences and diagnostic logs |
| **General → Launch at login** | Register or unregister the monitor's macOS login item; initially enabled |
| **General → Maintenance** | Separate uninstall confirmation and quit actions |
| **Runtime Status** | Show detected component health, unknown or disabled states, and links to related logs |
| **Logs** | Search and filter local events by level and source, select details, copy a record, or open the log folder |
| **About** | Show the full release version, build number, project links, and local licenses |
| **About → Software Updates** | Enable or disable automatic checks, check manually, and open a newer release for download |

Automatic update checks use Sparkle and are enabled by default, with a one-day schedule. A green upward arrow appears immediately left of the overview Settings button and at the trailing edge of the About sidebar row when a newer version is known. Click the overview arrow to select About and scroll to Software Updates. Choose **View Update** there to bring up Sparkle's unchanged update window, then open the release page to download manually. Opening About, choosing Remind Me Later, and restarting the app preserve the indicator. Upgrading or explicitly skipping that version clears it. Cached update notices belong to their update feed and are not restored into Preview builds. Downloading does not replace the running app. Network failures do not interrupt observation; manual checks show errors. **Last Check** records a check attempt, including an unsuccessful one.

Checks fetch a static HTTPS appcast from the project's update site. They do not call the GitHub Releases API or send account credentials, chat content, diagnostics, or usage records. The hosting provider receives the request and normal connection metadata. Sparkle stores preferences in an app-specific macOS preferences domain; the old `updates.json` automatic-check choice is migrated once. Uninstall cleanup removes the update preferences. Preview and disposable test profiles do not check online or read daily update preferences. The dedicated update acceptance harness is an explicit exception limited to a unique temporary bundle and either a loopback server or the three allowlisted public project feeds.

The explicitly configured GitHub release type selects the default update feed: release builds receive only releases; prerelease builds also receive prereleases. Version-name suffixes such as alpha or preview do not select the channel. The old appcast-alpha.xml address remains a compatible copy of the prerelease feed. The publishing script verifies that listed releases have uploaded macOS arm64 archives. Sparkle compares build numbers and system requirements. The deployment workflow and feeds must be published before the configured update-site URL becomes available; local build success alone does not establish that the hosted service is online.

First launch continues to enable integration automatically. The Codex switch represents confirmed configuration; its message separately explains connection and restart requirements. **Configured** is not proof that already running Codex processes use it. **Active** requires a fresh matching bridge connection and covers confirmed local connections. When requested, finish tasks, fully quit Codex, and reopen it. Monitor does not force-quit Codex.

If a check fails or configuration has changed, the switch may be unavailable until the state is verified. Use **Check or Reconnect**, **Reconfigure Integration**, or **Continue Restoration** as offered. Operation errors remain visible independently of the confirmed configuration.

The monitor configures the macOS GUI-session environment and saves the values it replaces. It does not require you to edit a shell startup file. The [architecture guide](architecture.md#integration-changes) lists the affected variables. Changing these variables yourself while integrated can change what restoration leaves in place.

## Disconnect without uninstalling

1. Turn off **Settings → General → Codex**.
2. Finish active tasks and quit Codex Desktop when the monitor asks it to exit. Leave the monitor open until cleanup completes.
3. Reopen Codex Desktop to use the restored configuration.

App preferences and structured diagnostic logs remain. Reopening the monitor keeps integration disabled; turn the Codex switch on when you want to enable it again. Restoration only reverts environment values still owned by this integration; later user changes are preserved.

## Uninstall

1. Open **Settings → General → Maintenance**.
2. Choose **Remove local data and quit to uninstall**, then confirm **Restore and Remove Data**.
3. Finish tasks and fully quit Codex Desktop. The monitor restores configuration first, then waits until it can safely stop its owned runtime and clean up.
4. Wait for the monitor to finish cleaning up and quit itself.
5. Move **Codex Activity Monitor.app** to Trash.

Cleanup removes owned runtime state, derived timeline data, integration records, preferences, diagnostic logs, and the login item. It does not remove Codex-owned conversations, source checkouts, or development virtual environments. Unknown files or active processes can prevent cleanup; follow the displayed error instead of deleting the configuration backup.

If the monitor exits before cleanup completes, reopen the same app to continue. If you already deleted it, restore or rebuild the app and use the restoration flow. Deleting the `.app` alone is not an uninstall: an external launcher can fall back to the original CLI, but configuration and data still need cleanup.

## Local data

### Codex chat history

Monitor reads Codex chat history from `~/.codex/` by default, where `~` is your user home directory. If the wrapped Codex backend process inherits a `CODEX_HOME` environment variable, Monitor uses that directory instead. It reads history files under `sessions/` and `archived_sessions/` without modifying them. The GUI has no option to select a different Codex data directory.

### Monitor settings and data

The default directory for Monitor's own data is:

```text
~/Library/Application Support/CodexActivityMonitor/
```

It contains preferences, integration backups and management state, a launcher, runtime files, derived timeline metadata, and a `diagnostics/` folder. A migrated terminal installation may retain its previous runtime path; do not move it while Codex processes still use it.

The source build also uses the ignored `.local/` directory for its virtual environment, build products, and tokenizer cache. The standalone app bundles its runtime dependencies. Neither installation changes ownership of Codex's own session history.

Local files can include titles, paths, command previews, and metrics. Backups can contain environment values. Review them before sharing; do not attach an entire data directory to a bug report.

### Runtime checks and diagnostic logs

Runtime Status combines current connection evidence with checks for bridge observation, parsing, token counting, history, timeline storage and queries, recovery checkpoints, and logging. A local storage problem can coexist with healthy live observation. Historical error counts alone do not keep a recovered component in an error state. Missing or stale evidence appears as unknown or unavailable. A previous-run warning only means the normal end marker was missing; it does not establish a crash or its cause.

Logs record lifecycle, configuration operations, failures, and recovery, with repeated events combined. They stay on this Mac; Monitor does not upload them. Structured records omit chat bodies, tool arguments/results, environment values, credentials, and observation secrets. They still contain local diagnostic metadata, so review selected records before sharing.

The four log streams (`gui`, `management`, `collector`, `bridge`) each keep a 2 MiB current file and up to three archives. Retention is up to seven days, within a 64 MiB overall design budget. The viewer initially loads 200 records and holds at most 1,000 records or 2 MiB of encoded records. Search is bounded and reports when only part of the retained range is available. Damaged or incomplete lines are skipped. Logs may be dropped under pressure or during an abrupt exit; logging failures have an independent status signal.

The runtime's `collector.log` and `collector.previous.log` are bounded compatibility files for raw process output. They are not automatically imported into the structured viewer and can contain unsanitized details. Ordinary disconnection retains structured diagnostics, while runtime compatibility files can be cleaned up.

## Troubleshooting

### The overview has no chats

Confirm that the monitor is configured and running. Finish active Codex work, fully quit Codex Desktop, reopen it, and start a new local task. Already running processes do not inherit later environment changes. Cloud or remote sessions outside the configured backend are not covered by the quick start.

### Speed displays `—` or `0`

`—` indicates unavailable information. Read the connection indicator or observation notice for the specific cause. `0` is a known zero in the most recent window; reasoning, waiting, and tool work can continue without incoming visible text. A historical gap can coexist with a valid current per-stream rate.

### A tool result or reasoning summary is missing

Only source content that Codex exposes and the monitor can read is available. Some reasoning activities have no visible summary. Tool results may not yet be persisted, may be source-truncated, or may be unavailable; the activity viewer distinguishes these states where evidence permits.

### Updates or moving the app have not taken effect

To move an already configured app, finish tasks and quit both Codex and Monitor. Move the app, open Monitor from its new location, then reopen Codex Desktop. For the same macOS user, app locations and the daily source-built GUI share settings and data under `~/Library/Application Support/CodexActivityMonitor/`; no data migration is needed. A source build still depends on its original checkout and virtual environment.

If an update or move has not taken effect, check **Settings → General → Codex** from the app's current location and use **Reconfigure Integration** if offered. Finish tasks and fully restart Codex Desktop when prompted so its new wrappers use the updated integration. **Runtime Status** and **Logs** provide additional evidence if reconnection fails.

### Restoration is waiting for Codex to exit

Finish work, then fully quit Codex Desktop. Leave the monitor running so it can complete cleanup. It does not force-quit your tasks. Reopening the monitor continues pending cleanup after an interruption.

### Codex is installed somewhere else

Monitor currently uses the Codex command-line component bundled inside `/Applications/ChatGPT.app`. The full path it expects is:

```text
/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex
```

The GUI has no option to choose another path. The command-line management layer has a `--real-cli` option, but that is a custom integration path, not a supported GUI quick-start recipe. Include the installation layout in a bug report or contribution proposal.

## Optional terminal view

With the daily GUI running, a source checkout can read the same collector:

```sh
bin/codex-activity-monitor watch --lang en
bin/codex-activity-monitor status
```

For a custom or migrated runtime, pass `--runtime /absolute/path/to/runtime` before the subcommand. Once the GUI manages integration, use the app for configuration and restoration; the legacy `setup` / `uninstall` commands refuse to overwrite GUI-managed state. The terminal view does not keep GUI-managed observation running after the GUI quits.
