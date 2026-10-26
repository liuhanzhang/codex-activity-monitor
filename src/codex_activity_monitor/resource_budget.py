"""Measured cache weights for derived metadata, separate from process RSS."""

from dataclasses import fields, is_dataclass
import sys


def retained_bytes(*roots):
    """Count each reachable Python object once, including immutable references.

    This is CPython heap ownership, not allocator/kernel/RSS accounting. Call
    only when a cached index revision changes, never on every event or tick.
    """
    seen = set()
    pending = list(roots)
    total = 0
    while pending:
        value = pending.pop()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        total += sys.getsizeof(value)
        if isinstance(value, dict):
            pending.extend(value.keys()); pending.extend(value.values())
        elif isinstance(value, (tuple, list, set, frozenset)):
            pending.extend(value)
        elif is_dataclass(value) and not isinstance(value, type):
            pending.extend(getattr(value, f.name) for f in fields(value))
    return total
