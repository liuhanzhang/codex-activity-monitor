"""Acceptance over existing captures; does not start models or print their text."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from codex_activity_monitor.engine import Engine
from codex_activity_monitor.events import Event,filter_event,DELTA_KINDS
from codex_activity_monitor.metrics import count_text


def verify(path):
    engine=Engine(False)
    expected={}
    phases=set()
    labels=set()
    peak_generation_rate=0
    input_bytes=path.stat().st_size
    capture_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    at=0
    began=time.perf_counter()
    for line in path.open():
        row=json.loads(line)
        row.setdefault('direction','protocol_out')
        value=row['event']
        if row['direction']=='upstream' and value.get('type')=='response.output_item.done':
            item=value['item'];item_id=item['id']
            parts=[]
            if item['type']=='message':
                parts=[('text',i,p.get('text','')) for i,p in enumerate(item.get('content',[])) if p.get('type')=='output_text']
            elif item['type'] in ('custom_tool_call','function_call'):
                parts=[('tool_input',0,item.get('input',item.get('arguments','')))]
            elif item['type']=='reasoning':
                parts=[('summary',i,p.get('text','')) for i,p in enumerate(item.get('summary',[]))]
            for kind,part,text in parts:
                if text:
                    expected[(item_id,kind,part)]={'chars':len(text),'tokens':count_text(text,'o200k_base')[0],'sha256':hashlib.sha256(text.encode()).hexdigest()}
        safe=filter_event(row['direction'],value)
        if safe is None:continue
        row['event']=safe;event=Event.from_row(row)
        engine.ingest(event)
        if row['direction']=='upstream' and value.get('type') in DELTA_KINDS:
            engine.ingest(event)
        at=max(at,event.at)
        engine.tick(at)
        for thread in engine.snapshot(at)['threads']:
            phases.add(thread['phase'])
            labels.add(thread['display_label'].split(' · ')[0])
            peak_generation_rate=max(peak_generation_rate,thread.get('generation_rate') or 0)
    engine.tick(at+.3)
    results=[]
    for key,want in expected.items():
        stream=engine.streams.get(('replay',*key))
        assert stream is not None,key
        match=stream.chars==want['chars'] and stream.tokens==want['tokens']
        assert match,(key,stream.chars,stream.tokens,want)
        assert not stream.text,key
        results.append({'item_id':key[0],'kind':key[1],'part':key[2],**want,'counts_match':match,'buffer_released':True})
    final=engine.snapshot(at+.3)
    assert all(t.get('generation_rate') is None for t in final['threads'] if t['status']!='inProgress')
    report={'capture':str(path),'sha256':capture_hash,'bytes':input_bytes,'streams':results,'phases_seen':sorted(phases),
            'display_labels_seen':sorted(labels),'peak_generation_rate':peak_generation_rate,'counters':dict(engine.counts),
            'unassociated_events':engine.pending_count,'elapsed_ms':round((time.perf_counter()-began)*1000,2)}
    engine.close()
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('captures',nargs='+',type=Path)
    args=parser.parse_args()
    print(json.dumps({'runs':[verify(path) for path in args.captures]},indent=2))
