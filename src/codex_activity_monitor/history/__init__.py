"""Weighted history cache facade, resource budgets and background refresh."""

from codex_activity_monitor import bind_package_facade

bind_package_facade(__name__, 'codex_activity_monitor.history.history')
