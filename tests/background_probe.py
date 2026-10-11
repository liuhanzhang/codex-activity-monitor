"""Bounded acceptance of a command that may outlive its generating turn."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'experiments/product-preflight'))
from run_live import RPC,new_thread
from codex_progress_monitor.cli import start,stop
from codex_progress_monitor.runtime import atomic_json,read_snapshot,runtime_dir


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path,required=True)
    args=parser.parse_args()
    run=args.run_dir.resolve();run.mkdir(parents=True,exist_ok=False)
    runtime=runtime_dir(run/'rt');home=args.home.resolve()
    before=hashlib.sha256((home/'config.toml').read_bytes()).hexdigest()
    start(runtime)
    env={k:v for k,v in os.environ.items() if not k.startswith(('CODEX_','PREFLIGHT_'))}
    env.update(CODEX_HOME=str(home),CODEX_SQLITE_HOME=str(home/'sqlite'),CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED='1')
    stderr=(run/'stderr.txt').open('w')
    process=subprocess.Popen([sys.executable,'-m','codex_progress_monitor.cli','--runtime',str(runtime),'bridge','--','-c','features.code_mode_host=true','app-server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,bufsize=1,env=env,cwd=run)
    rpc=RPC(process,run/'protocol.jsonl')
    result={'scope':'owned stdio command lifecycle across turn completion'}
    samples=[]
    try:
        rpc.request('initialize',{'clientInfo':{'name':'background_acceptance','version':'0.1'},'capabilities':{'experimentalApi':True}})
        rpc.send({'method':'initialized'})
        model=rpc.request('config/read',{'includeLayers':False})['config']['model']
        thread=new_thread(rpc,run,model,'后台生命周期验证')
        prompt=('这是有限后台命令生命周期验证，只调用一次 exec，里面只调用一次 tools.exec_command。'
                '在 JavaScript 中执行：const r = await tools.exec_command({cmd: "python3 -B -c \'import time; print(\"BG_BEGIN\",flush=True); time.sleep(8); print(\"BG_END\",flush=True)\'", yield_time_ms: 1000}); text(r); '
                '不要调用 write_stdin，不要等待进程结束，不要重试。看到工具返回后立刻只回复“已启动”。'
                '不读写文件，不联网。')
        rpc.request('turn/start',{'threadId':thread,'input':[{'type':'text','text':prompt}],'effort':'low','summary':'auto'})
        deadline=time.monotonic()+55
        done_at=None
        while time.monotonic()<deadline:
            snapshot=read_snapshot(runtime);samples.append(snapshot)
            events=list(rpc.events)
            commands=[e for e in events if e.get('method')=='item/started' and e.get('params',{}).get('item',{}).get('type')=='commandExecution']
            ends=[e for e in events if e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('type')=='commandExecution']
            if thread in rpc.completed and done_at is None:done_at=time.monotonic()
            if done_at and (ends or time.monotonic()-done_at>12):break
            time.sleep(.1)
        time.sleep(.5)
        events=list(rpc.events)
        result['model']=model
        result['turn_status']=rpc.completed.get(thread,{}).get('status')
        result['lifecycle']=[{'event':e['method'],'index':index,'turn_id':e.get('params',{}).get('turnId'),
                              'status':e.get('params',{}).get('item',{}).get('status'),
                              'exit_code':e.get('params',{}).get('item',{}).get('exitCode')}
                             for index,e in enumerate(events) if e.get('method')=='turn/completed' or (e.get('method') in ('item/started','item/completed') and e.get('params',{}).get('item',{}).get('type')=='commandExecution')]
        result['snapshot']=read_snapshot(runtime)
        ordering=result['lifecycle']
        turn_index=next((e['index'] for e in ordering if e['event']=='turn/completed'),None)
        end_index=next((e['index'] for e in ordering if e['event']=='item/completed'),None)
        result['command_outlived_turn']=turn_index is not None and end_index is not None and turn_index<end_index
        result['active_after_turn_samples']=sum(t['status']=='completed' and t.get('has_active_work',False) for s in samples for t in s.get('threads',[]) if t['id']==thread)
        result['passed']=result['command_outlived_turn'] and result['active_after_turn_samples']>0 and result['turn_status']=='completed'
        result['config_unchanged']=before==hashlib.sha256((home/'config.toml').read_bytes()).hexdigest()
    finally:
        if process.poll() is None:
            process.stdin.close()
            try:process.wait(timeout=12)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=8)
        stderr.close();stop(runtime)
        atomic_json(run/'result.json',result);atomic_json(run/'snapshots.json',samples)
    print(json.dumps({k:v for k,v in result.items() if k!='snapshot'},ensure_ascii=False))


if __name__=='__main__':main()
