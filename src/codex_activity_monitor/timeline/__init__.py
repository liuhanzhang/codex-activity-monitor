"""Timeline phase ledger, sidecar persistence and read-only query service."""

from codex_activity_monitor import bind_package_facade

bind_package_facade(__name__, 'codex_activity_monitor.timeline.timeline')
