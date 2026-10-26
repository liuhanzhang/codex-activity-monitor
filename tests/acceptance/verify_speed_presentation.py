"""Opt-in native Preview checks using synthetic, read-only snapshot fixtures."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import time

from codex_activity_monitor.engine import Activity, Engine
from codex_activity_monitor.metrics import TextStream, count_text
from codex_activity_monitor.runtime import atomic_json


def fixture(phase):
    engine = Engine(asynchronous_counts=False)
    try:
        engine.backends['fixture'] = {'connected': phase != 'disconnected', 'home': '/synthetic',
            'implementation': {'modules': {'observer_permit': 'synthetic'}}}
        thread = engine._thread('fixture', 'chat')
        thread.status, thread.turn_id, thread.name, thread.cwd = 'inProgress', 'turn', 'Speed sampling', '/synthetic/project'
        key = ('fixture', 'input', 'tool_input', 0)
        stream = TextStream(key, 'tool_input', 'upstream', 'unknown', 100)
        stream.append('abcde' if phase == 'prefix-correction' else ' x' * 25, 101)
        engine.streams[key] = stream
        thread.activities['input'] = Activity('input', 'turn', 'tool_input', '', 100, stream_keys=[key])
        if phase in ('ready', 'prefix-correction', 'recovered', 'partial', 'partial-zero'):
            stream.apply_count(count_text(stream.text, stream.encoding_name)[0], stream.revision, 101, 0)
            stream.append('f' if phase == 'prefix-correction' else ' x' * 75, 101.25)
            stream.apply_count(count_text(stream.text, stream.encoding_name)[0], stream.revision, 101.25, 0)
        elif phase == 'limited':
            stream.count_state = 'budget'
        elif phase == 'failed':
            stream.fail_count()
        elif phase == 'gap':
            thread.quality.add('observation_gap')
            stream.interrupt_observation()
        if phase in ('recovered', 'partial', 'partial-zero'):
            thread.quality.add('observation_gap')
        if phase in ('partial', 'partial-zero'):
            bad_key = ('fixture', 'unknown', 'tool_input', 0)
            bad = TextStream(bad_key, 'tool_input', 'upstream', 'unknown', 100)
            bad.interrupt_observation()
            engine.streams[bad_key] = bad
            thread.activities['unknown'] = Activity('unknown', 'turn', 'tool_input', '', 100, stream_keys=[bad_key])
        if phase == 'prefix-correction':
            engine.counter.tick([stream], 102)
        value = engine.snapshot(103 if phase == 'partial-zero' else 102 if phase == 'prefix-correction' else 101.25, time.time())
        value['running'] = True
        return value
    finally:
        engine.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    app = args.app.resolve()
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    assert info['CFBundleIdentifier'].endswith('.preview'), 'Use the isolated Preview bundle only'
    args.output.mkdir(parents=True, exist_ok=False)
    results = {}
    for language in ('en', 'zh-CN'):
        directory = args.output / language
        directory.mkdir()
        snapshot, diagnostics = directory / 'snapshot.json', directory / 'gui.jsonl'
        atomic_json(snapshot, fixture('sampling'))
        with (directory / 'process.log').open('w') as log:
            gui = subprocess.Popen([str(app / 'Contents/MacOS/MonitorApp'), '--snapshot', str(snapshot),
                '--window', '--lang', language, '--ui-diagnostics', str(diagnostics),
                '--ui-diagnostics-seconds', '90'], stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            try:
                checks = {}
                for phase in ('sampling', 'prefix-correction', 'ready', 'recovered', 'partial', 'partial-zero', 'limited', 'failed', 'gap', 'disconnected'):
                    sampling = phase in ('sampling', 'prefix-correction')
                    numeric = phase in ('ready', 'recovered', 'partial', 'partial-zero')
                    expected = ('0 T/s' if phase == 'partial-zero' else '100 T/s') if numeric else '—'
                    started = time.time()
                    deadline = time.monotonic() + 8
                    observed = None
                    while time.monotonic() < deadline:
                        atomic_json(snapshot, fixture(phase))
                        time.sleep(.12)
                        if gui.poll() is not None:
                            raise RuntimeError('native_preview_exited')
                        try:
                            lines = diagnostics.read_text().splitlines(keepends=True)
                        except FileNotFoundError:
                            continue
                        for line in reversed(lines):
                            if not line.endswith('\n'):
                                continue  # Only an in-flight trailing write.
                            record = json.loads(line)
                            if record.get('at', 0) < started:
                                break
                            notice = record.get('observation_notice')
                            matches = (record.get('title') == expected and
                                (notice is None if sampling or numeric else notice == {
                                    'limited': 'NoticeSpeedResourceLimit', 'failed': 'NoticeSpeedCountFailed',
                                    'gap': 'NoticeObservationGap', 'disconnected': 'NoticeWaitingForConnection'}[phase]))
                            rows = record.get('chat_speed_observations', [])
                            if phase in ('recovered', 'partial', 'partial-zero', 'gap', 'disconnected'):
                                expected_notice = (None if phase == 'recovered' else 'NoticeSourceDisconnected'
                                                   if phase == 'disconnected' else 'NoticeObservationGap')
                                matches &= bool(rows and rows[0].get('notice') == expected_notice)
                            if matches:
                                observed = record
                                break
                        if observed:
                            break
                    assert observed is not None, (language, phase, 'presentation_not_applied')
                    if sampling:
                        tooltip = 'Connected' if language == 'en' else '已连接'
                        assert tooltip in observed['status_tooltip']
                        assert observed['mode'] == 'unavailable'
                        assert observed['connection_label'] == ('Connected' if language == 'en' else '已连接')
                    checks[phase] = {k: observed.get(k) for k in (
                        'title', 'mode', 'status_tooltip', 'connection_label', 'observation_notice',
                        'chat_speed_observations', 'build_id')}
                    checks[phase]['button_width'] = observed['button_frame'][2]
                for phase in ('sampling', 'prefix-correction'):
                    assert checks[phase]['button_width'] <= checks['ready']['button_width'], (language, phase, 'sampling_widens_menu')
                results[language] = checks
            finally:
                gui.terminate()
                gui.wait(timeout=5)
    report = {'scope': 'native Preview applies synthetic read-only fixtures; no production runtime or model calls',
        'passed': True, 'languages': results,
        'limits': ['Applied AppKit/SwiftUI state and actual status-button tooltip, not a physical screen timing measurement.',
                   'Each independent fixture tests display semantics; these are not natural completion or recovery samples.']}
    (args.output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'passed': True, 'languages': list(results), 'states_per_language': len(checks)}))


if __name__ == '__main__':
    main()
