"""Interpret Antigravity steps for both live observation and transcript history."""

from dataclasses import dataclass, field
import json
import re
from urllib.parse import unquote, urlsplit

from codex_activity_monitor.agents.codex.history_index import timestamp
from codex_activity_monitor.timeline.timeline import item_phase
from codex_activity_monitor.core.usage import token_number


TYPE_PREFIX = 'CORTEX_STEP_TYPE_'
STATUS_PREFIX = 'CORTEX_STEP_STATUS_'
STEP_STATUSES = {'PENDING': 'waiting', 'WAITING': 'waiting', 'QUEUED': 'waiting', 'RUNNING': 'active',
                 'GENERATING': 'active', 'DONE': 'completed', 'ERROR': 'failed',
                 'FAILED': 'failed', 'CANCELED': 'interrupted', 'CANCELLED': 'interrupted',
                 'INTERRUPTED': 'interrupted', 'HALTED': 'interrupted',
                 'REJECTED': 'declined', 'SKIPPED': 'declined'}
OPEN = {'active', 'waiting'}
TOOLS = {'RUN_COMMAND': ('commandExecution', 'run_command'),
         'VIEW_FILE': ('dynamicToolCall', 'view_file'), 'LIST_DIRECTORY': ('dynamicToolCall', 'list_directory'),
         'FIND_BY_NAME': ('dynamicToolCall', 'find_by_name'), 'GREP_SEARCH': ('dynamicToolCall', 'grep_search'),
         'SEARCH_WEB': ('webSearch', 'search_web'), 'BROWSER_SUBAGENT': ('dynamicToolCall', 'browser_subagent'),
         'WRITE_TO_FILE': ('fileChange', 'write_to_file'), 'REPLACE_FILE_CONTENT': ('fileChange', 'replace_file_content'),
         'APPLY_PATCH': ('fileChange', 'apply_patch'), 'MCP_TOOL': ('mcpToolCall', ''),
         'INVOKE_SUBAGENT': ('collabAgentToolCall', 'invoke_subagent'),
         'CODE_ACTION': ('fileChange', 'code_action'), 'ASK_QUESTION': ('dynamicToolCall', 'ask_question')}


def object_value(value):
    return value if isinstance(value, dict) else {}


def string(value):
    return value if isinstance(value, str) else ''


def step_index(value):
    return type(value) is int and 0 <= value < 2**31


def turn_id(conversation, index):
    return f'{conversation}-turn-{index}'


def step_id(conversation, index):
    return f'{conversation}-step-{index}'


def workspace(summary):
    for row in summary.get('workspaces', ()):
        uri = string(object_value(row).get('workspaceFolderAbsoluteUri'))
        parts = urlsplit(uri)
        if parts.scheme == 'file' and not parts.netloc and parts.path.startswith('/'):
            return unquote(parts.path)
    return ''


def amount(value):
    if isinstance(value, str) and len(value) <= 19 and re.fullmatch(r'[0-9]+', value):
        value = int(value)
    return token_number(value)


def thinking_end(value, created, generation_ended):
    if created is None or generation_ended is None or not re.fullmatch(r'[0-9]{1,12}(?:\.[0-9]{1,9})?s', string(value)):
        return None
    end = created + float(value[:-1]) * 1000
    return end if created < end <= generation_ended else None


def usage_from(step):
    fields = object_value(object_value(step.get('metadata')).get('modelUsage'))
    result = {key: amount(fields.get(name)) for key, name in (
        ('output_tokens', 'outputTokens'), ('reasoning_tokens', 'thinkingOutputTokens'),
        ('visible_output_tokens', 'responseOutputTokens'), ('input_tokens', 'inputTokens'),
        ('cached_input_tokens', 'cacheReadTokens'),
    )}
    output, visible, thinking = (result[k] for k in ('output_tokens', 'visible_output_tokens', 'reasoning_tokens'))
    if output is None and visible is not None and thinking is not None:
        result['output_tokens'] = output = amount(visible + thinking)
    if output is not None and visible is not None and thinking is None and visible <= output:
        result['reasoning_tokens'] = output - visible
    if output is not None and thinking is not None and visible is None and thinking <= output:
        result['visible_output_tokens'] = output - thinking
    if output is not None and (any(n is not None and n > output for n in (visible, thinking)) or
                              visible is not None and thinking is not None and visible + thinking != output):
        result['visible_output_tokens'] = result['reasoning_tokens'] = None
    return {key: value for key, value in result.items() if value is not None}


def tool_details(row, kind):
    """Use only the detail fields supplied by the observed trajectory step."""
    if kind == 'VIEW_FILE':
        view = object_value(row.get('viewFile'))
        return ({k: view[k] for k in ('absolutePathUri', 'startLine', 'endLine') if k in view},
                {k: view[k] for k in ('numLines', 'numBytes', 'lineRangeBytes') if k in view})
    if kind == 'CODE_ACTION':
        action = object_value(row.get('codeAction'))
        return action.get('actionSpec'), {k: action[k] for k in ('actionResult', 'diffStats') if k in action}
    if kind == 'ASK_QUESTION':
        questions = object_value(row.get('askQuestion')).get('questions') or ()
        interactions = row.get('completedInteractions') or ()
        return ([{k: q[k] for k in ('question', 'options') if k in q} for q in questions if isinstance(q, dict)],
                [response for item in interactions
                 if (response := object_value(object_value(item).get('response')).get('askQuestion'))])
    if kind == 'INVOKE_SUBAGENT':
        invocation = object_value(row.get('invokeSubagent'))
        return invocation.get('subagents'), [
            {'conversationId': item['conversationId']} for item in invocation.get('results', ())
            if isinstance(item, dict) and isinstance(item.get('conversationId'), str)]
    return None, None


@dataclass(slots=True)
class Step:
    index: int
    state: str
    type: str
    created: float | None = None
    started: float | None = None
    ended: float | None = None
    model: str = ''
    text: str = ''
    thinking: str = ''
    tool_name: str = ''
    arguments: str = ''
    output: str = ''
    exit_code: int | None = None
    usage: dict = field(default_factory=dict)
    generation_ended: float | None = None
    children: tuple = ()
    model_generated: bool = False
    thinking_ended: float | None = None
    thinking_end_source: str = 'producer'

    @property
    def is_tool(self):
        return bool(self.type) and self.type not in ('SYSTEM_MESSAGE', 'CHECKPOINT', 'USER_INPUT', 'PLANNER_RESPONSE')

    def pair_next(self, nxt):
        """Use the following model tool call's creation time when thinking summary text was omitted."""
        if (self.type == 'PLANNER_RESPONSE' and self.thinking_ended is None and not self.text and
                (self.usage.get('reasoning_tokens') or 0) > 0 and nxt is not None and
                nxt.is_tool and nxt.model_generated and self.created is not None and
                nxt.created is not None and self.generation_ended is not None and
                self.created < nxt.created <= self.generation_ended):
            self.thinking_ended = nxt.created
            self.thinking_end_source = 'producer'
            return True
        return False

    def pair_text_receipt(self, main_phase):
        """Bound silent thinking before a text response using its observed first-chunk receipt."""
        if (self.type == 'PLANNER_RESPONSE' and self.thinking_ended is None and self.text and
                (self.usage.get('reasoning_tokens') or 0) > 0 and isinstance(main_phase, dict) and
                main_phase.get('start_source') == 'receipt' and self.created is not None and
                main_phase.get('start_ms') is not None and self.generation_ended is not None and
                self.created < main_phase['start_ms'] <= self.generation_ended):
            self.thinking_ended = main_phase['start_ms']
            self.thinking_end_source = 'receipt'
            return True
        return False

    def item(self, conversation, part='main'):
        name = step_id(conversation, self.index)
        if part == 'summary':
            return {'id': name+'-summary', 'type': 'reasoning', 'status': self.state}
        kind, tool = TOOLS.get(self.type, ('dynamicToolCall', ''))
        if part == 'input':
            return {'id': name+'-input', 'type': 'function_call', 'status': 'completed',
                    'tool': self.tool_name or tool, 'call_id': name, 'arguments': self.arguments}
        kind = {'USER_INPUT': 'userMessage', 'PLANNER_RESPONSE': 'agentMessage'}.get(self.type, kind)
        return {'id': name, 'type': kind, 'status': self.state, 'tool': self.tool_name or tool}

    def parts(self):
        if self.type in ('SYSTEM_MESSAGE', 'CHECKPOINT'):
            return
        if self.type == 'PLANNER_RESPONSE':
            if self.thinking or self.thinking_ended is not None or (self.state in OPEN and not self.text):
                yield 'summary'
            if self.text:
                yield 'main'
        else:
            yield 'main'
            if (self.is_tool and self.model_generated and self.created is not None and
                    self.generation_ended is not None and self.created <= self.generation_ended and
                    (self.started is None or self.generation_ended <= self.started)):
                yield 'input'

    def model_request(self):
        if self.type != 'PLANNER_RESPONSE' or self.state != 'completed' or 'output_tokens' not in self.usage:
            return None
        # Planner startedAt is the dispatch of the finished step.
        duration = (self.generation_ended - self.created
                    if self.created is not None and self.generation_ended is not None else 0)
        return {'output_tokens': self.usage['output_tokens'], 'duration_ms': duration if duration > 0 else None}

    def phase(self, scope, conversation, turn, part, *, at=None, history=False, final_response=False):
        item = self.item(conversation, part)
        start = self.started if history or self.type != 'PLANNER_RESPONSE' else None
        terminal = self.state not in OPEN and self.state != 'unknown'
        end = self.ended if terminal else None
        start_source = end_source = 'history' if history else 'producer'
        if self.type == 'USER_INPUT':
            start = end = self.created
        elif part == 'input':
            start, end = self.created, self.generation_ended
        elif part == 'summary' and not history:
            start = self.created if self.thinking_ended is not None or not self.text else None
            end = self.thinking_ended
            end_source = self.thinking_end_source
        elif final_response and not history:
            if self.thinking_ended is not None:
                start, end = self.thinking_ended, self.generation_ended
                start_source = self.thinking_end_source
            elif not (self.usage.get('reasoning_tokens') or 0):
                start, end = self.created, self.generation_ended
            else:
                start, end = None, self.generation_ended
        elif history and start is None and not self.is_tool:
            start = self.created
        if terminal and end is None and not history:
            end = at
            if at is not None:
                end_source = 'receipt'
        value = item_phase(scope, conversation, turn, item, start, end, 'history' if history else 'producer')
        value.update(source='antigravity', step_index=self.index, step_type=self.type)
        if value.get('start_ms') is not None:
            value['start_source'] = start_source
        if value.get('end_ms') is not None:
            value['end_source'] = end_source
        if part != 'input' and self.exit_code is not None:
            value['exit_code'] = self.exit_code
        if history:
            value['history_terminal'] = terminal
        return value


def live_step(index, value):
    row = object_value(value)
    row = object_value(row.get('step', row))
    kind = string(row.get('type')).removeprefix(TYPE_PREFIX)
    state = STEP_STATUSES.get(string(row.get('status')).removeprefix(STATUS_PREFIX), 'unknown')
    meta = object_value(row.get('metadata'))
    planner, command = (object_value(row.get(key)) for key in ('plannerResponse', 'runCommand'))
    user = object_value(row.get('userInput'))
    call = object_value(meta.get('toolCall'))
    fn = object_value(call.get('function'))
    name = string(call.get('name')) or string(fn.get('name'))
    arguments = string(command.get('commandLine')) or string(call.get('arguments')) or string(fn.get('arguments'))
    result = command.get('combinedOutput')
    tool_input, tool_output = tool_details(row, kind)
    if kind == 'GENERIC':
        tool_input = object_value(object_value(row.get('generic')).get('args'))
        if name == 'run_command':
            arguments = arguments or string(tool_input.get('CommandLine'))
    if not arguments and tool_input:
        arguments = json.dumps(tool_input, ensure_ascii=False, indent=2)
    output = string(result) or string(object_value(result).get('full'))
    if not output and tool_output:
        output = json.dumps(tool_output, ensure_ascii=False, indent=2)
    user_text = string(user.get('userResponse')) or '\n'.join(
        string(object_value(item).get('text')) for item in user.get('items', ()) if isinstance(item, dict))
    exit_code = command.get('exitCode')
    if state == 'completed' and type(exit_code) is int and exit_code != 0:
        state = 'failed'
    children = object_value(row.get('invokeSubagent')).get('results', ())
    created = timestamp(meta.get('createdAt'))
    generation_ended = timestamp(meta.get('finishedGeneratingAt'))
    return Step(index, state, kind, created, timestamp(meta.get('startedAt')),
                timestamp(meta.get('completedAt')), string(object_value(meta.get('modelUsage')).get('model'))
                or string(meta.get('generatorModel')),
                string(planner.get('response')) or user_text,
                string(planner.get('thinking')), name[:128], arguments, output,
                exit_code if type(exit_code) is int else None, usage_from(row),
                generation_ended,
                tuple(r.get('conversationId') for r in children if isinstance(r, dict)),
                model_generated=meta.get('source') == 'CORTEX_STEP_SOURCE_MODEL',
                thinking_ended=thinking_end(planner.get('thinkingDuration'), created, generation_ended))


def history_step(row):
    step = Step(row['step_index'], STEP_STATUSES.get(string(row.get('status')), 'unknown'),
                string(row.get('type')), created=timestamp(row.get('created_at')),
                started=timestamp(row.get('started_at')), ended=timestamp(row.get('completed_at')),
                text=string(row.get('content')), thinking=string(row.get('thinking')))
    if step.type == 'PLANNER_RESPONSE':
        for key, name in (('output_tokens', 'output_tokens'), ('input_tokens', 'input_tokens'),
                          ('reasoning_tokens', 'thinking_output_tokens'), ('cached_input_tokens', 'cache_read_tokens')):
            if (value := amount(row.get(name))) is not None:
                step.usage[key] = value
    return step
