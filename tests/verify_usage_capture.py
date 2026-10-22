"""Compare official capture usage without exporting text or estimating speed."""
import argparse
import hashlib
import json
from pathlib import Path

from codex_progress_monitor.engine import Engine
from codex_progress_monitor.events import Event, filter_event


def verify(path):
    engine=Engine(False)
    turns={};items={};expected={};responses=[];protocol={}
    try:
        for line in path.open():
            row=json.loads(line);event=row['event'];direction=row['direction']
            clock=Event.from_row(row).at
            if direction=='protocol_out':
                params=event.get('params',{});turn=params.get('turnId') or params.get('turn',{}).get('id')
                if params.get('threadId') and turn:
                    turns[turn]=params['threadId']
                if event.get('method')=='thread/tokenUsage/updated':
                    usage=params['tokenUsage']['total']
                    protocol[turn]=[usage['outputTokens'],usage['reasoningOutputTokens']]
            if direction=='upstream':
                kind=event.get('type','')
                if kind in ('response.output_item.added','response.output_item.done'):
                    item=event['item'];items.setdefault(item['id'],{'turn':item.get('metadata',{}).get('turn_id')})
                if kind=='response.completed' and event.get('response',{}).get('usage',{}).get('output_tokens',0)>0:
                    raw=event['response'];safe=filter_event(direction,event)['response']
                    item_ids=safe['item_ids'];owners={items[i]['turn'] for i in item_ids}
                    assert len(owners)==1,owners
                    turn=owners.pop();values=[raw['usage']['output_tokens'],raw['usage']['output_tokens_details']['reasoning_tokens']]
                    expected.setdefault(turn,[0,0])
                    expected[turn]=[expected[turn][i]+values[i] for i in range(2)]
                    responses.append({'turn':turn,'response_id':raw['id'],'usage':values,
                                      'items_source':safe['items_source']})
            safe=filter_event(direction,event)
            if safe is not None:
                engine.ingest({**row,'event':safe});engine.tick(clock)
        result=[]
        snapshot=engine.snapshot(engine.clock)
        for turn,values in expected.items():
            view=next(t for t in snapshot['threads'] if t['id']==turns[turn])
            actual=[view['usage']['output_tokens'],view['usage']['reasoning_tokens']]
            assert actual==values==protocol[turn],(actual,values,protocol[turn])
            assert 'latest_reasoning' not in view['usage']
            result.append({'expected':values,'actual':actual,'protocol_total':protocol[turn]})
        assert sorted(expected.values())==[[1424,156],[1820,237]]
        return {'capture_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'scope':'20261011 concurrent capture',
                'responses':responses,'threads':result,'passed':True}
    finally:
        engine.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('capture',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args();report=verify(args.capture)
    encoded=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(encoded)
    else:print(encoded)
