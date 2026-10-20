#!/usr/bin/env python3
"""Synthetic three-agent native preview; never reads the daily runtime."""

import argparse
from copy import deepcopy
import json
import time
from pathlib import Path

from preview import fixtures


def agent_fixtures(base, now, case='default'):
    snapshot, timeline = fixtures(base, now)
    replacements = {'review': '01a15451-1111-7000-8000-000000abc123',
                    'search': '01a15451-2222-7000-8000-000000def456'}

    def replace(value):
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return replacements.get(value, value) if isinstance(value, str) else value

    timeline = replace(timeline)
    current = timeline['bundles']['current']
    for agent, name, state in zip(current['agents'], ['主智能体', 'Lorentz', 'Parfit'], ['active', 'completed', 'unknown']):
        agent.update(display_name=name, status=state, status_scope='selected_parent_turn')
        if agent['thread_id'] != 'root':
            agent.update(name='子智能体 · '+name, name_source='history_nickname')
    third = '01a1546d-3333-7000-8000-000000fac789'
    current['agents'].append({'id': third, 'thread_id': third, 'name': '子智能体 · Sagan', 'display_name': 'Sagan',
        'name_source': 'history_nickname', 'parent_thread_id': 'root', 'status': 'active',
        'status_scope': 'selected_parent_turn', 'quality': [], 'agent_path': '/root/native_storage'})
    sample = deepcopy(next(p for p in current['phases'] if p['kind'] == 'reasoning'))
    sample.update(id='third-thinking', thread_id=third, turn_id='third-turn', item_id='third-thinking',
                  start_ms=base+64000, end_ms=base+85000)
    current['phases'].append(sample)
    live = {**sample, 'id': 'third-live', 'item_id': 'third-live', 'kind': 'text', 'status': 'active',
            'label': '正文输出', 'start_ms': base+86000, 'end_ms': None}
    current['phases'].append(live)
    call = {**sample, 'id': 'coordination', 'item_id': 'coordination', 'thread_id': 'root', 'turn_id': 'turn-current',
            'namespace': 'canonical', 'item_type': 'collabAgentToolCall', 'kind': 'tool_execution', 'track': 'tool',
            'label': 'wait', 'coordination_action': 'wait', 'related_agent_ids': list(replacements.values())+[third],
            'start_ms': base+64000, 'end_ms': base+76000}
    current['phases'].append(call)
    timeline['bundles']['turn-current'] = current
    for phase in (sample, live, call):
        text = json.dumps({'receiver_thread_ids': call['related_agent_ids']}, indent=2) if phase is call else '合成智能体内容。'
        sections = [{'id': 'input', 'title': '参数' if phase is call else '正文', 'format': 'code', 'text': text,
                     'offset': 0, 'total_chars': len(text), 'next_offset': None}]
        if phase is call:
            output = json.dumps({'agents_states': {child: {'status': 'completed'} for child in call['related_agent_ids']}}, indent=2)
            sections.append({'id': 'output', 'title': '结果', 'format': 'code', 'text': output,
                             'offset': 0, 'total_chars': len(output), 'next_offset': None})
        timeline['details'][phase['id']] = {'version': 1, 'state': 'available', 'phase': phase, 'source': 'synthetic',
            'sections': sections, 'attachments': []}
    snapshot['threads'] = snapshot['threads'][:1]
    snapshot['threads'][0]['name'] = '检查三个子智能体的活动'
    snapshot['overview']['active_thread_count'] = 1
    snapshot['menu_presentation']['active_conversation_count'] = 1
    if case == 'long':
        current['agents'][1].update(display_name='检查原生采集与本地存储并验证多种兼容场景的子智能体名称' * 2)
    elif case == 'nested':
        current['agents'][-1]['parent_thread_id'] = replacements['review']
    elif case == 'legacy':
        for agent in current['agents']:
            for key in ('display_name', 'name_source', 'status_scope'):
                agent.pop(key, None)
    elif case == 'single':
        current['agents'] = current['agents'][:1]
        current['phases'] = [p for p in current['phases'] if p['thread_id'] == 'root']
    elif case == 'fixes':
        current['current_turn_id'] = None
        current['turns'][0].update(status='completed', end_ms=now)
        for agent in current['agents']:
            agent.update(status='completed')
        current['agents'][0]['activity_quality'] = ['missing_item_end']
        for phase in current['phases']:
            if phase['status'] == 'active':
                phase.update(status='completed', end_ms=now)
        missing = next(p for p in current['phases'] if p['id'] == 'params-3')
        missing.update(status='unknown', end_ms=None, quality=['missing_item_end'])
        for key, action in [('params-1', 'spawn'), ('params-2', 'followup')]:
            phase = next(p for p in current['phases'] if p['id'] == key)
            phase.update(namespace='input', item_type='function_call', coordination_action=action,
                         related_agent_ids=[current['agents'][1]['id']], coordination_target_scope='specified')
            timeline['details'][key]['phase'] = phase
        call.update(related_agent_ids=[], coordination_target_scope='global')
        timeline['details'][call['id']]['sections'] = [
            {'id':'input', 'title':'参数', 'format':'code', 'text':'{"timeout_ms":45000}', 'offset':0, 'total_chars':20, 'next_offset':None},
            {'id':'output', 'title':'结果', 'format':'code', 'text':'{"message":"Wait completed."}', 'offset':0, 'total_chars':29, 'next_offset':None}]
        snapshot['threads'][0].update(status='completed', has_active_work=False, last_progress_seconds=5,
                                     display_label='已完成', thread_source='user')
        for agent in current['agents'][1:]:
            snapshot['threads'].append(dict(id=agent['id'], backend='fixture', name=agent['display_name'],
                thread_source='subagent', parent_thread_id='root', parent_source='session_header',
                status='completed', has_active_work=False, last_progress_seconds=10, connected=True))
        snapshot['threads'].append(dict(id='orphan', backend='fixture', name='父身份尚未到达的子任务',
            thread_source='subagent', status='completed', has_active_work=False, last_progress_seconds=15, connected=True))
        orphan = {**current, 'thread_id':'orphan', 'selected_turn_id':'orphan-turn', 'turns':[],
            'agents':[dict(id='orphan', thread_id='orphan', name='主智能体', status='unknown',
                           status_scope='selected_parent_turn', quality=['turn_relation_unknown'])], 'phases':[], 'markers':[]}
        timeline['threads']['orphan'] = {'bundles': {'current': orphan}}
    return snapshot, timeline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--keep-fresh', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = time.time()*1000-90000
    while True:
        selector = args.output / 'case.txt'
        case = selector.read_text().strip() if selector.exists() else 'default'
        if case not in ('default', 'long', 'nested', 'legacy', 'single', 'fixes'):
            raise ValueError('Unknown agent preview case')
        snapshot, timeline = agent_fixtures(base, base+90000, case)
        snapshot['updated_at'] = time.time()
        for name, value in [('snapshot', snapshot), ('timeline', timeline)]:
            path = args.output/(name+'.json')
            temporary = path.with_suffix('.new')
            temporary.write_text(json.dumps(value, ensure_ascii=False))
            temporary.replace(path)
        if not args.keep_fresh:
            break
        time.sleep(.5)


if __name__ == '__main__':
    main()
