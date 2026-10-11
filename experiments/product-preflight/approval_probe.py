#!/usr/bin/env python3
"""One real untrusted tool call; explicitly decline the test approval request."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from run_live import ROOT, RPC, launch
from telemetry import Telemetry, read_capture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=False)
    home = Path((ROOT/'experiments/desktop-isolation/latest-run.txt').read_text().strip())/'codex-home'
    before = hashlib.sha256((home/'config.toml').read_bytes()).hexdigest()
    process = launch(run, home)
    rpc = RPC(process, run/'protocol.jsonl')
    report = {'scope': 'one isolated approval regression; test requests are declined', 'status': 'running'}
    thread_id = turn_id = None
    try:
        info = rpc.request('initialize', {'clientInfo': {'name':'product_approval_probe','version':'0.1.0'},
                                          'capabilities': {'experimentalApi':True}})
        assert info['codexHome']==str(home)
        rpc.send({'method':'initialized'})
        model = rpc.request('config/read', {'includeLayers':False})['config']['model']
        started = rpc.request('thread/start', {'model':model,'cwd':str(run),'ephemeral':False,
                              'approvalPolicy':'untrusted','sandbox':'read-only',
                              'developerInstructions':'Telemetry test. Attempt the single harmless command requested. If permission is declined, stop without retrying.'})
        thread_id = started['thread']['id']
        prompt = '只调用一次shell执行 python3 -B -c \'print("PREFLIGHT_APPROVAL")\'，如果审批被拒绝则直接报告，不重试。'
        (run/'prompt.txt').write_text(prompt)
        started = rpc.request('turn/start', {'threadId':thread_id,'input':[{'type':'text','text':prompt}],
                              'effort':'low','summary':'auto'})
        turn_id = started['turn']['id']
        deadline = time.monotonic()+75
        while thread_id not in rpc.completed and time.monotonic()<deadline:
            time.sleep(0.2)
        approvals = [x for x in rpc.server_requests if x['method'].endswith('/requestApproval')]
        report.update(thread_id=thread_id, turn_id=turn_id,
                      approval_requests=[{'method':x['method'],'thread_id':x['params'].get('threadId'),
                                         'item_id':x['params'].get('itemId')} for x in approvals],
                      terminal_status=rpc.completed.get(thread_id,{}).get('status'))
        report['status']='passed' if approvals and thread_id in rpc.completed else 'not_observed'
    except Exception as exc:
        report.update(status='failed',error=str(exc))
    finally:
        if thread_id and turn_id and thread_id not in rpc.completed:
            try:
                rpc.request('turn/interrupt', {'threadId':thread_id,'turnId':turn_id},timeout=5)
            except Exception:
                pass
        process.stdin.close()
        try:
            process.wait(timeout=12)
        except Exception:
            process.terminate()
            process.wait(timeout=10)
        rpc.thread.join(timeout=2)
        report['wrapper_returncode']=process.returncode
        report['config_unchanged']=hashlib.sha256((home/'config.toml').read_bytes()).hexdigest()==before
        telemetry=Telemetry()
        for row in read_capture(run/'capture.jsonl'):
            telemetry.ingest(row)
        report['tool_activity']=telemetry.summarize()['tool_activity']
        report['forwarder']=json.loads((run/'capture.meta.json').read_text())
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    sys.exit(main())
