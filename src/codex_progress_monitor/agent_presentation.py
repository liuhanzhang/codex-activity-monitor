"""Bounded identity and presentation metadata; never changes activity ownership."""

from collections import Counter
from .agent_relations import OPERATIONS, thread_identity


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
    result = {'nickname': candidates[0] if candidates and 'agent_name_conflict' not in quality else None,
              'agent_path': path}
    source = payload.get('source')
    if isinstance(source, dict) and isinstance(source.get('subagent'), dict):
        result['thread_source'] = 'subagent'
        parent = spawn.get('parent_thread_id')
        if parent is not None:
            if thread_identity(parent) and parent != payload.get('id'):
                result.update(parent_thread_id=parent, parent_source='session_header')
            else:
                quality.add('agent_parent_invalid')
    elif isinstance(source, str) and source in ('vscode', 'cli', 'exec', 'appServer', 'user'):
        result['thread_source'] = 'user'
    result['quality'] = sorted(quality)
    return result


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
    invalid = {'collector_restarted', 'observation_stopped', 'observation_gap', 'disconnected',
               'identity_conflict', 'history_identity_mismatch', 'lifecycle_conflict', 'multiple_active_backends'}
    reliable = [p for p in phases if not invalid.intersection(p.get('quality', []))]
    if any(p['status'] == 'active' for p in reliable):
        return 'active'
    if any(p['status'] == 'waiting' for p in reliable):
        return 'waiting'
    unresolved = [p for p in phases if p.get('kind') != 'gap' and p['status'] in ('active', 'waiting', 'unknown')]
    # A known round end does not recover a missing item boundary. Only this
    # narrow absence may coexist with a terminal group; observation conflicts
    # and actual ongoing work retain the previous conservative behaviour.
    missing_only = all(p['status'] == 'unknown' and 'missing_item_end' in p.get('quality', []) and
                       not invalid.intersection(p.get('quality', [])) for p in unresolved)
    if unresolved and not missing_only:
        return 'unknown'
    terminal = {'completed', 'interrupted', 'failed'}
    if all(t['status'] in terminal and t.get('end_ms') is not None for t in turns):
        return 'failed' if any(t['status'] == 'failed' for t in turns) else (
            'interrupted' if any(t['status'] == 'interrupted' for t in turns) else 'completed')
    return 'unknown'


def present_coordination(phases, relations, scope, evidence_phases=None):
    by_call = {}
    for relation in relations:
        if relation['scope_id'] == scope:
            key = (relation['parent_thread_id'], relation['parent_turn_id'], relation.get('call_id'))
            by_call.setdefault(key, []).append(relation)
    raw_calls = {(p['thread_id'], p['turn_id'], p.get('call_id')): p for p in (evidence_phases or phases)
                 if p.get('namespace') == 'input' and p.get('call_operation')}
    for phase in phases:
        canonical = phase.get('namespace') == 'canonical' and phase.get('item_type') == 'collabAgentToolCall'
        raw = phase.get('namespace') == 'input' and phase.get('call_operation')
        if not canonical and not raw:
            continue
        call = phase.get('call_id') if raw else phase['item_id']
        key = (phase['thread_id'], phase['turn_id'], call)
        matches = by_call.get(key, [])
        evidence = phase if raw else raw_calls.get(key, {})
        if raw and not matches and evidence.get('call_target_scope') != 'global':
            continue
        actions = {OPERATIONS[r['operation']] for r in matches
                   if isinstance(r.get('operation'), str) and r['operation'] in OPERATIONS}
        action = evidence.get('call_operation') or OPERATIONS.get(phase.get('tool_name')) or (next(iter(actions)) if len(actions) == 1 else 'coordination')
        targets = list(dict.fromkeys(r['child_thread_id'] for r in matches))
        phase.update(coordination_action=action,
                     related_agent_ids=targets,
                     coordination_target_scope='specified' if targets else evidence.get('call_target_scope', 'unknown'))
