#!/usr/bin/env python3
"""Synthetic round-3 panel fixtures. No daily runtime or history is read."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from agent_preview import agent_fixtures

CASES = ('single', 'three', 'five', 'six', 'many', 'agents', 'nested', 'many-agents',
         'empty', 'recent', 'orphans', 'long-input', 'partial-input', 'missing-input', 'zero', 'unknown',
         'finished', 'lost-source', 'long-title', 'failed-input', 'associated')


def fixtures(base, case):
    snapshot, timeline = agent_fixtures(base, base + 90000, 'nested' if case == 'nested' else 'default')
    current = timeline['bundles']['current']
    count = {'three': 3, 'five': 5, 'six': 6, 'many': 12, 'empty': 0, 'recent': 0}.get(case, 1)
    if case not in ('agents', 'nested', 'many-agents'):
        current['agents'] = current['agents'][:1]
        current['phases'] = [p for p in current['phases'] if p['thread_id'] == 'root' and p['id'] != 'coordination']
    if case == 'many-agents':
        sample = current['agents'][-1]
        phase = current['phases'][-2]
        for i in range(8):
            identity = 'member-' + str(i)
            current['agents'].append({**sample, 'id': identity, 'thread_id': identity, 'display_name': '成员 ' + str(i+4),
                                      'parent_thread_id': 'root'})
            current['phases'].append({**phase, 'id': identity + '-model', 'thread_id': identity, 'turn_id': identity + '-turn'})
    root = snapshot['threads'][0]
    root.update(name='改进 Codex Activity Monitor 面板', cwd='/synthetic/codex-activity-monitor')
    snapshot['threads'] = []
    for i in range(count):
        identity = 'root' if i == 0 else 'chat-' + str(i)
        row = deepcopy(root)
        row.update(id=identity, name='改进 Codex Activity Monitor 面板' if i == 0 else '活跃聊天 ' + str(i+1))
        snapshot['threads'].append(row)
        if i:
            bundle = deepcopy(current)
            bundle.update(thread_id=identity, selected_turn_id=identity + '-turn', current_turn_id=identity + '-turn')
            bundle['turns'] = [{'thread_id': identity, 'turn_id': identity + '-turn', 'status': 'active', 'start_ms': base}]
            bundle['agents'] = [{'id': identity, 'thread_id': identity, 'name': '主智能体', 'status': 'active', 'status_scope': 'selected_parent_turn'}]
            bundle['phases'] = [{**p, 'id': identity + '-' + p['id'], 'thread_id': identity, 'turn_id': identity + '-turn'}
                                for p in current['phases'] if p['thread_id'] == 'root']
            bundle['markers'] = []
            timeline['threads'][identity] = {'bundles': {'current': bundle}}
    if case != 'empty':
        for i in range(2):
            identity = 'recent-' + str(i)
            bundle = deepcopy(timeline['threads']['recent']['bundles']['current'])
            bundle.update(thread_id=identity)
            for turn in bundle['turns']: turn['thread_id'] = identity
            bundle['agents'][0].update(id=identity, thread_id=identity)
            for phase in bundle['phases']:
                source = deepcopy(timeline['details'].get(phase['id']))
                phase.update(id=identity + '-' + phase['id'], thread_id=identity)
                if source:
                    source['phase'] = phase
                    timeline['details'][phase['id']] = source
            timeline['threads'][identity] = {'bundles': {'current': bundle}}
            snapshot['threads'].append({**deepcopy(root), 'id': identity, 'name': '最近结束 ' + str(i+1),
                'has_active_work': False, 'status': 'completed' if i == 0 else 'failed', 'last_progress_seconds': 20+i})
    if case in ('orphans', 'associated'):
        snapshot['threads'].append({**deepcopy(root), 'id': 'orphan', 'name': '父关系尚未到达的子任务',
                                    'thread_source': 'subagent', 'parent_thread_id': None})
        bundle = deepcopy(current); bundle['thread_id'] = 'orphan'
        bundle.update(selected_turn_id='orphan-turn', current_turn_id='orphan-turn',
                      turns=[{'thread_id': 'orphan', 'turn_id': 'orphan-turn', 'status': 'active', 'start_ms': base}])
        bundle['agents'] = []; bundle['phases'] = []; bundle['markers'] = []
        timeline['threads']['orphan'] = {'bundles': {'current': bundle}}
        if case == 'associated':
            snapshot['threads'][-1].update(parent_thread_id='root', parent_source='session_header')
            current['agents'].append({'id': 'orphan', 'thread_id': 'orphan', 'name': '已关联的子任务',
                'parent_thread_id': 'root', 'status': 'active', 'status_scope': 'selected_parent_turn'})
            linked = {**current['phases'][0], 'id': 'orphan-activity', 'thread_id': 'orphan', 'turn_id': 'orphan-turn'}
            current['phases'].append(linked)
            bundle['phases'] = [linked]

    initial = timeline['details']['input-initial']
    if case == 'long-input':
        initial['sections'][0]['text'] = '\n'.join(f'{i+1}. 这是合成输入，用于检查选择、复制和独立滚动。' for i in range(45))
    if case == 'long-title' and snapshot['threads']:
        snapshot['threads'][0]['name'] = '检查多个同名聊天与非常长的中文标题是否能保持紧凑对齐' * 3
    if case == 'finished':
        snapshot['threads'][0].update(has_active_work=False, status='completed', last_progress_seconds=1)
        current['turns'][0].update(status='completed', end_ms=base+90000)
        current['current_turn_id'] = None
        for phase in current['phases']:
            if phase['status'] == 'active': phase.update(status='completed', end_ms=base+90000)
        for agent in current['agents']: agent['status'] = 'completed'
        count = 0
    if case == 'lost-source':
        snapshot['threads'] = [p for p in snapshot['threads'] if p['id'] != 'root']
        count = 0
    if case == 'partial-input': initial['state'] = 'source_truncated'
    if case == 'missing-input':
        current['markers'] = [p for p in current['markers'] if not p.get('is_initial')]
    if case in ('zero', 'unknown') and snapshot['threads']:
        snapshot['threads'][0]['usage'] = ({'turn_id': 'turn-current', 'output_tokens': 0, 'reasoning_tokens': 0}
                                         if case == 'zero' else {})
    # Every interactive synthetic phase has a detail reply with matching identity.
    for owner in [{'bundles': timeline['bundles']}, *timeline['threads'].values()]:
        for bundle in owner['bundles'].values():
            for phase in bundle['phases'] + bundle['markers']:
                if phase['id'] not in timeline['details']:
                    timeline['details'][phase['id']] = {'version': 1, 'state': 'available', 'phase': phase,
                        'sections': [{'id': 'input', 'title': '合成记录', 'format': 'text',
                                      'text': '合成预览原始内容。', 'offset': 0, 'next_offset': None}],
                        'attachments': [], 'source': 'synthetic'}
    if case == 'failed-input': timeline['details'].pop('input-initial', None)
    snapshot['overview']['generation_rate'] = 48 * count
    snapshot['overview']['active_thread_count'] = count
    snapshot['menu_presentation']['active_conversation_count'] = count
    if count == 0: snapshot['menu_presentation'].update(mode='speed', reason='idle')
    return snapshot, timeline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--keep-fresh', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = time.time() * 1000 - 90000
    while True:
        selector = args.output / 'case.txt'
        case = selector.read_text().strip() if selector.exists() else 'single'
        if case not in CASES: raise ValueError('Unknown round-3 case: ' + case)
        snapshot, timeline = fixtures(base, case)
        snapshot['updated_at'] = time.time()
        for name, value in [('snapshot', snapshot), ('timeline', timeline)]:
            path = args.output / (name + '.json')
            temporary = path.with_suffix('.new')
            temporary.write_text(json.dumps(value, ensure_ascii=False))
            temporary.replace(path)
        if not args.keep_fresh: break
        time.sleep(.5)


if __name__ == '__main__': main()
