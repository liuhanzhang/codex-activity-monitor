"""Explicit hierarchy and bounded collaboration metadata, without message bodies."""

import json

SPAWN_OPERATIONS = ('spawnAgent', 'spawn_agent', 'spawn')
OPERATIONS = {value: action for action, values in {
    'spawn': SPAWN_OPERATIONS,
    'send': ('sendInput', 'send_input', 'send_message'),
    'followup': ('followup_task',),
    'wait': ('wait', 'waitAgent', 'wait_agent'),
    'close': ('closeAgent', 'close_agent'),
    'resume': ('resumeAgent', 'resume_agent'),
}.items() for value in values}


def thread_identity(value):
    return (isinstance(value, str) and 0 < len(value) <= 256 and
            not any(c.isspace() or ord(c) < 32 or c in '/\\\x00' for c in value))


def raw_coordination(item):
    """Only a formal collaboration function; never inspect Code Mode source."""
    if item.get('type') != 'function_call':
        return {}
    name = item.get('name', '')
    if not isinstance(name, str):
        return {}
    namespace = item.get('namespace')
    if name.startswith('collaboration.') and namespace in (None, 'collaboration'):
        operation = name.removeprefix('collaboration.')
    elif namespace == 'collaboration':
        operation = name
    else:
        return {}
    action = OPERATIONS.get(operation)
    if not action:
        return {}
    result = {'call_operation': action}
    if operation == 'wait_agent':
        try:
            arguments = json.loads(item.get('arguments', ''))
        except (ValueError, TypeError):
            return result
        # This API waits on the team's mailbox. Empty canonical receivers alone
        # do not establish this scope; a complete formal input does.
        if isinstance(arguments, dict) and set(arguments) <= {'timeout_ms'}:
            result['call_target_scope'] = 'global'
    return result


def parent_edges(views):
    """Resolve structural edges only. Communication never supplies a fallback."""
    by_id = {(v.get('home') or v['backend'], v['id']): v for v in views}
    candidates = {}
    for key, child in by_id.items():
        parent = child.get('parent_thread_id')
        if (parent and thread_identity(parent) and parent != key[1] and child.get('thread_source') != 'user' and
                'parent_identity_conflict' not in child.get('parent_quality', [])):
            candidates[key] = {parent}
    for key, parent in by_id.items():
        for child, operation in parent.get('subagents', {}).items():
            child_key = (key[0], child)
            if operation in SPAWN_OPERATIONS and child_key in by_id and child != key[1]:
                target = by_id[child_key]
                if (not target.get('parent_thread_id') and target.get('thread_source') != 'user' and
                        'parent_identity_conflict' not in target.get('parent_quality', [])):
                    candidates.setdefault(child_key, set()).add(key[1])
    edges = {key: next(iter(values)) for key, values in candidates.items() if len(values) == 1}
    valid = {}
    for key, parent in edges.items():
        seen = {key[1]}; cursor = parent
        while cursor not in seen and (key[0], cursor) in edges:
            seen.add(cursor); cursor = edges[(key[0], cursor)]
        if cursor not in seen:
            valid[key] = parent
    return valid
