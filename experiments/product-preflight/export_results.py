#!/usr/bin/env python3
"""Export hashes, counts and coverage from local evidence; omit source text."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics
from zoneinfo import ZoneInfo

from tokenizer_probe import encoding
from telemetry import Telemetry, read_capture
from run_live import ROOT


def main():
    enc = encoding()
    exported = []
    for name in ('live-20261011', 'tools-20261011'):
        run = ROOT/'.local/product-preflight'/name
        local = json.loads((run/'live-report.json').read_text())
        collector = Telemetry()
        lag = []
        for row in read_capture(run/'capture.jsonl'):
            collector.ingest(row)
            if row.get('source_timestamp'):
                source = datetime.fromisoformat(row['source_timestamp'].replace('Z', '+00:00')).timestamp()
                lag.append((row['observed_timestamp']-source)*1000)
        summary = collector.summarize()
        persisted = {}
        evidence = []
        for thread in local['threads']:
            paths = list((Path(local['isolated_home'])/'sessions').rglob('*'+thread['thread_id']+'.jsonl'))
            assert len(paths)==1
            path = paths[0]
            evidence.append({'path':str(path), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            for line in path.read_text().splitlines():
                row = json.loads(line)
                if row.get('type')!='response_item':
                    continue
                item = row['payload']
                if item['type']=='custom_tool_call':
                    persisted[item['id']] = item['input']
                elif item['type']=='message' and item.get('role')=='assistant':
                    persisted[item['id']] = ''.join(x.get('text','') for x in item.get('content',[]))
        for stream in summary['streams']:
            key = (stream['item_id'], stream['kind'], stream['part'])
            text = collector.streams[key]['text']
            stream['local_tokens'] = len(enc.encode_ordinary(text))
            stream['encoding'] = enc.name
            stream['token_count_status'] = 'estimate_for_unmapped_model'
            stream['matches_persisted_item'] = persisted.get(stream['item_id'])==text
            assert stream['matches_completed_item'] and stream['matches_persisted_item']
            assert stream['delayed_mapping_count']==0
            assert stream['last_at'] <= stream['item_finished_at']
        assert summary['unassociated_delta_count']==0
        assert local['forwarder']['dropped_packets']==0
        if name.startswith('live'):
            assert summary['inter_thread_delta_switches'] > 0
            assert len(summary['streams'])==6
            assert local['interruption_status']=='interrupted'
        else:
            finished = [x for x in summary['tool_activity'] if x['event']=='item/completed']
            assert sorted(x['exit_code'] for x in finished)==[0,7]
        exported.append({'run':name, 'model':local['model'], 'transport':local['backend_transport'],
                         'scope':'owned stdio backend; not Desktop-submitted turns',
                         'tool_execution_verified':name.startswith('tools'),
                         'tool_execution_gap':'nested parent sandbox denied command launch' if name.startswith('live') else None,
                         'interruption_status':local.get('interruption_status'),
                         'config_unchanged':local['config_unchanged'],
                         'forwarder':local['forwarder'], 'telemetry':summary,
                         'source_to_observed_lag_ms':{'p50':statistics.median(lag),
                                                     'p95':sorted(lag)[int(len(lag)*0.95)],
                                                     'max':max(lag)},
                         'evidence':[{'path':str(run/'capture.jsonl'),
                                     'sha256':hashlib.sha256((run/'capture.jsonl').read_bytes()).hexdigest()},
                                     *evidence]})
    target = ROOT/'analysis/product-preflight/live.json'
    target.write_text(json.dumps({'runs':exported},indent=2,ensure_ascii=False)+'\n')
    desktop = ROOT/'analysis/product-preflight/desktop.json'
    report = json.loads(desktop.read_text())
    meta = ROOT/'.local/product-preflight/desktop-20261011/desktop-capture.meta.json'
    report['forwarder']=json.loads(meta.read_text())
    desktop.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'exported_runs':len(exported),'streams_checked':sum(len(r['telemetry']['streams']) for r in exported),
                      'all_completed_and_persisted_matches':True,'report':str(target)}))


if __name__=='__main__':
    main()
