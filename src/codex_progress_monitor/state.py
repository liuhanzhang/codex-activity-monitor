"""Mutable progress state owned by the collector event loop.

These domain objects are not the snapshot wire schema. Presentation and history
projection read them; only event intake, recovery and reconciliation mutate them.
Runtime *_at fields and display_since use observed monotonic seconds. Source
review_started_ms/history_progress_ms and history *_ms fields use wall-clock
milliseconds; they never supply a missing observation timestamp.
"""

from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class Activity:
    id: str
    turn_id: str
    kind: str
    label: str
    started_at: float | None
    status: str = 'inProgress'
    ended_at: float | None = None
    stream_keys: list = field(default_factory=list)
    summary_chars: int = 0
    summary_parts: set = field(default_factory=set)
    output_chars: int = 0
    exit_code: int | None = None
    turn_bound: bool = True
    tool_name: str | None = None
    tool_names: list = field(default_factory=list)
    command: str | None = None
    command_name: str | None = None
    app_context: dict = field(default_factory=dict)
    command_previews: list = field(default_factory=list)
    file_edit_preview: dict | None = None
    call_id: str | None = None
    tool_label_revision: int = -1
    item_type: str | None = None
    last_progress_at: float = 0
    files: list = field(default_factory=list)
    file_count: int = 0
    receiver_thread_ids: list = field(default_factory=list)
    agents_states: dict = field(default_factory=dict)
    server: str | None = None
    mirrored: bool = False
    target_item_id: str | None = None
    review_started_ms: int | None = None
    startup_pending: bool = False
    coordination_action: str | None = None
    history: dict | None = None
    history_retired: bool = False
    observation_lost: bool = False

    @property
    def effective_status(self):
        return self.history['status'] if self.history and self.history.get('history_terminal') else self.status

    @property
    def unresolved(self):
        return not self.history_retired and self.observation_lost and self.effective_status == 'unknown' and self.ended_at is None

    @property
    def active(self):
        return not self.history_retired and self.ended_at is None and self.effective_status not in ('completed','failed','declined','interrupted','unknown')


@dataclass
class Thread:
    backend: str
    id: str
    name: str = ''
    cwd: str = ''
    model: str = 'unknown'
    turn_id: str | None = None
    status: str = 'idle'
    wait_started_at: float = 0
    last_progress_at: float = 0
    activities: OrderedDict = field(default_factory=OrderedDict)
    quality: set = field(default_factory=set)
    ephemeral: bool | None = None
    thread_source: str | None = None
    active_flags: set = field(default_factory=set)
    visible: bool = True
    goal_status: str | None = None
    plan: list = field(default_factory=list)
    review_mode: bool = False
    project_id: str | None = None
    environment_connected: bool | None = None
    environment_id: str | None = None
    error_status: str | None = None
    subagents: dict = field(default_factory=dict)
    display_primary: str | None = None
    display_since: float = 0
    parent_thread_id: str | None = None
    parent_source: str | None = None
    parent_quality: set = field(default_factory=set)
    context_only: bool = False
    history_progress_ms: float | None = None
    history_quality: set = field(default_factory=set)
    history_turn: dict | None = None
