"""Bounded identity and presentation metadata; never changes activity ownership."""

from collections import Counter


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _text(value, limit=160):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        return None
    return value.strip()


def agent_metadata(payload):
    spawn = _mapping(_mapping(_mapping(payload.get('source')).get('subagent')).get('thread_spawn'))
    quality = set()
    candidates = []
    for value in (payload.get('agent_nickname'), spawn.get('agent_nickname')):
        if value is not None:
            name = _text(value)
            if name:
                candidates.append(name)
            else:
                quality.add('agent_metadata_invalid')
    if len(set(candidates)) > 1:
        quality.add('agent_name_conflict')
    path = _text(spawn.get('agent_path'), 512)
    if spawn.get('agent_path') is not None and path is None:
        quality.add('agent_metadata_invalid')
    return {'nickname': candidates[0] if candidates and 'agent_name_conflict' not in quality else None,
            'agent_path': path, 'quality': sorted(quality)}


def short_ids(threads):
    threads = list(dict.fromkeys(threads))
    width = 6
    while width < max(map(len, threads), default=0) and len({t[-width:] for t in threads}) != len(threads):
        width += 1
    return {t: t[-width:] for t in threads}


def present_agents(agents, indices, names):
    children = agents[1:]
    suffixes = short_ids(a['thread_id'] for a in children)
    for agent in children:
        thread = agent['thread_id']
        metadata = getattr(indices.get(thread), 'agent_metadata', {})
        agent['quality'] = sorted(set(agent['quality']) | set(metadata.get('quality', [])))
        name = _text(names.get(thread))
        if name in (thread, thread[:8], '子智能体 · ' + thread[:8]):
            name = None
        nickname, path = metadata.get('nickname'), metadata.get('agent_path')
        if name:
            display, source = name, 'thread_name'
        elif nickname:
            display, source = nickname, 'history_nickname'
        elif path and 'agent_name_conflict' not in agent['quality'] and path.rstrip('/').rsplit('/', 1)[-1]:
            display, source = path.rstrip('/').rsplit('/', 1)[-1], 'history_path'
        else:
            display, source = suffixes[thread], 'thread_id'
        agent.update(display_name=display, name_source=source, nickname=nickname, agent_path=path,
                     status_scope='selected_parent_turn')
    duplicates = Counter(a['display_name'] for a in children)
    used = set()
    for agent in children:
        if duplicates[agent['display_name']] > 1:
            agent['display_name'] += ' · ' + suffixes[agent['thread_id']]
        while agent['display_name'] in used:
            agent['display_name'] += ' · ' + suffixes[agent['thread_id']]
        used.add(agent['display_name'])
        agent['name'] = '子智能体 · ' + agent['display_name']


def scoped_status(turns, phases):
    if not turns:
        return 'unknown'
    invalid = {'collector_restarted', 'observation_stopped', 'observation_gap', 'disconnected'}
    reliable = [p for p in phases if not invalid.intersection(p.get('quality', []))]
    if any(p['status'] == 'active' for p in reliable):
        return 'active'
    if any(p['status'] == 'waiting' for p in reliable):
        return 'waiting'
    if any(p['status'] in ('active', 'waiting', 'unknown') for p in phases if p.get('kind') != 'gap'):
        return 'unknown'
    terminal = {'completed', 'interrupted', 'failed'}
    if all(t['status'] in terminal and t.get('end_ms') is not None for t in turns):
        return 'failed' if any(t['status'] == 'failed' for t in turns) else (
            'interrupted' if any(t['status'] == 'interrupted' for t in turns) else 'completed')
    return 'unknown'


OPERATIONS = {value: action for action, values in {
    'spawn': ('spawnAgent', 'spawn_agent', 'spawn'),
    'send': ('sendInput', 'send_input', 'send_message'),
    'wait': ('wait', 'waitAgent', 'wait_agent'),
    'close': ('closeAgent', 'close_agent'),
    'resume': ('resumeAgent', 'resume_agent'),
}.items() for value in values}


def present_coordination(phases, relations, scope):
    by_call = {}
    for relation in relations:
        if relation['scope_id'] == scope:
            key = (relation['parent_thread_id'], relation['parent_turn_id'], relation.get('call_id'))
            by_call.setdefault(key, []).append(relation)
    for phase in phases:
        if phase.get('namespace') != 'canonical' or phase.get('item_type') != 'collabAgentToolCall':
            continue
        matches = by_call.get((phase['thread_id'], phase['turn_id'], phase['item_id']), [])
        actions = {OPERATIONS[r['operation']] for r in matches if r.get('operation') in OPERATIONS}
        action = OPERATIONS.get(phase.get('tool_name')) or (next(iter(actions)) if len(actions) == 1 else 'coordination')
        phase.update(coordination_action=action,
                     related_agent_ids=list(dict.fromkeys(r['child_thread_id'] for r in matches)))
