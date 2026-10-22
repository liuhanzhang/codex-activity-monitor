"""Isolated feasibility prototype; never connects to the daily collector.

Uses the shipped HistoryProvider and Engine with synthetic protocol events and
small fixture JSONL files. The history-to-engine adapter below is experimental,
not an implementation of the production reconciliation contract.
"""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import queue
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from codex_progress_monitor.engine import Engine
from codex_progress_monitor.events import Event
from codex_progress_monitor.history import HistoryProvider
from codex_progress_monitor.timeline import TimelineBook, status
from codex_progress_monitor.timeline_query import TimelineQuery


def row(kind, payload, at):
    return {'type': kind, 'payload': payload,
            'timestamp': datetime.fromtimestamp(at, timezone.utc).isoformat()}


class BackgroundHistory:
    """One reader owns its index; only detached metadata reaches the owner."""

    def __init__(self, candidates, delay=0):
        self.candidates = list(dict.fromkeys(candidates))
        self.delay = delay
        self.provider = HistoryProvider()
        self.ready = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.reads = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            batch = {'commands': [], 'turns': [], 'errors': []}
            if self.stop.wait(self.delay):
                return
            for scope, thread in self.candidates:
                try:
                    index = self.provider.index(scope, thread)
                    self.reads += 1
                    if index is None:
                        continue
                    for phase in index.phases.values():
                        # A raw parameter item or outer call return is not a
                        # canonical command completion, even if call IDs match.
                        if (phase['namespace'] != 'canonical' or
                                phase['item_type'] != 'commandExecution' or
                                phase['status'] not in ('completed', 'failed', 'interrupted')):
                            continue
                        ref = index.refs[phase['id']]
                        if ref['source'] != 'canonical':
                            continue
                        item = index.read_ref(ref)['item']
                        batch['commands'].append({**deepcopy(phase),
                                                  'exit_code': item.get('exit_code')})
                    batch['turns'].extend({**deepcopy(t), 'scope_id': scope}
                                          for t in index.turns.values())
                except (OSError, ValueError, KeyError) as error:
                    batch['errors'].append(type(error).__name__)
            try:
                self.ready.put_nowait(batch)
            except queue.Full:
                # Each batch is a detached current summary, not a delta log.
                try:
                    self.ready.get_nowait()
                except queue.Empty:
                    pass
                self.ready.put_nowait(batch)
            self.stop.wait(.025)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        assert not self.thread.is_alive(), 'background reader did not stop'


class Scenario:
    def __init__(self, directory, *, gap=True, age=0, duplicates=1, delay=0):
        self.home = directory / 'home'
        self.path = self.home / 'sessions/2026/10/22/rollout-probe-thread-A.jsonl'
        self.path.parent.mkdir(parents=True)
        self.wall = time.time() - 2 - age
        self.mono = time.monotonic() - 2 - age
        self.book = TimelineBook()
        self.engine = Engine(asynchronous_counts=False, timeline=self.book)
        self.facts = {}
        self.queries = 0
        self.owner_ticks = 0
        self.tick_gaps = []
        self.previous_tick = None
        self.errors = []
        self.path.write_text(''.join(json.dumps(r) + '\n' for r in [
            row('session_meta', {'id': 'thread-A', 'history_mode': 'paginated'}, self.wall),
            row('event_msg', {'type': 'task_started', 'turn_id': 'turn-old',
                              'started_at': self.wall}, self.wall)]))
        self.send({'type': 'bridge/started', 'home': str(self.home)}, 0, 'lifecycle')
        self.turn('turn-old', 0)
        self.send({'method': 'item/started', 'params': {
            'threadId': 'thread-A', 'turnId': 'turn-old', 'item': {
                'id': 'exec-fixture-A', 'type': 'commandExecution',
                'source': 'unifiedExecStartup', 'processId': 'fixture-process',
                'status': 'inProgress', 'command': 'fixture only; never executed'}}}, .1)
        if gap:
            self.send({'type': 'bridge/gap'}, .5, 'lifecycle')
        self.send({'method': 'turn/completed', 'params': {
            'threadId': 'thread-A', 'turn': {'id': 'turn-old', 'status': 'completed'}}}, .6)
        self.worker = BackgroundHistory([(str(self.home), 'thread-A')] * duplicates, delay)

    def send(self, data, offset, direction='protocol_out'):
        self.engine.ingest(Event('probe', direction, data, self.mono + offset,
                                 self.wall + offset))

    def turn(self, turn, offset):
        self.send({'method': 'turn/started', 'params': {
            'threadId': 'thread-A', 'turn': {'id': turn, 'status': 'inProgress'}}}, offset)

    @property
    def command(self):
        return self.engine.threads[('probe', 'thread-A')].activities['exec-fixture-A']

    def completion(self, *, item='exec-fixture-A', turn='turn-old'):
        return row('event_msg', {
            'type': 'item_completed', 'thread_id': 'thread-A', 'turn_id': turn,
            'started_at_ms': round((self.wall + .1) * 1000),
            'completed_at_ms': round((self.wall + .453) * 1000),
            'item': {'type': 'CommandExecution', 'id': item, 'status': 'completed',
                     'process_id': 'fixture-process', 'source': 'unified_exec_startup',
                     'exit_code': 0, 'aggregated_output': 'fixture output',
                     'duration': {'secs': 0, 'nanos': 349819625}}}, self.wall + .453)

    def append(self, record):
        with self.path.open('a') as stream:
            stream.write(json.dumps(record) + '\n')

    def apply_ready(self):
        try:
            batch = self.worker.ready.get_nowait()
        except queue.Empty:
            return
        self.errors.extend(batch['errors'])
        # Prototype adapter: direct state correction, not invented live events.
        for fact in batch['commands']:
            for thread in self.engine.threads.values():
                if (self.engine.backends[thread.backend].get('home') != fact['scope_id'] or
                        thread.id != fact['thread_id']):
                    continue
                activity = thread.activities.get(fact['item_id'])
                if (not activity or activity.turn_id != fact['turn_id'] or
                        activity.item_type != fact['item_type']):
                    continue
                activity.status = fact['status']
                activity.exit_code = fact['exit_code']
                self.facts[(thread.id, activity.turn_id, activity.id)] = fact
        for fact in batch['turns']:
            thread = self.engine.threads.get(('probe', fact['thread_id']))
            if (thread and str(self.home) == fact['scope_id'] and
                    thread.turn_id == fact['turn_id'] and
                    fact['status'] in ('completed', 'failed', 'interrupted')):
                thread.status = fact['status']

    def snapshot(self):
        snapshot = self.engine.snapshot(time.monotonic(), time.time())
        # Keep history wall times separate from Engine monotonic observation
        # fields. This read projection demonstrates the minimal field bridge.
        for thread in snapshot['threads']:
            for activity in thread['activities']:
                fact = self.facts.get((thread['id'], activity['turn_id'], activity['id']))
                if fact:
                    activity['source_start_ms'] = fact['start_ms']
                    activity['source_end_ms'] = fact['end_ms']
                    activity['duration_seconds'] = (fact['end_ms'] - fact['start_ms']) / 1000
        return snapshot

    def pump(self, predicate, timeout=1.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            now = time.monotonic()
            if self.previous_tick is not None:
                self.tick_gaps.append((now - self.previous_tick) * 1000)
            self.previous_tick = now
            self.owner_ticks += 1
            self.engine.ingest(Event('probe', 'lifecycle', {'type': 'bridge/heartbeat'}, now, time.time()))
            self.apply_ready()
            snapshot = self.snapshot()
            if predicate(snapshot):
                return snapshot
            time.sleep(.005)
        raise AssertionError('prototype condition did not become true before timeout')

    def settle(self, seconds=.10):
        end = time.monotonic() + seconds
        return self.pump(lambda _: time.monotonic() >= end)

    def close(self):
        self.worker.close()
        self.engine.close()


def run_case(name, directory):
    slow = name == 'slow_reader'
    scenario = Scenario(directory, age=36000 if name == 'old_completion' else 0,
                        duplicates=128 if name == 'duplicate_candidates' else 1,
                        delay=.25 if slow else 0)
    try:
        assert scenario.command.active or scenario.command.unresolved, 'missing completion did not retain unresolved work'
        scenario.worker.thread.start()
        if name == 'raw_return_only':
            scenario.append(row('response_item', {'type': 'custom_tool_call', 'id': 'outer-input',
                'name': 'exec', 'call_id': 'exec-fixture-A', 'input': 'fixture'}, scenario.wall + .2))
            scenario.append(row('response_item', {'type': 'custom_tool_call_output',
                'call_id': 'exec-fixture-A', 'output': 'Process running with session ID 7'}, scenario.wall + .4))
            scenario.settle()
            assert (scenario.command.active or scenario.command.unresolved) and not scenario.facts
            return {'passed': True, 'canonical_command_remained_unresolved': True,
                    'total_rate': scenario.snapshot()['overview']['generation_rate']}
        if name == 'wrong_identity':
            scenario.append(scenario.completion(item='exec-other'))
            scenario.append(scenario.completion(turn='turn-other'))
            scenario.settle()
            assert (scenario.command.active or scenario.command.unresolved) and not scenario.facts
            return {'passed': True, 'different_item_and_turn_ignored': True}
        if name == 'new_turn':
            scenario.turn('turn-new', .8)
            scenario.send({'type': 'response.output_item.added', 'item': {
                'id': 'new-message', 'type': 'message',
                'metadata': {'thread_id': 'thread-A', 'turn_id': 'turn-new'}}}, .9, 'upstream')
            scenario.send({'type': 'response.output_text.delta', 'item_id': 'new-message',
                           'delta': 'New generation fixture.'}, .95, 'upstream')
            scenario.engine.tick(scenario.mono + 1)
            assert scenario.engine.streams[('probe', 'new-message', 'text', 0)].tokens > 0
        if name == 'realtime_first':
            scenario.send({'method': 'item/completed', 'params': {
                'threadId': 'thread-A', 'turnId': 'turn-old', 'item': {
                    'id': 'exec-fixture-A', 'type': 'commandExecution',
                    'status': 'completed', 'exitCode': 0}}}, .7)
            scenario.settle()
            assert not scenario.command.active and not scenario.facts
        before = scenario.snapshot()
        encoded = json.dumps(scenario.completion()) + '\n'
        if name == 'partial_tail':
            middle = len(encoded) // 2
            with scenario.path.open('a') as stream:
                stream.write(encoded[:middle])
            scenario.settle()
            assert (scenario.command.active or scenario.command.unresolved) and not scenario.facts
            with scenario.path.open('a') as stream:
                stream.write(encoded[middle:])
        else:
            scenario.settle(.025)
            scenario.append(scenario.completion())
        started = time.monotonic()
        after = scenario.pump(lambda _: bool(scenario.facts))
        delay_ms = (time.monotonic() - started) * 1000
        assert not scenario.command.active and scenario.command.exit_code == 0
        assert not scenario.errors
        assert scenario.command.started_at < 1e9 and scenario.command.ended_at != scenario.completion()['payload']['completed_at_ms']
        command = next(a for a in after['threads'][0]['activities'] if a['id'] == 'exec-fixture-A')
        assert abs(command['duration_seconds'] - .353) < .002
        count = len(scenario.engine.threads[('probe', 'thread-A')].activities)
        scenario.settle(.055)
        assert len(scenario.engine.threads[('probe', 'thread-A')].activities) == count
        if name == 'new_turn':
            thread = after['threads'][0]
            assert thread['turn_id'] == 'turn-new' and thread['status'] == 'inProgress'
            assert after['overview']['known_stream_count'] == 1
            assert after['overview']['generation_rate'] is not None
        else:
            assert 'observation_gap' in after['threads'][0]['quality']
            assert after['overview']['coverage'] == 'complete'
            assert after['overview']['generation_rate'] == 0
        result = {'passed': True, 'background_correction_ms': round(delay_ms, 2),
                  'timeline_queries_before_correction': scenario.queries,
                  'canonical_duration_s': command['duration_seconds'],
                  'before_active_count': before['threads'][0]['active_count'],
                  'after_active_count': after['threads'][0]['active_count'],
                  'total_coverage': after['overview']['coverage'],
                  'current_thread_gap_flag': 'observation_gap' in after['threads'][0]['quality'],
                  'historical_command_gap_retained': any(
                      p.get('item_id') == 'exec-fixture-A' and 'observation_gap' in p['quality']
                      for p in scenario.book.phases.values())}
        if slow:
            assert scenario.owner_ticks >= 10
            result.update(owner_ticks=scenario.owner_ticks,
                          owner_max_tick_gap_ms=round(max(scenario.tick_gaps), 2))
        if name == 'duplicate_candidates':
            assert len(scenario.worker.candidates) == 1
            result.update(candidate_references=128, unique_candidates=1)
        if name == 'old_completion':
            old_progress = scenario.engine.threads[('probe', 'thread-A')].last_progress_at
            assert time.monotonic() - old_progress > 35000
            scenario.engine.tick(time.monotonic())
            assert ('probe', 'thread-A') not in scenario.engine.threads
            result['old_thread_expired_without_refreshing_activity_time'] = True
        else:
            # Query only after proving correction without any panel request.
            scenario.queries += 1
            bundle = TimelineQuery(scenario.book).bundle(str(scenario.home), 'thread-A', 'turn-old')
            phases = bundle.get('phases', [])
            phase = next(p for p in phases if p.get('item_id') == 'exec-fixture-A')
            assert phase['status'] == 'completed'
            assert 'observation_gap' in phase['quality']
            result['subsequent_timeline_status'] = phase['status']
        return result
    finally:
        scenario.close()


def check_original_source():
    evidence = ROOT / '.local/diagnostics/stale-command-20261021/evidence.json'
    if not evidence.is_file():
        return {'available': False, 'reason': 'prior evidence file missing'}
    source = json.loads(evidence.read_text())['source']
    try:
        with Path(source['path']).open('rb') as stream:
            for number, line in enumerate(stream, 1):
                if number == source['line']:
                    payload = json.loads(line)['payload']
                    same = hashlib.sha256(line).hexdigest() == source['record_sha256']
                    return {'available': True, 'same_record_hash': same,
                            'same_item': payload['item']['id'] == source['item_id'],
                            'status': payload['item']['status'], 'exit_code': payload['item']['exit_code']}
    except (OSError, ValueError, KeyError) as error:
        return {'available': False, 'reason': type(error).__name__}
    return {'available': False, 'reason': 'source line missing'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cases = ('delayed_completion', 'partial_tail', 'raw_return_only', 'wrong_identity',
             'new_turn', 'realtime_first', 'duplicate_candidates', 'slow_reader', 'old_completion')
    results = {}
    for name in cases:
        try:
            results[name] = run_case(name, output / name)
        except Exception as error:
            results[name] = {'passed': False, 'error': type(error).__name__, 'detail': str(error)}
    report = {'scope': 'isolated prototype with synthetic events; not production collector or native UI acceptance',
              'poll_interval_s': .025, 'original_source': check_original_source(), 'cases': results,
              'source_sha256': {name: hashlib.sha256((ROOT / 'src/codex_progress_monitor' / (name + '.py')).read_bytes()).hexdigest()
                                for name in ('engine', 'history', 'overview', 'timeline', 'timeline_query')},
              'prototype_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    passed = sum(r['passed'] for r in results.values())
    report.update(passed=passed, total=len(cases))
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'result': str(output / 'result.json'), 'passed': passed, 'total': len(cases),
                      'failures': {k: v for k, v in results.items() if not v['passed']}}, ensure_ascii=False))
    return 0 if passed == len(cases) else 1


if __name__ == '__main__':
    raise SystemExit(main())
