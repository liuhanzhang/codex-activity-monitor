"""Read Codex-owned JSONL lazily. Cache offsets and metadata, never transcripts."""

from collections import OrderedDict
from datetime import datetime
import hashlib
import json
import re
from pathlib import Path
import threading
import time

from .timeline import identity, item_phase, merge_phase, status, valid_ms
from .events import TOOL_TYPES

CANONICAL_TYPES = {'UserMessage': 'userMessage', 'AgentMessage': 'agentMessage', 'Reasoning': 'reasoning',
                   'Plan': 'plan', 'CommandExecution': 'commandExecution', 'McpToolCall': 'mcpToolCall',
                   'DynamicToolCall': 'dynamicToolCall', 'FileChange': 'fileChange',
                   'CollabAgentToolCall': 'collabAgentToolCall', 'ImageView': 'imageView',
                   'ImageGeneration': 'imageGeneration', 'ContextCompaction': 'contextCompaction',
                   'SubAgentActivity': 'subAgentActivity', 'WebSearch': 'webSearch'}

ABSENT = object()


def timestamp(value):
    if isinstance(value, (int, float)):
        return valid_ms(value * 1000 if value < 100000000000 else value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000
        except ValueError:
            pass
    return None


def canonical_item(item):
    result = dict(item)
    result['type'] = CANONICAL_TYPES.get(item.get('type'), item.get('type'))
    if item.get('type') == 'Extension':
        result['type'] = {'web.search': 'webSearch', 'image_gen.generation': 'imageGeneration'}.get(item.get('kind'), 'extension')
    for snake, camel in (('process_id', 'processId'), ('receiver_thread_ids', 'receiverThreadIds'),
                         ('sender_thread_id', 'senderThreadId'), ('agents_states', 'agentsStates'),
                         ('agent_thread_id', 'agentThreadId')):
        if snake in result:
            result[camel] = result[snake]
    return result


class RolloutIndex:
    def __init__(self, path, scope, thread):
        self.path, self.scope, self.thread = path, scope, thread
        self.offset = 0
        self.fingerprint = None
        self.anchor = None
        self.turn = None
        self.turns = OrderedDict()
        self.phases = OrderedDict()
        self.refs = {}
        self.outputs = {}
        self.relations = {}
        self.mode = None
        self.quality = set()
        self.revision = 0

    def refresh(self):
        stat = self.path.stat()
        signature = (stat.st_dev, stat.st_ino)
        with self.path.open('rb') as stream:
            first = stream.readline()
            try:
                meta = json.loads(first)
            except ValueError:
                raise ValueError('history_header_invalid') from None
            if meta.get('type') != 'session_meta' or meta.get('payload', {}).get('id') != self.thread:
                raise ValueError('history_identity_mismatch')
            self.mode = meta['payload'].get('history_mode', 'legacy')
            stream.seek(max(0, self.offset - 256))
            anchor = hashlib.sha256(stream.read(min(self.offset, 256))).digest()
            if self.fingerprint != signature or stat.st_size < self.offset or (self.anchor is not None and self.anchor != anchor):
                self.offset = 0
                self.turn = None
                self.turns.clear(); self.phases.clear(); self.refs.clear(); self.outputs.clear(); self.relations.clear()
                self.quality.clear()
            self.fingerprint = signature
            stream.seek(self.offset)
            while True:
                start = stream.tell()
                line = stream.readline()
                if not line or not line.endswith(b'\n'):
                    break
                self.offset = stream.tell()
                try:
                    row = json.loads(line)
                    self._row(row, start, len(line))
                except (ValueError, TypeError, KeyError, AttributeError):
                    self.quality.add('malformed_history_record')
                self.revision += 1
            stream.seek(max(0, self.offset - 256))
            self.anchor = hashlib.sha256(stream.read(min(self.offset, 256))).digest()

    def _turn_record(self, turn, at, start=None, end=None, state=None, parent=None):
        if not turn:
            return
        old = self.turns.get(turn, {})
        self.turns[turn] = {**old, 'id': identity('turn', self.scope, self.thread, turn),
                            'scope_id': self.scope, 'thread_id': self.thread, 'turn_id': turn,
                            'start_ms': start or old.get('start_ms'), 'end_ms': end or old.get('end_ms'),
                            'updated_ms': at or old.get('updated_ms'),
                            'parent_turn_id': parent or old.get('parent_turn_id'),
                            'status': state or old.get('status', 'unknown')}

    def _row(self, row, offset, length):
        outer, payload = row.get('type'), row.get('payload', {})
        at = timestamp(row.get('timestamp'))
        kind = payload.get('type')
        if outer == 'turn_context':
            self.turn = payload.get('turn_id')
            self._turn_record(self.turn, at, parent=payload.get('parent_turn_id'))
        elif outer == 'event_msg' and kind in ('task_started', 'turn_started'):
            self.turn = payload.get('turn_id') or payload.get('turn_attribution', {}).get('turn_id')
            parent = (payload.get('turn_attribution') or {}).get('parent_turn_id')
            self._turn_record(self.turn, at, start=timestamp(payload.get('started_at')), state='active', parent=parent)
        elif outer == 'event_msg' and kind in ('task_complete', 'turn_complete', 'turn_aborted'):
            turn = payload.get('turn_id') or self.turn
            self._turn_record(turn, at, end=timestamp(payload.get('completed_at')) or at,
                              state='interrupted' if kind == 'turn_aborted' else 'completed')
        elif outer == 'event_msg' and kind == 'item_completed':
            turn = payload.get('turn_id')
            if payload.get('thread_id') not in (None, self.thread):
                self.quality.add('history_identity_mismatch')
                return
            item = canonical_item(payload.get('item', {}))
            if not turn:
                self.quality.add('turn_identity_unavailable')
            if item.get('type') == 'subAgentActivity':
                child = item.get('agentThreadId')
                if child:
                    self._relation(turn, child, item.get('id'), item.get('kind'))
                return
            phase = item_phase(self.scope, self.thread, turn, item,
                               valid_ms(payload.get('started_at_ms')), valid_ms(payload.get('completed_at_ms')), 'history')
            if phase:
                phase['status'] = status(item.get('status', 'completed'))
                phase['observed_until_ms'] = at
                self._phase(phase, offset, length, 'canonical')
                self._turn_record(turn, at)
            for child in item.get('receiverThreadIds', []):
                self._relation(turn, child, item.get('id'), item.get('tool'))
        elif outer == 'response_item':
            if kind in ('function_call_output', 'custom_tool_call_output'):
                if payload.get('call_id'):
                    self.outputs[payload['call_id']] = {'offset': offset, 'length': length}
                return
            if kind not in ('message', 'agentMessage', 'reasoning', 'function_call', 'custom_tool_call'):
                return
            if kind == 'message' and payload.get('role') not in ('assistant', 'user'):
                return
            # Paginated canonical records provide user/model items; raw calls supply missing parameters.
            if self.mode == 'paginated' and kind not in ('function_call', 'custom_tool_call'):
                return
            item = dict(payload)
            if kind == 'message' and payload.get('role') == 'user':
                item['type'] = 'userMessage'
            if not item.get('id'):
                item['id'] = payload.get('call_id') or 'offset-' + str(offset)
            if not self.turn:
                self.quality.add('turn_identity_unavailable')
            phase = item_phase(self.scope, self.thread, self.turn, item, source='history')
            if phase:
                phase.update(status='completed', observed_until_ms=at)
                self._phase(phase, offset, length, 'raw')

    def _phase(self, phase, offset, length, source):
        self.phases[phase['id']] = merge_phase(self.phases.get(phase['id']), phase)
        self.refs[phase['id']] = {'offset': offset, 'length': length, 'source': source,
                                  'fingerprint': self.fingerprint}

    def _relation(self, turn, child, call, operation):
        if turn and child and child != self.thread:
            key = identity('relation', self.scope, self.thread, turn, child, call)
            self.relations[key] = {'id': key, 'scope_id': self.scope, 'parent_thread_id': self.thread,
                                   'parent_turn_id': turn, 'child_thread_id': child,
                                   'call_id': call, 'operation': operation}

    def read_ref(self, ref):
        stat = self.path.stat()
        if (stat.st_dev, stat.st_ino) != self.fingerprint:
            raise ValueError('history_changed')
        with self.path.open('rb') as stream:
            stream.seek(ref['offset'])
            row = json.loads(stream.read(ref['length']))
        return row.get('payload', {})

    def detail(self, phase):
        ref = self.refs.get(phase['id'])
        if not ref:
            return None
        payload = self.read_ref(ref)
        item = payload.get('item', {}) if ref['source'] == 'canonical' else payload
        if ref['source'] == 'canonical' and (payload.get('turn_id') != phase['turn_id'] or item.get('id') != phase['item_id']):
            raise ValueError('history_changed')
        if ref['source'] == 'raw' and (item.get('id') or item.get('call_id') or 'offset-' + str(ref['offset'])) != phase['item_id']:
            raise ValueError('history_changed')
        sections, attachments = item_content(item)
        output = ABSENT
        call = item.get('call_id')
        if item.get('type') in ('function_call', 'custom_tool_call') and call in self.outputs:
            returned = self.read_ref(self.outputs[call])
            if returned.get('call_id') != call:
                raise ValueError('history_changed')
            output = returned.get('output', ABSENT)
            sections = [s for s in sections if s['id'] != 'output'] + [raw_section('output', '结果', output)]
        state = 'no_content' if phase.get('item_type') == 'contextCompaction' else (
            'source_truncated' if source_truncated(item, output) else 'available')
        return {'state': state, 'sections': sections,
                'attachments': attachments, 'source': 'codex_history'}


def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(text_content(v) for v in value if v is not None)
    if isinstance(value, dict):
        if isinstance(value.get('text'), str):
            return value['text']
        return json.dumps(value, ensure_ascii=False, indent=2)
    return '' if value is None else str(value)


def section(key, title, value, format=None):
    if format is None:
        format = 'json' if isinstance(value, (dict, list)) else 'text'
    return {'id': key, 'title': title, 'format': format,
            'text': json.dumps(value, ensure_ascii=False, indent=2) if format == 'json' else text_content(value)}


def raw_section(key, title, value=ABSENT):
    """Keep strings verbatim, containers/scalars as JSON, and absence explicit."""
    if value is ABSENT:
        return {'id': key, 'title': title, 'format': 'text', 'text': '',
                'notice': '源记录未提供独立参数。' if key == 'input' else '来源中尚无这次调用的结果。'}
    return {'id': key, 'title': title, 'format': 'code' if isinstance(value, str) else 'json',
            'text': value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)}


def fields(item, names):
    value = {key: item[key] for key in names if key in item}
    return value if value else ABSENT


def result_texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for part in value:
            if isinstance(part, dict) and isinstance(part.get('text'), str):
                yield part['text']
    elif isinstance(value, dict):
        yield from result_texts(value.get('content', value.get('content_items', value.get('contentItems'))))


def source_truncated(item, output=ABSENT):
    """Recognise source result conventions, never inspect generated parameters."""
    kind = CANONICAL_TYPES.get(item.get('type'), item.get('type'))
    if kind == 'commandExecution':
        text = item.get('aggregated_output', item.get('aggregatedOutput'))
        if isinstance(text, str) and '\n... command output truncated for persistence ...\n' in text:
            return True
        values = [text]
    elif kind in ('mcpToolCall', 'dynamicToolCall'):
        values = [item[key] for key in ('result', 'content_items', 'contentItems') if key in item]
    elif kind in ('function_call', 'custom_tool_call'):
        values = [output]
    else:
        return False
    omission = re.compile(r'…\d+ (?:tokens|chars) truncated…')
    for value in values:
        for text in result_texts(value):
            # Code Mode may wrap a tool-owned warning with its execution header.
            body = re.sub(r'\AScript [^\n]*\nWall time[^\n]*\nOutput:\n', '', text, count=1)
            if body.startswith('Warning: truncated output (original token count:') and omission.search(body):
                return True
            # Persisted MCP truncation replaces the result with a cut JSON preview.
            if kind == 'mcpToolCall' and body.startswith('{') and omission.search(body):
                try:
                    json.loads(body)
                except ValueError:
                    return True
    return False


def tool_content(item, kind):
    if kind in ('function_call', 'custom_tool_call'):
        args = item.get('arguments' if kind == 'function_call' else 'input', ABSENT)
        result = ABSENT  # Matched raw output is attached by RolloutIndex.detail.
    elif kind == 'commandExecution':
        args = fields(item, ('command', 'cwd', 'interaction_input', 'interactionInput'))
        result = fields(item, ('aggregated_output', 'aggregatedOutput', 'exit_code', 'exitCode',
                               'duration', 'duration_ms', 'durationMs', 'status'))
    elif kind == 'mcpToolCall':
        args = item.get('arguments', ABSENT)
        result = fields(item, ('result', 'error'))
    elif kind == 'dynamicToolCall':
        args = item.get('arguments', ABSENT)
        result = fields(item, ('content_items', 'contentItems', 'success', 'error', 'duration', 'durationMs'))
    elif kind == 'fileChange':
        args = ABSENT
        result = fields(item, ('changes', 'status', 'stdout', 'stderr', 'auto_approved', 'autoApproved'))
    else:
        args = item.get('arguments', item.get('input', ABSENT))
        result = item
    return [raw_section('input', '参数', args), raw_section('output', '结果', result)], []


def item_content(item):
    kind = CANONICAL_TYPES.get(item.get('type'), item.get('type'))
    if kind == 'Extension':
        kind = {'web.search': 'webSearch', 'image_gen.generation': 'imageGeneration'}.get(item.get('kind'), kind)
    sections, attachments = [], []
    if kind in ('userMessage', 'agentMessage', 'message', 'plan'):
        value = item.get('text', item.get('content', ''))
        body = [] if isinstance(value, list) else value
        for part in value if isinstance(value, list) else []:
            if isinstance(part, dict) and str(part.get('type', '')).replace('_', '').lower() in ('image', 'inputimage', 'localimage'):
                path = part.get('path') or part.get('image_url') or part.get('url')
                if isinstance(path, dict):
                    path = path.get('url')
                if isinstance(path, str):
                    attachments.append(path)
            else:
                body.append(part)
        sections.append(section('body', '用户输入' if kind == 'userMessage' else '正文', body, 'text'))
        if attachments and not text_content(body):
            sections.append(section('attachments', '图片附件', '此条输入包含图片，预览见下方。', 'text'))
    elif kind == 'reasoning':
        value = item.get('summary_text', item.get('summary', []))
        sections.append(section('body', '可见思考摘要', value, 'text'))
    elif kind in TOOL_TYPES or kind in ('function_call', 'custom_tool_call'):
        return tool_content(item, kind)
    elif kind == 'contextCompaction':
        return [], []
    else:
        sections.append(section('body', '活动内容', item, 'json'))
    return [s for s in sections if s['text']], attachments


class HistoryProvider:
    def __init__(self):
        self.indices = OrderedDict()
        self.lock = threading.RLock()
        self.missing = {}

    def index(self, scope, thread):
        if not isinstance(thread, str) or not thread or any(c in thread for c in ('/', '\\', '\x00')):
            raise ValueError('invalid_thread_identity')
        home = Path(scope).expanduser()
        if not home.is_absolute():
            return None
        key = (scope, thread)
        with self.lock:
            index = self.indices.get(key)
            if index is None or not index.path.is_file():
                if time.monotonic() - self.missing.get(key, 0) < 2:
                    return None
                matches = []
                for directory in ('sessions', 'archived_sessions'):
                    base = home / directory
                    if base.is_dir():
                        matches.extend(base.rglob('*' + thread + '.jsonl'))
                verified = []
                for path in matches:
                    try:
                        with path.open('rb') as stream:
                            meta = json.loads(stream.readline())
                        if meta.get('type') == 'session_meta' and meta.get('payload', {}).get('id') == thread:
                            verified.append(path)
                    except (OSError, ValueError):
                        continue
                if len(verified) != 1:
                    self.missing[key] = time.monotonic()
                    return None
                index = RolloutIndex(verified[0], scope, thread)
                self.indices[key] = index
            index.refresh()
            self.indices.move_to_end(key)
            while len(self.indices) > 12:
                self.indices.popitem(last=False)
            return index
