"""Bounded acceptance on an owned backend; writes only filtered metric evidence."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from rich.console import Console
from codex_progress_monitor.cli import start, stop
from codex_progress_monitor.display import render
from codex_progress_monitor.runtime import atomic_json, read_snapshot, runtime_dir

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'experiments/product-preflight'))
from run_live import RPC


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    run=args.run_dir.resolve();run.mkdir(parents=True,exist_ok=False)
    runtime=runtime_dir(run/'rt')
    config=args.home/'config.toml'
    before=hashlib.sha256(config.read_bytes()).hexdigest()
    start(runtime)
    env={k:v for k,v in os.environ.items() if not k.startswith(('CODEX_','PREFLIGHT_'))}
    env.update(CODEX_HOME=str(args.home.resolve()),CODEX_SQLITE_HOME=str(args.home.resolve()/'sqlite'),
               CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED='1')
    stderr=(run/'stderr.log').open('w')
    process=subprocess.Popen([sys.executable,'-m','codex_progress_monitor.cli','--runtime',str(runtime),'bridge','--','app-server'],
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,env=env,cwd=run)
    rpc=RPC(process,run/'protocol.jsonl')
    evidence={'scope':'owned stdio backend and isolated collector','turns':[],'restart_preserved':False}
    try:
        rpc.request('initialize',{'clientInfo':{'name':'usage_smoke','version':'0.1'},'capabilities':{'experimentalApi':True}})
        rpc.send({'method':'initialized'})
        model=rpc.request('config/read',{'includeLayers':False})['config']['model']
        thread=rpc.request('thread/start',{'model':model,'cwd':str(run),'ephemeral':False,'approvalPolicy':'never','sandbox':'read-only'})['thread']['id']
        prompts=['用心算求 sum(k*k for k in range(1,201))，再且只调用一次 exec_command 用 python3 -B -c 验证该数值。不读写文件、不联网，最后回答数值和一句解释。',
                 '不调用工具。计算 19*23*29*31，给出精确值和一句解释。']
        previous=None
        for index,prompt in enumerate(prompts):
            rpc.completed.pop(thread,None)
            turn=rpc.request('turn/start',{'threadId':thread,'input':[{'type':'text','text':prompt}],'effort':'medium','summary':'auto'})['turn']['id']
            samples=[]
            deadline=time.monotonic()+150
            while time.monotonic()<deadline:
                snapshot=read_snapshot(runtime)
                view=next((t for t in snapshot.get('threads',[]) if t['id']==thread and t.get('turn_id')==turn),None)
                if view:
                    samples.append({'phase':view['phase'],'usage':view.get('usage'),'generation_rate':view.get('generation_rate')})
                    if thread in rpc.completed and view['usage']['output_tokens'] and view['usage'].get('latest_reasoning'):
                        break
                time.sleep(.05)
            assert thread in rpc.completed,'Turn timed out'
            time.sleep(.35)
            final=next(t for t in read_snapshot(runtime)['threads'] if t['id']==thread)
            notifications=[e['params']['tokenUsage'] for e in rpc.events if e.get('method')=='thread/tokenUsage/updated'
                           and e['params'].get('turnId')==turn]
            expected=[sum(u['last'][key] for u in notifications) for key in ('outputTokens','reasoningOutputTokens')]
            actual=[final['usage']['output_tokens'],final['usage']['reasoning_tokens']]
            assert actual==expected,(actual,expected)
            assert final['usage']['latest_reasoning'],'No verified reasoning speed'
            if previous:
                assert any(s['usage']['output_tokens']==0 and s['usage']['latest_reasoning']==previous for s in samples),'Reset/retention unobserved'
            previous=final['usage']['latest_reasoning']
            evidence['turns'].append({'notification_count':len(notifications),'expected':expected,'actual':actual,
                                      'latest_reasoning':previous,'zero_observed':any(s['usage']['output_tokens']==0 for s in samples),
                                      'live_generation_observed':any(s['generation_rate'] is not None for s in samples)})
        before_restart=final['usage']
        stop(runtime);start(runtime)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            snapshot=read_snapshot(runtime)
            found=next((t for t in snapshot.get('threads',[]) if t['id']==thread and t.get('connected')),None)
            if found and found['usage']==before_restart:
                evidence['restart_preserved']=True
                break
            time.sleep(.1)
        assert evidence['restart_preserved'],'Static metrics lost on restart'
        evidence['collector_implementation']=snapshot['implementation']
        evidence['bridge_implementation']=snapshot['backends'][found['backend']]['implementation']
        evidence['config_unchanged']=hashlib.sha256(config.read_bytes()).hexdigest()==before
        evidence['observation_health']={k:snapshot['backends'][found['backend']].get(k) for k in ('dropped_packets','parse_errors','drop_reasons')}
        output=io.StringIO();Console(file=output,width=120).print(render(snapshot,detail=True)[0])
        (run/'watch.txt').write_text(output.getvalue())
        evidence['passed']=evidence['config_unchanged'] and evidence['restart_preserved']
    finally:
        if process.poll() is None:
            process.stdin.close()
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate();process.wait(timeout=8)
        stderr.close();stop(runtime)
        # RPC records include the deliberately submitted prompt. Do not retain
        # them as metric evidence or copy them to tracked documentation.
        (run/'protocol.jsonl').unlink(missing_ok=True)
        atomic_json(run/'result.json',evidence)
    print(json.dumps(evidence,ensure_ascii=False))


if __name__=='__main__':
    main()
