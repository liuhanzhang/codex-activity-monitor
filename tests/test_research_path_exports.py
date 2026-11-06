import contextlib
from datetime import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT/'analysis/file-edit-progress/extract_progress_sources.py'
EXPORT = ROOT/'experiments/product-preflight/export_results.py'


def write_asar(path, files):
    entries = {}
    payload = bytearray()
    for name, content in files.items():
        entries[name] = {'offset': str(len(payload)), 'size': len(content)}
        payload.extend(content)
    header = json.dumps({'files': {'assets': {'files': entries}}}).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack('<4I', 0, len(header)+8, 0, len(header)) + header + payload)


class AsarPathTests(unittest.TestCase):
    def test_extracts_selected_content_and_records_portable_source_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            old, current = home/'inputs/old.asar', home/'inputs/current.asar'
            files = {
                'match.js': b'const change = "patchUpdated";',
                'agent-activity-item-demo.js': b'explicit module',
                'en-1234.js': b'patchUpdated',
                'unrelated.js': b'other',
            }
            write_asar(old, files)
            write_asar(current, files)
            old_alias = home/'old-alias.asar'
            old_alias.symlink_to(old)
            output = home/'output'
            env = dict(os.environ, HOME=str(home))
            completed = subprocess.run(
                [sys.executable, str(EXTRACT), '--older-asar', str(old_alias),
                 '--current-asar', str(current), '--output', str(output)],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads((output/'source-manifest.json').read_text())
            self.assertEqual([entry['source'] for entry in manifest],
                             ['~/old-alias.asar', '~/inputs/current.asar'])
            for entry in manifest:
                extracted = {item['path']: item for item in entry['files']}
                self.assertEqual(set(extracted), {'assets/match.js', 'assets/agent-activity-item-demo.js'})
                self.assertEqual((output/entry['version']/'assets/match.js').read_bytes(), files['match.js'])
                self.assertEqual(extracted['assets/match.js']['sha256'], hashlib.sha256(files['match.js']).hexdigest())
                self.assertEqual(extracted['assets/match.js']['matches'], {'patchUpdated': 1})

    def test_missing_input_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            old, missing, output = home/'old.asar', home/'missing.asar', home/'output'
            write_asar(old, {'match.js': b'patchUpdated'})
            completed = subprocess.run(
                [sys.executable, str(EXTRACT), '--older-asar', str(old),
                 '--current-asar', str(missing), '--output', str(output)],
                env=dict(os.environ, HOME=str(home)), capture_output=True, text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn('ASAR file not found: ~/missing.asar', completed.stderr)
            self.assertFalse(output.exists())


class EvidenceExportTests(unittest.TestCase):
    def test_export_records_home_relative_evidence_without_changing_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()/'home'
            root = home/'repo'
            evidence_files = []
            for index, run_name in enumerate(('live-20261011', 'tools-20261011')):
                run = root/'.local/product-preflight'/run_name
                run.mkdir(parents=True)
                capture = run/'capture.jsonl'
                capture.write_text('synthetic capture')
                actual = home/f'source-files-{index}'
                (actual/'sessions').mkdir(parents=True)
                alias = home/f'isolated-{index}'
                alias.symlink_to(actual, target_is_directory=True)
                sessions = alias/'sessions'
                transcript = sessions/'rollout-thread.jsonl'
                transcript.write_text(''.join(json.dumps({
                    'type': 'response_item',
                    'payload': {'type': 'message', 'role': 'assistant', 'id': f'item-{i}',
                                'content': [{'text': f'text-{i}'}]},
                })+'\n' for i in range(6)))
                report = {'threads': [{'thread_id': 'thread'}], 'isolated_home': str(sessions.parent),
                          'model': 'fixture', 'backend_transport': 'fixture', 'interruption_status': 'interrupted',
                          'config_unchanged': True, 'forwarder': {'dropped_packets': 0}}
                (run/'live-report.json').write_text(json.dumps(report))
                evidence_files.append((capture, transcript))
            analysis = root/'analysis/product-preflight'
            analysis.mkdir(parents=True)
            (analysis/'desktop.json').write_text('{}')
            metadata = root/'.local/product-preflight/desktop-20261011/desktop-capture.meta.json'
            metadata.parent.mkdir(parents=True)
            metadata.write_text('{"fixture": true}')

            class FakeEncoding:
                name = 'fixture-encoding'

                def encode_ordinary(self, text):
                    return list(text)

            class FakeTelemetry:
                def __init__(self):
                    self.streams = {(f'item-{i}', 'text', 0): {'text': f'text-{i}'} for i in range(6)}

                def ingest(self, row):
                    pass

                def summarize(self):
                    streams = [{'item_id': f'item-{i}', 'kind': 'text', 'part': 0,
                                'matches_completed_item': True, 'delayed_mapping_count': 0,
                                'last_at': 1, 'item_finished_at': 2} for i in range(6)]
                    return {'streams': streams, 'inter_thread_delta_switches': 1, 'unassociated_delta_count': 0,
                            'tool_activity': [{'event': 'item/completed', 'exit_code': 0},
                                              {'event': 'item/completed', 'exit_code': 7}]}

            stamp = '2026-10-11T00:00:00+00:00'
            tokenizer = types.ModuleType('tokenizer_probe')
            tokenizer.encoding = FakeEncoding
            telemetry = types.ModuleType('telemetry')
            telemetry.Telemetry = FakeTelemetry
            telemetry.read_capture = lambda path: [{'source_timestamp': stamp,
                                                    'observed_timestamp': datetime.fromisoformat(stamp).timestamp()+0.25}]
            run_live = types.ModuleType('run_live')
            run_live.ROOT = root
            spec = importlib.util.spec_from_file_location('research_export_fixture', EXPORT)
            module = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, {'tokenizer_probe': tokenizer, 'telemetry': telemetry, 'run_live': run_live}):
                spec.loader.exec_module(module)
            with patch.dict(os.environ, {'HOME': str(home)}), contextlib.redirect_stdout(io.StringIO()):
                module.main()
            result = json.loads((analysis/'live.json').read_text())
            for run, expected in zip(result['runs'], evidence_files):
                for record, original in zip(run['evidence'], expected):
                    self.assertEqual(record['path'], '~/'+str(original.relative_to(home)))
                    self.assertEqual(record['sha256'], hashlib.sha256(original.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
