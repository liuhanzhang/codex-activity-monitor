#!/usr/bin/env python3
"""Write synthetic GUI cases. Never reads or modifies the daily runtime."""

import argparse
import json
from pathlib import Path
import time

CASES = ['multiple','aggregate','slow','fast','thinking','executing','generating','huge','partial','empty','idle','waiting','long','tree']


def fixture(case):
    def chat(identity, title, project='codex_progress_monitor', active=True):
        return dict(id=identity, home='/preview', name=title, cwd='/preview/'+project,
                    model='preview-model', status='inProgress' if active else 'completed',
                    connected=True, visible=True, has_active_work=active,
                    display_label='编辑界面' if active else '任务完成', display_seconds=18 if active else None,
                    generation_rate=48.2 if active else None, last_progress_seconds=1,
                    usage=dict(output_tokens=3240, reasoning_tokens=1280, latest_reasoning=dict(tokens_per_second=43.1)),
                    activities=[dict(id=identity+'-tool', display_label='编辑 DashboardView.swift', display_visible=True,
                                     active=active, status='inProgress' if active else 'completed', duration_seconds=18,
                                     start_known=True)])

    threads = [chat('design', '打磨 Monitor 界面'), chat('capture', '调查活动采集', 'cairn')]
    threads[1].update(display_label='运行命令', generation_rate=None, display_seconds=42)
    threads += [chat('recent1', '检查状态切换', active=False), chat('recent2', '整理界面需求', active=False)]
    mode, count, rate, coverage = 'speed', 2, 48.2, 'complete'
    if case in ('thinking', 'executing', 'generating', 'huge', 'slow', 'fast'):
        threads = [threads[0]] + threads[2:]
        count = 1
        mode = dict(thinking='thinking', executing='executing').get(case, 'speed')
        rate = dict(huge=1e100, generating=48.2, slow=9, fast=9999).get(case, 0)
        threads[0].update(display_label={'thinking':'思考中', 'executing':'运行命令'}.get(case, '生成回答'),
                          generation_rate=rate if mode == 'speed' else None)
    elif case == 'aggregate':
        rate = 126.3
        threads[1].update(display_label='生成回答', generation_rate=78.1)
    elif case == 'partial':
        rate, coverage, mode = None, 'partial', 'unavailable'
    elif case in ('empty', 'idle'):
        threads, count, rate = [], 0, 0
    elif case == 'waiting':
        threads, count, rate = [threads[0]], 1, 0
        threads[0].update(display_label='等待用户输入', generation_rate=0)
    elif case == 'long':
        threads = [chat('long'+str(i), '检查长标题、多层级与滚动时的界面可读性和稳定排序 '+str(i),
                        'very_long_project_name_for_layout_validation') for i in range(12)]
        count, rate = 12, 578.4
    elif case == 'tree':
        parent = chat('parent', '父聊天已完成，子代理仍在工作', active=False)
        parent.update(last_progress_seconds=700, subagents={'design':'running', 'capture':'running'})
        for thread in threads[:2]:
            thread['parent_thread_id'] = 'parent'
        threads.insert(0, parent)
        count = 1
    return dict(schema_version=1, running=True, updated_at=time.time(), threads=threads,
                overview=dict(version=1, coverage=coverage, generation_rate=rate, observed_subtotal=48.2 if coverage=='partial' else rate,
                              estimated=True, active_thread_count=sum(t['has_active_work'] for t in threads), quality=[]),
                menu_presentation=dict(version=1, mode=mode, active_conversation_count=count,
                                       reason='idle' if case in ('empty', 'idle') else case),
                backends={'preview':dict(connected=True, implementation={'modules':{'app_session':'synthetic'}})})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=CASES, default='multiple')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--keep-fresh', action='store_true')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Read an optional sibling case.txt to change a running preview without
    # restarting the App; the JSON is always replaced atomically.
    try:
        while True:
            selector = args.output.with_name('case.txt')
            case = selector.read_text().strip() if args.keep_fresh and selector.exists() else args.case
            if case not in CASES:
                raise ValueError('Unknown preview case: '+case)
            temporary = args.output.with_suffix('.new')
            temporary.write_text(json.dumps(fixture(case), ensure_ascii=False))
            temporary.replace(args.output)
            if not args.keep_fresh:
                break
            time.sleep(.5)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
