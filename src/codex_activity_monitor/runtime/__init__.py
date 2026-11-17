"""Collector event loop, local IPC transports, leases and runtime diagnostics."""

from codex_activity_monitor import bind_package_facade

bind_package_facade(__name__, 'codex_activity_monitor.runtime.runtime')
