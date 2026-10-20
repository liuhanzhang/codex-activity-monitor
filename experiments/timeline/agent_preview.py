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
        if case not in ('default', 'long', 'nested', 'legacy', 'single'):
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
