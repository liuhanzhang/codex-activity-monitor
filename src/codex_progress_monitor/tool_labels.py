"""Bounded display metadata; generated tool code is never evaluated or retained here."""

import re
import shlex
from collections import deque
from pathlib import PurePath


MAX_LABEL = 160
MAX_CODE_PREFIX = 64 * 1024
MAX_TOOL_NAMES = 6

_IDENTIFIER = re.compile(r'^[A-Za-z_$][\w$]{0,127}$')


def _literal_end(code, start):
    quote = code[start]
    index = start + 1
    while index < len(code):
        if code[index] == '\\':
            index += 2
        elif quote == '`' and code.startswith('${', index):
            # Nested interpolation needs a full parser. Leave the remainder
            # opaque rather than interpreting template text as tool calls.
            return len(code)
        elif code[index] == quote:
            return index + 1
        else:
            index += 1
    return len(code)


def _slash_end(code, start):
    # Ambiguous division expressions are opaque too. This may hide a reference,
    # but keeps partial regex literals from becoming invented tool names.
    index, in_class = start + 1, False
    while index < len(code):
        char = code[index]
        if char == '\\':
            index += 2
            continue
        if char == '\n':
            return index + 1
        if char == '[':
            in_class = True
        elif char == ']':
            in_class = False
        elif char == '/' and not in_class:
            index += 1
            while index < len(code) and code[index].isalpha():
                index += 1
            return index
        index += 1
    return len(code)


def _code_tokens(code):
    index = 0
    while index < len(code):
        char = code[index]
        if char.isspace():
            index += 1
            continue
        if code.startswith('//', index):
            end = code.find('\n', index + 2)
            index = end + 1 if end != -1 else len(code)
            continue
        if code.startswith('/*', index):
            end = code.find('*/', index + 2)
            index = end + 2 if end != -1 else len(code)
            continue
        start = index
        if char in ('"', "'", '`'):
            index = _literal_end(code, index)
            token = ''
        elif char == '/':
            index = _slash_end(code, index)
            token = ''
        elif char.isalpha() or char in '_$':
            end = index + 1
            while end < len(code) and (code[end].isalnum() or code[end] in '_$'):
                end += 1
            token, index = code[index:end], end
        else:
            token, index = char, index + 1
        yield token, start, min(index, len(code))


def code_tool_names(code):
    """Recognize direct tools.NAME( references, including incomplete arguments."""
    tokens = deque(maxlen=5)
    names = []
    for token, _, _ in _code_tokens(code[:MAX_CODE_PREFIX]):
        tokens.append(token)
        if (len(tokens) >= 4 and tokens[-4] == 'tools' and tokens[-3] == '.'
                and _IDENTIFIER.fullmatch(tokens[-2]) and tokens[-1] == '('
                and (len(tokens) == 4 or tokens[-5] != '.')):
            name = tokens[-2]
            if name not in names:
                names.append(name)
                if len(names) == MAX_TOOL_NAMES:
                    break
    return names


def _literal_prefix(code, start, end):
    """Decode a literal prefix without evaluating JavaScript or expressions."""
    quote, index, result = code[start], start + 1, []
    escapes = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f',
               'v': '\v', '0': '\0', '\\': '\\', '"': '"', "'": "'", '`': '`', '/': '/'}
    while index < end:
        char = code[index]
        if char == quote:
            return ''.join(result), True
        if quote == '`' and code.startswith('${', index):
            break
        if char != '\\':
            result.append(char)
            index += 1
            continue
        index += 1
        if index >= end:
            break
        escaped = code[index]
        if escaped in ('x', 'u'):
            width = 2 if escaped == 'x' else 4
            begin, finish = index + 1, index + 1 + width
            if escaped == 'u' and code.startswith('{', begin):
                close = code.find('}', begin + 1, end)
                if close == -1:
                    break
                begin, finish = begin + 1, close
                next_index = close + 1
            else:
                next_index = finish
            if finish > end:
                break
            digits = code[begin:finish]
            if not re.fullmatch(r'[0-9a-fA-F]{1,6}', digits):
                return None
            value = int(digits, 16)
            if 0xD800 <= value <= 0xDBFF:
                if next_index + 6 > end:
                    break
                low = code[next_index + 2:next_index + 6]
                if not code.startswith('\\u', next_index) or not re.fullmatch(r'[0-9a-fA-F]{4}', low):
                    return None
                low = int(low, 16)
                if not 0xDC00 <= low <= 0xDFFF:
                    return None
                value = 0x10000 + ((value - 0xD800) << 10) + low - 0xDC00
                next_index += 6
            if value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
                return None
            result.append(chr(value))
            index = next_index
        elif escaped in ('\n', '\r'):
            index += 2 if escaped == '\r' and code.startswith('\r\n', index) else 1
        elif escaped in escapes:
            if escaped == '0' and index + 1 < end and code[index + 1].isdigit():
                return None
            result.append(escapes[escaped])
            index += 1
        else:
            return None
    return ''.join(result), False


def is_command_tool(name):
    return bool(name) and name.rsplit('.', 1)[-1] in ('exec_command', 'shell_command')


def _object_command(code, tokens, start, name):
    key = 'cmd' if name.rsplit('.', 1)[-1] == 'exec_command' else 'command'
    depth, expect_key, preview = 1, True, None
    for index in range(start + 1, len(tokens)):
        value, begin, end = tokens[index]
        if depth == 1 and expect_key:
            field = value
            if not field and code[begin] in ('"', "'", '`'):
                decoded = _literal_prefix(code, begin, end)
                field = decoded[0] if decoded and decoded[1] else None
            if field == key and index + 2 < len(tokens) and tokens[index + 1][0] == ':':
                literal, literal_start, literal_end = tokens[index + 2]
                preview = None
                if not literal and code[literal_start] in ('"', "'", '`'):
                    decoded = _literal_prefix(code, literal_start, literal_end)
                    if decoded and decoded[0]:
                        command, closed = decoded
                        following = tokens[index + 3][0] if index + 3 < len(tokens) else None
                        complete = closed and following in (',', '}')
                        summary = command_summary(command)
                        if not complete and not summary.endswith('…'):
                            summary = summary[:MAX_LABEL - 1] + '…'
                        preview = {'tool_name': name, 'command': summary, 'complete': complete}
            # A spread can overwrite cmd, so its final value is unobservable.
            if value == '.' and [t[0] for t in tokens[index:index + 3]] == ['.', '.', '.']:
                preview = None
            expect_key = False
        if value in ('{', '[', '('):
            depth += 1
        elif value in ('}', ']', ')'):
            depth -= 1
            if depth == 0:
                break
        elif value == ',' and depth == 1:
            expect_key = True
    return preview


def command_previews(name, code):
    """Only command literals in command-tool argument objects become metadata."""
    code = code[:MAX_CODE_PREFIX]
    tokens = list(_code_tokens(code))
    if is_command_tool(name):
        if tokens and tokens[0][0] == '{':
            preview = _object_command(code, tokens, 0, name)
            return [preview] if preview else []
        return []
    if not is_code_exec(name):
        return []
    previews = []
    for index in range(len(tokens) - 4):
        if (tokens[index][0] == 'tools' and tokens[index + 1][0] == '.'
                and is_command_tool(tokens[index + 2][0]) and tokens[index + 3][0] == '('
                and tokens[index + 4][0] == '{'
                and (index == 0 or tokens[index - 1][0] != '.')):
            preview = _object_command(code, tokens, index + 4, tokens[index + 2][0])
            if preview:
                previews.append(preview)
                if len(previews) == MAX_TOOL_NAMES:
                    break
    return previews


def tool_name(item):
    name = item.get('name') or item.get('tool')
    if not isinstance(name, str) or not name:
        return None
    namespace = item.get('namespace')
    if isinstance(namespace, dict):
        namespace = namespace.get('name')
    if isinstance(namespace, str) and namespace and not name.startswith(namespace + '.'):
        name = namespace + '.' + name
    elif item.get('server'):
        name = str(item['server']) + '/' + name
    return name[:MAX_LABEL]


def is_code_exec(name):
    return name in ('exec', 'functions.exec')


def tool_label(name, names=()):
    if is_code_exec(name):
        return (name + ' → ' + ', '.join(names)) if names else name + '（Code Mode）'
    return name


def command_summary(command):
    if isinstance(command, list):
        command = shlex.join(str(part) for part in command)
    command = str(command)
    incomplete = False
    if len(command) <= MAX_CODE_PREFIX:
        lexer = shlex.shlex(command, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ''
        parts = []
        try:
            for part in lexer:
                parts.append(part)
        except ValueError:
            # Older running bridges truncate before removing the shell wrapper.
            # shlex retains the decoded prefix of the unfinished argument.
            if lexer.token:
                parts.append(lexer.token)
            incomplete = True
        shells = {'bash', 'zsh', 'sh', 'dash', 'ksh', 'fish'}
        if parts and PurePath(parts[0]).name in shells:
            for index, part in enumerate(parts[1:], 1):
                if re.fullmatch(r'-[ilxvefu]*c[ilxvefu]*', part) and index + 1 < len(parts):
                    command = parts[index + 1]
                    if incomplete:
                        command += '…'
                    break
                if not part.startswith('-'):
                    break
    first = command.split('\n', 1)[0]
    return first if len(first) <= MAX_LABEL else first[:MAX_LABEL - 1] + '…'
