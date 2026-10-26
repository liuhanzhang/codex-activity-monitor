"""Render selected Codex source values verbatim, with explicit absence/truncation."""

import json
import re

from .events import TOOL_TYPES
from .i18n import message

CANONICAL_TYPES = {'UserMessage': 'userMessage', 'AgentMessage': 'agentMessage', 'Reasoning': 'reasoning',
                   'Plan': 'plan', 'CommandExecution': 'commandExecution', 'McpToolCall': 'mcpToolCall',
                   'DynamicToolCall': 'dynamicToolCall', 'FileChange': 'fileChange',
                   'CollabAgentToolCall': 'collabAgentToolCall', 'ImageView': 'imageView',
                   'ImageGeneration': 'imageGeneration', 'ContextCompaction': 'contextCompaction',
                   'SubAgentActivity': 'subAgentActivity', 'WebSearch': 'webSearch'}

ABSENT = object()


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


def section(key, title, value, format=None, title_key=None, text_key=None):
    if format is None:
        format = 'json' if isinstance(value, (dict, list)) else 'text'
    return {'id': key, 'title': title, 'title_message': message(title_key) if title_key else None,
            'text_message': message(text_key) if text_key else None, 'format': format,
            'text': json.dumps(value, ensure_ascii=False, indent=2) if format == 'json' else text_content(value)}


def raw_section(key, title, value=ABSENT):
    """Keep strings verbatim, containers/scalars as JSON, and absence explicit."""
    if value is ABSENT:
        return {'id': key, 'title': title, 'format': 'text', 'text': '',
                'title_message': message('detail.' + key),
                'notice_message': message('detail.missing_input' if key == 'input' else 'detail.missing_output'),
                'notice': '源记录未提供独立参数。' if key == 'input' else '来源中尚无这次调用的结果。'}
    return {'id': key, 'title': title, 'title_message': message('detail.' + key), 'format': 'code' if isinstance(value, str) else 'json',
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
        sections.append(section('body', '用户输入' if kind == 'userMessage' else '正文', body, 'text', title_key='detail.user' if kind == 'userMessage' else 'detail.body'))
        if attachments and not text_content(body):
            sections.append(section('attachments', '图片附件', '此条输入包含图片，预览见下方。', 'text', title_key='detail.attachments', text_key='detail.image_notice'))
    elif kind == 'reasoning':
        value = item.get('summary_text', item.get('summary', []))
        sections.append(section('body', '可见思考摘要', value, 'text', title_key='detail.reasoning'))
    elif kind in TOOL_TYPES or kind in ('function_call', 'custom_tool_call'):
        return tool_content(item, kind)
    elif kind == 'contextCompaction':
        return [], []
    else:
        sections.append(section('body', '活动内容', item, 'json', title_key='detail.content'))
    return [s for s in sections if s['text']], attachments
