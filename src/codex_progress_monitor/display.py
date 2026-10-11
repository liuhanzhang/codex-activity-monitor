"""A terminal view of metrics snapshots. It never reads generated text."""

import json
import select
import sys
import time
from copy import deepcopy
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .runtime import read_snapshot


def seconds(value):
    if value is None:
        return '—'
    if value<60:
        return f'{value:.1f}s'
    return f'{int(value//60)}m{int(value%60):02}s'


def render(snapshot,selected=0,detail=False,thread_filter=None):
    age=time.time()-(snapshot.get('updated_at') or 0)
    stale=age>3 or not snapshot.get('running',True)
    if stale:
        snapshot=deepcopy(snapshot)
        for thread in snapshot.get('threads',[]):
            thread['connected']=False
            thread['token_rates']={'text':None,'tool_input':None}
            thread['quality']=sorted(set(thread.get('quality',[])) | {'stale_snapshot'})
            if thread.get('last_progress_seconds') is not None:
                thread['last_progress_seconds']=thread['last_progress_seconds']+max(0,age) if snapshot.get('updated_at') else None
            if thread['status']=='inProgress' or thread.get('active_count'):
                thread.update(status='unknown',phase='unknown',phase_label='观测中断',phase_seconds=None,active_count=0,has_active_work=False)
            for activity in thread.get('activities',[]):
                if activity['active']:
                    activity.update(active=False,status='unknown')
    threads=[t for t in snapshot.get('threads',[]) if (t['status']!='idle' or t.get('active_count'))
             and (t.get('ephemeral') is not True or thread_filter)]
    if thread_filter:
        threads=[t for t in threads if thread_filter in t['id'] or thread_filter in t['name']]
    table=Table(expand=True,box=None,padding=(0,1))
    for label in ('聊天 / 项目','阶段 · 活动','持续','正文 / 参数 token/s','本段 token','最近进展','采集'):
        table.add_column(label)
    selected=min(selected,max(0,len(threads)-1))
    for index,thread in enumerate(threads):
        active=[a for a in thread['activities'] if a['active']]
        primary=next((a for a in active if a['id']==thread.get('primary_activity_id')),None)
        current=primary or (thread['activities'][0] if thread['activities'] else None)
        streams=current['streams'] if current else []
        known=[s['tokens'] for s in streams if s.get('tokens') is not None and s['kind']!='summary']
        total=f'{sum(known):,}' if known else '—'
        if any(s.get('estimated') for s in streams):
            total='≈'+total if known else total
        phase=thread['phase_label']
        if current and (current['kind']=='reasoning'):
            phase+=f" · 摘要 {current['summary_chars']} 字/{current['summary_parts']} 段"
        elif primary and primary['label']:
            phase+=' · '+primary['label'][:40]
        if primary and not primary.get('turn_bound',True) and thread['status']=='completed':
            phase+=' · 回合已完成'
        if thread['active_count']>1:
            phase+=f" (+{thread['active_count']-1})"
        rates=thread['token_rates']
        estimated={k:any(s.get('estimated') for a in active for s in a['streams'] if s['kind']==k) for k in ('text','tool_input')}
        rate=' / '.join('—' if rates.get(k) is None else ('≈' if estimated[k] else '')+f'{rates[k]:.1f}' for k in ('text','tool_input'))
        quality='断开' if not thread['connected'] else ', '.join(thread['quality']) or '正常'
        cwd=Path(thread['cwd']).name if thread.get('cwd') else ''
        name=('› ' if index==selected else '  ')+thread['name']+'\n  '+thread['id'][:8]+(' · '+cwd if cwd else '')
        table.add_row(Text(name),Text(phase),seconds(thread.get('phase_seconds')),rate,total,
                      seconds(thread.get('last_progress_seconds'))+' 前',Text(quality),
                      style='bold' if index==selected else None)
    state='采集进程未运行 / 快照已过期' if stale else '本机实时采集'
    header=Text(f'Codex Progress · {state} · {len(threads)} 条聊天',style='bold')
    footer=Text('j/k 或 ↑/↓ 选择 · Enter 展开活动 · q 退出 | 接收文本 token/s · ≈ 编码未知时用 o200k_base 估算 · 2 秒窗口',style='dim')
    counts=snapshot.get('counters',{})
    if snapshot.get('unassociated_events') or counts.get('unassociated_expired'):
        footer.append(f"\n未关联: {snapshot.get('unassociated_events',0)}，已过期: {counts.get('unassociated_expired',0)}")
    elements=[header,table if threads else Text('等待本机 Codex 任务。首次接入后需重启 Desktop 才会创建受监控后端。'),footer]
    if detail and threads:
        thread=threads[selected]
        details=Table(box=None,expand=True)
        for label in ('活动','状态','经过时间','接收指标 / 来源'):
            details.add_column(label)
        for activity in thread['activities']:
            notes=[]
            for stream in activity['streams']:
                total='—' if stream.get('tokens') is None else str(stream['tokens'])
                notes.append(f"{stream['kind']}: {total} token / {stream['chars']} 字 / {stream['deltas']} Δ · {stream['source']} · {stream['encoding']} ({'估算' if stream['estimated'] else '模型映射'}) · {stream['quality']} · 修正 {stream['corrections']}")
                if stream.get('mean_received_rate') is not None:
                    notes.append(f"本段接收均速 {stream['mean_received_rate']:.1f} token/s")
            if activity['summary_chars']:
                notes.append(f"摘要 {activity['summary_chars']} 字 / {activity['summary_parts']} 段")
            if activity.get('output_chars'):
                notes.append(f"工具输出 {activity['output_chars']} 字")
            if activity.get('exit_code') is not None:
                notes.append(f"exit {activity['exit_code']}")
            details.add_row(Text(activity['kind']+' · '+activity['label']),activity['status'],
                            seconds(activity['duration_seconds']) if activity.get('start_known') else '开始未知',Text('\n'.join(notes)))
        elements.append(Panel(Group(Text(thread['id']+'\n'+thread.get('cwd','')+'\n模型 '+thread['model']),details),title=Text(thread['name'])))
    return Group(*elements),len(threads)


def watch(runtime,once=False,json_output=False,thread_filter=None):
    console=Console()
    if once:
        snapshot=read_snapshot(runtime)
        if json_output:
            print(json.dumps(snapshot,ensure_ascii=False,indent=2))
        else:
            console.print(render(snapshot,detail=bool(thread_filter),thread_filter=thread_filter)[0])
        return
    if json_output or not sys.stdout.isatty():
        try:
            while True:
                print(json.dumps(read_snapshot(runtime),ensure_ascii=False),flush=True)
                time.sleep(0.5)
        except KeyboardInterrupt:
            return
    import termios
    import tty
    old=None
    if sys.stdin.isatty():
        old=termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin)
    selected,detail=0,bool(thread_filter)
    try:
        with Live(console=console,refresh_per_second=4,screen=True) as live:
            while True:
                visual,count=render(read_snapshot(runtime),selected,detail,thread_filter)
                live.update(visual)
                if old and select.select([sys.stdin],[],[],0.25)[0]:
                    key=sys.stdin.read(1)
                    if key=='q':
                        break
                    if key in ('j','B'):
                        selected=min(selected+1,max(0,count-1))
                    elif key in ('k','A'):
                        selected=max(0,selected-1)
                    elif key in ('\n',' '):
                        detail=not detail
                elif not old:
                    time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        if old:
            termios.tcsetattr(sys.stdin,termios.TCSADRAIN,old)
