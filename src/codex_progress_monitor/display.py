"""A localized terminal view of metrics snapshots; generated text stays private."""

import json
import os
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

from .i18n import DEFAULT_LANGUAGE, available_languages, message, phase_message, render_message, tr
from .progress_state import activity_message
from .runtime import read_snapshot, save_ui_language, ui_language

REFRESH_SECONDS = 1.0


def seconds(value, language=DEFAULT_LANGUAGE):
    if value is None:
        return '—'
    if value < 1:
        return ''
    total = int(value)
    if total < 60:
        return tr('time.seconds', language, seconds=total)
    if total < 3600:
        return tr('time.minutes', language, minutes=total//60, seconds=total%60)
    return tr('time.hours', language, hours=total//3600, minutes=total//60%60, seconds=total%60)


def _activity_message(activity):
    if isinstance(activity.get('display_message'), dict):
        return activity['display_message']
    legacy = dict(activity)
    if not legacy.get('item_type') and legacy.get('command'):
        legacy['item_type'] = 'commandExecution'
    return activity_message(legacy, ended=not legacy.get('active'))


def render(snapshot, selected=0, detail=False, thread_filter=None, language=DEFAULT_LANGUAGE, notice=None):
    def t(key, **params):
        return tr(key, language, **params)
    age = time.time()-(snapshot.get('updated_at') or 0)
    stale = age > 3 or not snapshot.get('running', True)
    if stale:
        snapshot = deepcopy(snapshot)
        for thread in snapshot.get('threads', []):
            thread['connected'] = False
            thread['token_rates'] = {'text': None, 'tool_input': None}
            thread['generation_rate'] = None
            thread['quality'] = sorted(set(thread.get('quality', [])) | {'stale_snapshot'})
            if thread.get('last_progress_seconds') is not None:
                thread['last_progress_seconds'] = thread['last_progress_seconds']+max(0, age) if snapshot.get('updated_at') else None
            if thread['status'] == 'inProgress' or thread.get('active_count'):
                disconnected = message('activity.disconnected')
                thread.update(status='unknown', phase='unknown', phase_label=render_message(disconnected),
                              display_label=render_message(disconnected), display_message=disconnected,
                              phase_seconds=None, display_seconds=None, display_active_count=0,
                              active_count=0, has_active_work=False)
            for activity in thread.get('activities', []):
                if activity['active']:
                    activity.update(active=False, status='unknown')
                    activity.pop('display_message', None)
    threads = [t for t in snapshot.get('threads', []) if (t['status'] != 'idle' or t.get('active_count'))
               and t.get('visible', True) and (t.get('ephemeral') is not True or thread_filter)]
    if thread_filter:
        threads = [t for t in threads if thread_filter in t['id'] or thread_filter in t['name']]
    table = Table(expand=True, box=None, padding=(0, 1))
    for key in ('table.chat', 'table.activity', 'table.duration', 'table.speed'):
        table.add_column(t(key))
    selected = min(selected, max(0, len(threads)-1))
    for index, thread in enumerate(threads):
        active = [a for a in thread['activities'] if a['active']]
        primary = next((a for a in active if a['id'] == thread.get('display_primary_id', thread.get('primary_activity_id'))), None)
        descriptor = thread.get('display_message')
        if descriptor:
            phase = render_message(descriptor, language)
        elif primary and thread.get('connected', True) and thread.get('phase') != 'unknown':
            phase = render_message(_activity_message(primary), language)
        else:
            fallback = phase_message(thread.get('phase'))
            if not thread.get('connected', True) and thread.get('phase') == 'unknown':
                fallback = message('activity.disconnected')
            elif thread.get('phase') == 'waiting' and thread.get('activities'):
                fallback = message('activity.processing')
            phase = render_message(fallback, language)
        if primary and not primary.get('turn_bound', True) and thread['status'] == 'completed':
            phase += t('view.turn_finished')
        parallel = thread.get('display_active_count', thread['active_count'])
        if parallel > 1:
            phase += t('view.parallel', count=parallel-1)
        value = thread.get('generation_rate')
        rate = '—' if value is None else ('≈' if thread.get('generation_estimated') else '')+f'{value:.1f} token/s'
        cwd = Path(thread['cwd']).name if thread.get('cwd') else ''
        title = thread['name'] if thread['name'] != thread['id'][:8] else t('view.unnamed')
        name = ('› ' if index == selected else '  ')+title+('\n  '+cwd if cwd else '')
        table.add_row(Text(name), Text(phase), seconds(thread.get('display_seconds', thread.get('phase_seconds')), language), rate,
                      style='bold' if index == selected else None)
    state = t('activity.disconnected' if stale else 'view.local')
    header = Text(t('view.header', state=state, count=len(threads)), style='bold')
    footer = Text(t('view.footer', language_hint=t('view.language_hint') if len(available_languages()) > 1 else ''), style='dim')
    elements = [header, table if threads else Text(t('view.empty')), footer]
    if notice:
        elements.append(Text(t(notice), style='dim'))
    if detail and threads:
        thread = threads[selected]
        details = Table(box=None, expand=True)
        for key in ('table.detail_activity', 'table.status', 'table.duration'):
            details.add_column(t(key))
        for activity in thread['activities']:
            if not activity.get('display_visible', True) or activity['kind'] in ('approval', 'input'):
                continue
            statuses = {'completed': 'completed', 'failed': 'failed', 'declined': 'declined',
                        'approved': 'approved', 'denied': 'denied', 'timedOut': 'timedOut',
                        'aborted': 'stopped', 'interrupted': 'stopped', 'unknown': 'unknown'}
            state = t('status.active' if activity['active'] else 'status.'+statuses.get(activity['status'], 'ended'))
            if activity.get('exit_code') not in (None, 0):
                state = t('status.command_failed')
            details.add_row(Text(render_message(_activity_message(activity), language)), state,
                            seconds(activity['duration_seconds'], language) if activity.get('start_known') else '—')
        context = []
        if thread.get('model') and thread['model'] != 'unknown':
            context.append(t('context.model', name=thread['model']))
        plan = thread.get('plan', [])
        if plan:
            completed = sum(p.get('status') == 'completed' for p in plan)
            current = next((p.get('step', '') for p in plan if p.get('status') == 'inProgress'), '')
            context.append(t('context.plan', completed=completed, total=len(plan), target=current))
        agents = {key: state.get('status') for a in thread['activities'] for key, state in a.get('agents_states', {}).items()}
        if agents:
            context.append(t('context.agents', count=len(agents), completed=sum(s == 'completed' for s in agents.values())))
        if thread.get('review_mode'):
            context.append(t('context.review'))
        if thread.get('environment_connected') is False and thread['status'] == 'inProgress':
            context.append(t('context.disconnected'))
        if thread.get('subagents'):
            context.append(t('context.agents', count=len(thread['subagents']), completed=sum(s == 'completed' for s in thread['subagents'].values())))
        elements.append(Panel(Group(Text('\n'.join(context)), details), title=Text(thread['name'])))
    return Group(*elements), len(threads)


def input_keys(buffer):
    keys = []
    while buffer:
        if buffer.startswith('\x1b'):
            if len(buffer) < 3 and '\x1b['.startswith(buffer):
                break
            if buffer.startswith(('\x1b[A', '\x1b[B')):
                keys.append('k' if buffer[2] == 'A' else 'j')
                buffer = buffer[3:]
                continue
        keys.append(buffer[0])
        buffer = buffer[1:]
    return keys, buffer


def watch(runtime, once=False, json_output=False, thread_filter=None, language=None):
    language = ui_language(runtime, language)
    console = Console()
    if once:
        snapshot = read_snapshot(runtime)
        if json_output:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        else:
            console.print(render(snapshot, detail=bool(thread_filter), thread_filter=thread_filter, language=language)[0])
        return
    if json_output or not sys.stdout.isatty():
        try:
            while True:
                print(json.dumps(read_snapshot(runtime), ensure_ascii=False), flush=True)
                time.sleep(REFRESH_SECONDS)
        except KeyboardInterrupt:
            return
    import termios
    import tty
    old = None
    if sys.stdin.isatty():
        old = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin)
    selected, detail, buffer, notice = 0, bool(thread_filter), '', None
    next_refresh, dirty, count = 0.0, True, 0
    try:
        with Live(console=console, refresh_per_second=1, auto_refresh=False, screen=True) as live:
            while True:
                now = time.monotonic()
                due = now >= next_refresh
                if due or dirty:
                    visual, count = render(read_snapshot(runtime), selected, detail, thread_filter, language, notice)
                    live.update(visual, refresh=True)
                    dirty = False
                    if due:
                        next_refresh = time.monotonic()+REFRESH_SECONDS
                timeout = max(0, next_refresh-time.monotonic())
                if not old:
                    time.sleep(timeout)
                    continue
                if not select.select([sys.stdin], [], [], timeout)[0]:
                    continue
                data = os.read(sys.stdin.fileno(), 64)
                if not data:
                    break
                keys, buffer = input_keys(buffer+data.decode(errors='ignore'))
                for key in keys:
                    if key == 'q':
                        return
                    if key == 'j':
                        selected, dirty = min(selected+1, max(0, count-1)), True
                    elif key == 'k':
                        selected, dirty = max(0, selected-1), True
                    elif key in ('\n', ' '):
                        detail, dirty = not detail, True
                    elif key == 'l' and len(available_languages()) > 1:
                        languages = available_languages()
                        language, dirty = languages[(languages.index(language)+1)%len(languages)], True
                        try:
                            save_ui_language(runtime, language)
                            notice = None
                        except OSError:
                            notice = 'view.settings_failed'
    except KeyboardInterrupt:
        pass
    finally:
        if old:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
