"""Bounded display metadata; generated tool code is never evaluated or retained here."""

import re
import shlex
import unicodedata
from collections import deque
from pathlib import PurePath

from .i18n import tr


MAX_LABEL = 160
MAX_CODE_PREFIX = 64 * 1024
MAX_TOOL_NAMES = 6
MAX_PATCH_FILES = 64

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


def is_patch_tool(name):
    return name in ('apply_patch', 'functions.apply_patch')


def _patch_files(patch):
    """Read complete patch headers only; retain names, never patch contents."""
    preview = {'files': [], 'file_count': 0, 'current_file': ''}
    if len(patch) > MAX_CODE_PREFIX:
        return preview
    lines = patch.splitlines(keepends=True)
    if not lines or lines[0].rstrip('\r\n') != '*** Begin Patch' or not lines[0].endswith('\n'):
        return preview
    indices, current = {}, None

    def filename(raw):
        if any(unicodedata.category(char).startswith('C') for char in raw):
            return ''
        base = PurePath(raw).name
        return clean_name(base) if base not in ('', '.', '..') and len(base) <= MAX_LABEL else ''

    for line in lines[1:]:
        if line.rstrip('\r\n') == '*** End Patch':
            break
        if not line.endswith('\n'):
            if line.startswith('*** '):
                preview['current_file'] = ''
            break
        line = line.rstrip('\r\n')
        header = re.fullmatch(r'\*\*\* (?:Add|Update|Delete) File: (.+)', line)
        if header:
            raw = header[1]
            name = filename(raw)
            if not name:
                current, preview['current_file'] = None, ''
                continue
            if raw not in indices:
                if len(indices) == MAX_PATCH_FILES:
                    return {'files': [], 'file_count': 0, 'current_file': ''}
                indices[raw] = len(preview['files'])
                preview['files'].append({'path': name})
            current = indices[raw]
            preview['current_file'] = name
        elif line.startswith('*** Move to: ') and current is not None:
            name = filename(line[len('*** Move to: '):])
            preview['current_file'] = name
            if name:
                preview['files'][current]['path'] = name
    preview['file_count'] = len(preview['files'])
    return preview


def file_edit_preview(name, code):
    """Recognize direct patches or a literal argument to the latest Code Mode call."""
    empty = {'files': [], 'file_count': 0, 'current_file': ''}
    if is_patch_tool(name):
        return _patch_files(code)
    if not is_code_exec(name):
        return None
    truncated = len(code) > MAX_CODE_PREFIX
    code = code[:MAX_CODE_PREFIX]
    tokens = list(_code_tokens(code))
    latest = None
    for index in range(len(tokens) - 3):
        if (tokens[index][0] == 'tools' and tokens[index + 1][0] == '.'
                and _IDENTIFIER.fullmatch(tokens[index + 2][0]) and tokens[index + 3][0] == '('
                and (index == 0 or tokens[index - 1][0] != '.')):
            latest = index
    if latest is None or not is_patch_tool(tokens[latest + 2][0]):
        return None
    if truncated:
        return empty
    if latest + 4 >= len(tokens):
        return empty
    value, begin, end = tokens[latest + 4]
    if value or code[begin] not in ('"', "'", '`'):
        return empty
    # Interpolation and concatenation can alter filenames; stay conservative.
    if code[begin] == '`' and '${' in code[begin:end]:
        return empty
    decoded = _literal_prefix(code, begin, end)
    following = tokens[latest + 5][0] if latest + 5 < len(tokens) else None
    if decoded is None or (decoded[1] and following not in (None, ')', ',')):
        return empty
    return _patch_files(decoded[0])


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
                        preview = {'tool_name': name, 'command': summary, 'complete': complete,
                                   'command_name': command_name(command, complete=complete)}
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
        return (name + ' → ' + ', '.join(names)) if names else tr('diagnostic.code_mode', name=name)
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


def clean_name(value):
    if not isinstance(value, str):
        return ''
    return ' '.join(''.join(c for c in value if not unicodedata.category(c).startswith('C')).split())[:MAX_LABEL]


def tool_display(name, app_context=None):
    """One provider-first rule for all tools; no service aliases or inferred brands."""
    name = clean_name(name)
    context = app_context or {}
    product = clean_name(context.get('appName'))
    tool = clean_name(context.get('actionName'))
    raw_product, raw_tool = '', ''
    if name and not is_code_exec(name):
        normalized = name.split('mcp__', 1)[-1] if 'mcp__' in name else name
        if 'mcp__' in name:
            parts = re.split(r'__|\.', normalized)
            raw_product, raw_tool = ' · '.join(parts[:-1]), parts[-1]
        elif '/' in name:
            raw_product, raw_tool = name.rsplit('/', 1)
        else:
            raw_tool = name.rsplit('.', 1)[-1]
    product_source = 'display' if product else 'protocol' if raw_product else None
    tool_source = 'display' if tool else 'protocol' if raw_tool else None
    product, tool = product or raw_product, tool or raw_tool
    return {'product_name': product, 'tool_display_name': tool,
            'name_source': {'product': product_source, 'tool': tool_source}}


def _shell_words(command, complete):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()<>')
    lexer.whitespace_split = True
    words = []
    try:
        for word in lexer:
            words.append((word, True))
            if len(words) == 64:
                break
    except ValueError:
        if lexer.token:
            words.append((lexer.token, False))
        return words
    if words and not complete and command and command[-1] not in ' \t\r\n;&|()<>':
        words[-1] = (words[-1][0], False)
    return words


def command_name(command, complete=True, _depth=0):
    """Extract a proven executable token, never evaluating shell expressions."""
    if _depth > 4 or not isinstance(command, (str, list)):
        return None
    if isinstance(command, list):
        command = shlex.join(str(part) for part in command)
    if len(command) > MAX_CODE_PREFIX:
        command, complete = command[:MAX_CODE_PREFIX], False
    words = _shell_words(command, complete)
    index = 0
    assignments = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
    while index < len(words):
        token, known = words[index]
        if not known:
            return None
        if assignments.match(token):
            index += 1
            continue
        base = PurePath(token).name
        if base in ('env', 'sudo'):
            index += 1
            flags = ({'-i', '--ignore-environment', '-0', '--null', '-'} if base == 'env'
                     else {'-n', '-E', '-H', '-S', '-b', '-k', '-K'})
            valued = ({'-u', '--unset', '-C', '--chdir'} if base == 'env'
                      else {'-u', '--user', '-g', '--group', '-h', '--host', '-p', '--prompt', '-C', '--close-from'})
            while index < len(words):
                option, finished = words[index]
                if not finished:
                    return None
                if option == '--':
                    index += 1
                    break
                if not option.startswith('-'):
                    break
                if option in flags:
                    index += 1
                elif option in valued:
                    if index + 1 >= len(words) or not words[index + 1][1]:
                        return None
                    index += 2
                elif any(option.startswith(v + '=') for v in valued if v.startswith('--')):
                    index += 1
                else:
                    return None
            continue
        if base in {'bash', 'zsh', 'sh', 'dash', 'ksh', 'fish'}:
            for pos in range(index + 1, len(words)):
                option, finished = words[pos]
                if not finished:
                    return None
                if re.fullmatch(r'-[ilxvefu]*c[ilxvefu]*', option):
                    if pos + 1 >= len(words):
                        return None
                    body, body_complete = words[pos + 1]
                    return command_name(body, body_complete, _depth + 1)
                if option in ('--login', '--noprofile', '--norc') or re.fullmatch(r'-[ilxvefu]+', option):
                    continue
                break
        if base == 'cd':
            pos = index + 1
            if pos < len(words) and words[pos][0] in ('--', '-L', '-P'):
                pos += 1
            if pos + 1 < len(words) and words[pos][1] and words[pos + 1][0] in ('&&', ';'):
                index = pos + 2
                continue
        if (not base or base in ('.', '..') or token.startswith('-') or
                any(c in token for c in '$`…;&|()<>\n\r\x00')):
            return None
        return clean_name(base) or None
    return None
