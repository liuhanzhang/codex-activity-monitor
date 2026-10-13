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
            thread['generation_rate']=None
            thread['quality']=sorted(set(thread.get('quality',[])) | {'stale_snapshot'})
            if thread.get('last_progress_seconds') is not None:
                thread['last_progress_seconds']=thread['last_progress_seconds']+max(0,age) if snapshot.get('updated_at') else None
            if thread['status']=='inProgress' or thread.get('active_count'):
                thread.update(status='unknown',phase='unknown',phase_label='连接中断',display_label='连接中断',
                              phase_seconds=None,display_seconds=None,display_active_count=0,active_count=0,has_active_work=False)
            for activity in thread.get('activities',[]):
                if activity['active']:
                    activity.update(active=False,status='unknown')
    threads=[t for t in snapshot.get('threads',[]) if (t['status']!='idle' or t.get('active_count'))
             and t.get('visible',True) and (t.get('ephemeral') is not True or thread_filter)]
    if thread_filter:
        threads=[t for t in threads if thread_filter in t['id'] or thread_filter in t['name']]
    table=Table(expand=True,box=None,padding=(0,1))
    for label in ('聊天 / 项目','当前活动','耗时','生成速度'):
        table.add_column(label)
    selected=min(selected,max(0,len(threads)-1))
    for index,thread in enumerate(threads):
        active=[a for a in thread['activities'] if a['active']]
        primary=next((a for a in active if a['id']==thread.get('display_primary_id',thread.get('primary_activity_id'))),None)
        phase=thread.get('display_label',thread['phase_label'])
        if primary and not primary.get('turn_bound',True) and thread['status']=='completed':
            phase+=' · 回合已完成'
        parallel=thread.get('display_active_count',thread['active_count'])
        if parallel>1:
            phase+=f" · 另 {parallel-1} 项进行中"
        value=thread.get('generation_rate')
        rate='—' if value is None else ('≈' if thread.get('generation_estimated') else '')+f'{value:.1f} token/s'
        cwd=Path(thread['cwd']).name if thread.get('cwd') else ''
        title=thread['name'] if thread['name']!=thread['id'][:8] else '未命名聊天'
        name=('› ' if index==selected else '  ')+title+('\n  '+cwd if cwd else '')
        table.add_row(Text(name),Text(phase),seconds(thread.get('display_seconds',thread.get('phase_seconds'))),rate,
                      style='bold' if index==selected else None)
    state='连接中断' if stale else '本机进度'
    header=Text(f'Codex Progress · {state} · {len(threads)} 条聊天',style='bold')
    footer=Text('j/k 或 ↑/↓ 选择 · Enter 展开活动 · q 退出 | 生成速度：收到的模型文本 · — 暂无可用生成流 · ≈ 估算',style='dim')
    elements=[header,table if threads else Text('等待 Codex 任务。'),footer]
    if detail and threads:
        thread=threads[selected]
        details=Table(box=None,expand=True)
        for label in ('活动','状态','耗时'):
            details.add_column(label)
        for activity in thread['activities']:
            if not activity.get('display_visible',True) or activity['kind'] in ('approval','input'):
                continue
            state='进行中' if activity['active'] else {'completed':'完成','failed':'失败','declined':'未执行',
                    'approved':'审核通过','denied':'审核未通过','timedOut':'审核超时','aborted':'已停止',
                    'interrupted':'已停止','unknown':'状态暂不可用','resolved':'已结束','superseded':'已结束'}.get(activity['status'],'已结束')
            if activity.get('exit_code') not in (None,0):
                state='命令失败'
            details.add_row(Text(activity.get('display_label','处理中')),state,
                            seconds(activity['duration_seconds']) if activity.get('start_known') else '—')
        context=[]
        if thread.get('model') and thread['model']!='unknown':
            context.append('模型 · '+thread['model'])
        plan=thread.get('plan',[])
        if plan:
            completed=sum(p.get('status')=='completed' for p in plan)
            current=next((p.get('step','') for p in plan if p.get('status')=='inProgress'),'')
            context.append(f'计划 {completed}/{len(plan)}'+(' · '+current if current else ''))
        agents={key:state.get('status') for a in thread['activities'] for key,state in a.get('agents_states',{}).items()}
        if agents:
            context.append(f'子任务 {len(agents)} 项 · '+str(sum(s=='completed' for s in agents.values()))+' 项完成')
        if thread.get('review_mode'):
            context.append('审阅代码')
        if thread.get('environment_connected') is False and thread['status']=='inProgress':
            context.append('工作环境连接中断')
        if thread.get('subagents'):
            context.append('子任务 '+str(len(thread['subagents']))+' 项 · '+str(sum(s=='completed' for s in thread['subagents'].values()))+' 项完成')
        elements.append(Panel(Group(Text('\n'.join(context)),details),title=Text(thread['name'])))
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
