"""Synthetic native timeline fixtures; no real chats, history reads or observation lease."""

import argparse
import json
from pathlib import Path
import time

from codex_progress_monitor.runtime import atomic_json
from codex_progress_monitor.history import item_content, raw_section


def fixtures(base, now):
    phases = []
    def phase(key, kind, start, end, thread='root', turn='turn-current', label=None, state='completed', quality=None):
        p = {'id': key, 'scope_id': 'fixture', 'thread_id': thread, 'turn_id': turn, 'item_id': key,
             'item_type': {'input': 'userMessage', 'contextCompaction': 'contextCompaction',
                           'tool_execution': 'commandExecution'}.get(kind, kind),
             'namespace': 'model' if kind in ('reasoning', 'text', 'tool_input', 'file_edit_input') else 'canonical',
             'track': 'marker' if kind in ('input', 'approval') else 'tool' if kind == 'tool_execution' else 'model',
             'kind': kind, 'label': label or {'reasoning': '思考', 'text': '正文输出', 'tool_input': '准备工具调用',
                                           'file_edit_input': '生成文件改动', 'tool_execution': '运行命令', 'input': '用户输入',
                                           'contextCompaction': '整理上下文'}.get(kind, kind),
             'status': state, 'start_ms': base + start * 1000 if start is not None else None,
             'end_ms': base + end * 1000 if end is not None else None, 'observed_until_ms': now,
             'start_source': 'producer', 'end_source': 'producer', 'quality': quality or []}
        phases.append(p); return p
    phase('input-initial', 'input', 0, 0)['is_initial'] = True
    phase('background-long', 'tool_execution', 0, 90, label='持续运行的预览服务', state='failed')
    phase('thinking-1', 'reasoning', 0, 10)
    phase('text-1', 'text', 10, 13)
    phase('params-1', 'tool_input', 13, 17)
    phase('tool-1', 'tool_execution', 17, 25, label='读取项目文件')
    phase('thinking-2', 'reasoning', 25, 29)
    phase('compaction', 'contextCompaction', 29, 33)
    for n in range(18):
        phase(f'dense-{n:02d}', 'tool_execution', 26+n*.002, 26+n*.002+.001, label=f'短工具 {n+1:02d}')
    old_hook = phase('old-hook', 'approval', 27, 27, label='旧准备运行标记')
    old_hook.update(kind='other', item_type='sleep')
    phase('params-2', 'tool_input', 33, 38)
    phase('text-2', 'text', 38, 41)
    phase('edit-input', 'file_edit_input', 41, 47)
    phase('short-a', 'tool_execution', 42.2, 42.205, label='快速读取')
    phase('short-b', 'tool_execution', 44.3, 44.31, label='快速检查')
    phase('file-apply', 'tool_execution', 47, 48, label='应用文件改动')
    phase('test-run', 'tool_execution', 48, 63, label='验证改动')
    phase('parallel-tool', 'tool_execution', 52, 66, label='检查项目结构')
    phase('parallel-tool-2', 'tool_execution', 52, 65, label='检查测试覆盖')
    phase('after-parallel', 'tool_execution', 68, 70, label='后续独立工具')
    phase('input-steer', 'input', 55, 55)['is_initial'] = False
    phase('thinking-3', 'reasoning', 60, 66)
    # Synthetic mixed-clock boundary overlap must not collapse model stages.
    phase('text-3', 'text', 65.978, 70)
    phase('params-3', 'tool_input', 69.978, 76)
    phase('tool-3', 'tool_execution', 76, 82, label='读取验证结果')
    phase('failed-short', 'tool_execution', 84, 84.005, label='未通过的快速检查', state='failed')
    phase('unknown-params', 'tool_input', None, 79, quality=['start_unknown'])
    phase('short-model', 'reasoning', 80, 80.046)
    phase('text-live', 'text', 82, None, state='active')
    phase('review-model', 'reasoning', 15, 36, thread='review', turn='review-turn')
    phase('review-tool', 'tool_execution', 36, 58, thread='review', turn='review-turn', label='审查文件')
    phase('review-result', 'text', 59, 69, thread='review', turn='review-turn')
    phase('blocked-command', 'tool_execution', None, 78, thread='review', turn='review-turn',
          label='需要审核的命令', state='declined')
    phase('search-model', 'tool_input', 44, 50, thread='search', turn='search-turn')
    phase('search-tool', 'tool_execution', 50, 74, thread='search', turn='search-turn', label='搜索相关资料')
    phase('search-result', 'text', 75, 86, thread='search', turn='search-turn')
    current = {'version': 1, 'scope_id': 'fixture', 'thread_id': 'root', 'selected_turn_id': 'turn-current',
               'current_turn_id': 'turn-current', 'turns': [
                   {'thread_id': 'root', 'turn_id': 'turn-current', 'status': 'active', 'start_ms': base},
                   {'thread_id': 'root', 'turn_id': 'turn-previous', 'status': 'completed', 'start_ms': base - 180000}],
               'agents': [{'id': 'root', 'thread_id': 'root', 'name': '主智能体', 'parent_thread_id': None, 'quality': []},
                          {'id': 'review', 'thread_id': 'review', 'name': '子智能体 · 代码审查', 'parent_thread_id': 'root', 'quality': []},
                          {'id': 'search', 'thread_id': 'search', 'name': '子智能体 · 资料搜索', 'parent_thread_id': 'root', 'quality': []}],
               'phases': [p for p in phases if p['track'] != 'marker'], 'markers': [p for p in phases if p['track'] == 'marker'],
               'origin_ms': base, 'end_ms': now, 'now_ms': now, 'revision': 'synthetic', 'quality': [], 'earlier_active_turns': []}
    old = phase('old-text', 'text', -180, -165, turn='turn-previous')
    previous = {**current, 'selected_turn_id': 'turn-previous', 'phases': [old], 'markers': [],
                'agents': current['agents'][:1], 'origin_ms': base - 180000, 'end_ms': base - 165000}
    details = {}
    for p in phases:
        kind = p['kind']; format = 'text'; title = '正文'
        if p['status'] == 'declined':
            details[p['id']] = {'version': 1, 'state': 'no_content', 'phase': p, 'sections': [],
                                'attachments': [], 'source': 'synthetic'}
            continue
        if kind == 'contextCompaction':
            details[p['id']] = {'version': 1, 'state': 'no_content', 'phase': p, 'sections': [],
                                'attachments': [], 'source': 'synthetic'}
            continue
        if kind == 'file_edit_input':
            body = '*** Begin Patch\n*** Update File: Monitor.swift\n@@\n-    showTextHistory()\n+    showActivityTimeline()\n*** End Patch'
            format = 'code'; title = '编辑内容'
        elif p['id'] == 'file-apply':
            body = '--- Monitor.swift\n+++ Monitor.swift\n@@ -12,1 +12,1 @@\n-    showTextHistory()\n+    showActivityTimeline()'
            format = 'diff'; title = '文件改动'
        elif kind == 'tool_input':
            body = 'const result = await tools.exec_command({cmd: "python3 -B -c \'print(42)\'"});\ntext(result.output);'
            format = 'code'; title = '参数／代码'
        elif kind == 'tool_execution':
            body = ('合成失败记录：退出码 1，用于检查失败状态。' if p['status'] == 'failed' else
                    '验证完成：全部 25 项检查通过。\n未发现不匹配的活动身份。\n时间线保留独立的模型与工具阶段。')
            format = 'code'; title = '输出'
        elif kind == 'reasoning':
            body = '先检查事件中提供的稳定身份和时间边界，再按来源类型组织轨道。对缺少开始时间的活动保留未知状态。'
            title = '可见思考摘要'
        elif kind == 'input':
            body = '请检查这个项目，并整理当前代码结构。' if p['id'] == 'input-initial' else '请同时检查子智能体的执行情况。'
            title = '用户输入'
        else:
            body = '已完成项目结构检查。\n\n模型生成与工具执行分别显示在固定轨道中；子智能体保留自己的活动归属。\n\n点击任一时间块可以查看对应的输入与结果。'
        sections = [{'id': 'body', 'title': title, 'format': format, 'text': body}]
        if kind in ('tool_input', 'file_edit_input'):
            sections = [raw_section('input', '参数', body), raw_section('output', '结果', [
                {'type': 'text', 'text': '合成工具结果', 'example': {'success': False, 'value': None}}])]
        elif p['id'] == 'file-apply':
            sections, _ = item_content({'type': 'FileChange', 'status': 'completed',
                'changes': {'Monitor.swift': {'unified_diff': '@@\n- old()\n+ new()'}}})
        elif kind == 'tool_execution':
            sections, _ = item_content({'type': 'CommandExecution', 'command': ['python3', '-c', 'print(42)'],
                'cwd': '/example/monitor', 'aggregated_output': body, 'exit_code': 1 if p['status'] == 'failed' else 0,
                'status': p['status'],
                'duration': {'secs': 0, 'nanos': 1000000}})
        for s in sections:
            s.update(offset=0, total_chars=len(s['text']), next_offset=None)
        details[p['id']] = {'version': 1, 'state': 'available', 'phase': p, 'sections': sections,
                             'attachments': [], 'source': 'synthetic'}
    snapshot = {'schema_version': 1, 'updated_at': now / 1000, 'running': True,
                'menu_presentation': {'version': 1, 'mode': 'speed', 'active_conversation_count': 2, 'reason': 'multi_conversation'},
                'overview': {'version': 1, 'coverage': 'complete', 'generation_rate': 48, 'active_thread_count': 4},
                'timeline_summary': {'version': 1, 'revision': 1, 'available': True, 'quality': []},
                'backends': {'fixture': {'connected': True, 'implementation': {'modules': {'app_session': 'synthetic'}}}}, 'threads': [
                    {'id': 'root', 'backend': 'fixture', 'name': '项目检查与代码结构整理', 'cwd': '/example/monitor',
                     'turn_id': 'turn-current', 'status': 'inProgress', 'connected': True, 'has_active_work': True,
                     'display_label': '整理检查结果', 'display_seconds': 8, 'generation_rate': 48,
                     'model': 'Codex', 'usage': {'output_tokens': 12400, 'reasoning_tokens': 8200, 'latest_reasoning': {'tokens_per_second': 42}}},
                    {'id': 'other', 'backend': 'fixture', 'name': '文档与资源检查', 'cwd': '/example/notes', 'status': 'inProgress',
                     'connected': True, 'has_active_work': True, 'display_label': '思考中', 'display_seconds': 6, 'generation_rate': 0},
                    {'id': 'recent', 'backend': 'fixture', 'name': '上一次项目扫描', 'status': 'completed', 'connected': True,
                     'has_active_work': False, 'display_label': '已完成', 'last_progress_seconds': 60, 'generation_rate': 0}]}
    return snapshot, {'bundles': {'current': current, 'turn-current': current, 'turn-previous': previous}, 'details': details}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--keep-fresh', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = time.time() * 1000 - 90000
    while True:
        # Fixed timeline geometry; only freshness changes. Live growth is tested separately.
        snapshot, timeline = fixtures(base, base + 90000)
        snapshot['updated_at'] = time.time()
        atomic_json(args.output / 'snapshot.json', snapshot)
        atomic_json(args.output / 'timeline.json', timeline)
        if not args.keep_fresh:
            break
        time.sleep(.5)


if __name__ == '__main__':
    main()
