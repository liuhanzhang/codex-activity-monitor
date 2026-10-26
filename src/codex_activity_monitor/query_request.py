"""Validated read requests shared by the CLI and socket adapters."""

from dataclasses import asdict, dataclass
from typing import Literal

from .agent_relations import thread_identity
from .content_version import MAX_TOKEN_BYTES, page_request


@dataclass(frozen=True, slots=True)
class TimelineRequest:
    action: Literal['timeline', 'detail']
    scope_id: str
    thread_id: str
    turn_id: str | None
    phase_id: str | None
    section_id: str | None
    offset: int  # Python Unicode code points, supplied by the previous page.
    page_token: str | None

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict):
            raise ValueError('invalid_query')
        action = value.get('action')
        if action not in ('timeline', 'detail'):
            raise ValueError('unsupported_query')
        scope, thread = value.get('scope_id'), value.get('thread_id')
        if (not isinstance(scope, str) or not 0 < len(scope) <= 4096 or '\0' in scope or not thread_identity(thread)):
            raise ValueError('invalid_identity')
        turn, phase, section = (value.get(key) for key in ('turn_id', 'phase_id', 'section_id'))
        if turn is not None and not thread_identity(turn):
            raise ValueError('invalid_turn')
        if phase is not None and not thread_identity(phase):
            raise ValueError('invalid_phase')
        if action == 'detail' and (not thread_identity(turn) or not thread_identity(phase)):
            raise ValueError('invalid_phase')
        if section is not None and not thread_identity(section):
            raise ValueError('invalid_section')
        offset = value.get('offset', 0)
        if isinstance(offset, str) and len(offset) <= 20 and offset.isdecimal():
            offset = int(offset)
        if type(offset) is not int or not 0 <= offset <= 2**63-1:
            raise ValueError('invalid_offset')
        token = value.get('page_token')
        if token is not None and (not isinstance(token, str) or not 1 <= len(token) <= MAX_TOKEN_BYTES):
            raise ValueError('invalid_page_token')
        if action == 'detail':
            # Validate before acquiring a provider lock or opening a socket.
            page_request(scope, thread, turn, phase, section, offset, token)
        return cls(action, scope, thread, turn, phase, section, offset, token)

    def wire(self):
        return {name: value for name, value in asdict(self).items() if value is not None}
