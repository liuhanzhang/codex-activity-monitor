"""Immutable history requests and the scope of justified absence decisions."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HistorySelection:
    scope: str
    thread: str
    turns: frozenset[str]
    urgent: bool = False
    items: frozenset[tuple[str, str]] | None = None  # (turn ID, item ID); None requests a full view.
    source_epoch: int = 0
    source_hint: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'turns', frozenset(self.turns))
        if self.items is not None:
            object.__setattr__(self, 'items', frozenset(self.items))
        object.__setattr__(self, 'source_hint', tuple(self.source_hint))

    @property
    def key(self):
        return self.scope, self.thread

    @property
    def utf8_bytes(self):
        values = [self.scope, self.thread, *self.turns]
        if self.items is not None:
            values.extend(value for pair in self.items for value in pair)
        values.extend(value for pair in self.source_hint for value in pair)
        return sum(len(value.encode('utf-8')) for value in values)

    def merge(self, old):
        items = None if self.items is None or old.items is None else self.items | old.items
        source = self if self.source_epoch >= old.source_epoch else old
        return HistorySelection(self.scope, self.thread, self.turns | old.turns,
                                self.urgent or old.urgent, items, source.source_epoch, source.source_hint)


@dataclass(frozen=True, slots=True)
class HistoryCoverage:
    turns: frozenset[str]
    items: frozenset[tuple[str, str]] | None

    @classmethod
    def from_summary(cls, summary):
        selected = summary.get('phase_selection')
        return cls(frozenset(summary.get('requested_turns', [])),
                   None if selected is None else frozenset(tuple(pair) for pair in selected))

    def covers(self, turn, item):
        return turn in self.turns and (self.items is None or (turn, item) in self.items)
