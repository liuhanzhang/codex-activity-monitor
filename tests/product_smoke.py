"""Manual real-backend acceptance; owns only the created stdio backend and turns."""

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
    run=args.run_dir.resolve()
    run.mkdir(parents=True,exist_ok=False)
    runtime=runtime_dir(run/'rt')
    home=args.home.resolve()
    before=hashlib.sha256((home/'config.toml').read_bytes()).hexdigest()
    start(runtime)
    env={k:v for k,v in os.environ.items() if not k.startswith(('CODEX_','PREFLIGHT_'))}
    env.update(CODEX_HOME=str(home),CODEX_SQLITE_HOME=str(home/'sqlite'),CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED='1')
    stderr=(run/'bridge.stderr').open('w')
    process=subprocess.Popen([sys.executable,'-m','codex_progress_monitor.cli','--runtime',str(runtime),'bridge','--','-c','features.code_mode_host=true','-c','tools.update_plan.enabled=true','app-server'],
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,bufsize=1,env=env,cwd=run)
    rpc=RPC(process,run/'protocol.jsonl')
    result={'scope':'product collector/bridge on owned stdio backend','phases':{},'display_labels':{},'peak_rates':{},'recovery':False,
            'parallel_work':False,'plan_observed':False,'silent_command_observed':False,'approval_resolved':False}
    samples=[]
    try:
        rpc.request('initialize',{'clientInfo':{'name':'product_smoke','version':'0.1'},'capabilities':{'experimentalApi':True}})
        rpc.send({'method':'initialized'})
        model=rpc.request('config/read',{'includeLayers':False})['config']['model']
        result['model']=model
        ids=[new_thread(rpc,run,model,'产品实测 '+letter) for letter in ('A','B')]
        prompts=[
            '先用 update_plan 制定两步计划并将第一步设为进行中，输出100字中文说明。用 Code Mode 的 Promise.all 并行执行两个 exec_command：第一个执行 python3 -B heredoc程序，程序至少50行、1500字符，定义六个纯函数并自检，只打印不读写文件不联网，最后打印 PRODUCT_OK，然后sleep(5)，所有源码直接写在heredoc参数中；第二个只执行 python3 -B -c "import time; time.sleep(4)"，不输出任何内容。等待两条命令全部结束，然后把计划两步都标为完成，输出100字结果。',
            '不调用任何工具。写一篇约800字中文介绍Python生成器，分10小段，逐条编号。']
        for thread,prompt in zip(ids,prompts):
            rpc.request('turn/start',{'threadId':thread,'input':[{'type':'text','text':prompt}],'effort':'low','summary':'auto'})
        deadline=time.monotonic()+150
        while time.monotonic()<deadline and not all(t in rpc.completed for t in ids):
            snapshot=read_snapshot(runtime)
            samples.append(snapshot)
            for thread in snapshot.get('threads',[]):
                if thread['id'] not in ids:
                    continue
                result['phases'].setdefault(thread['id'],set()).add(thread['phase'])
                result['display_labels'].setdefault(thread['id'],set()).add(thread['display_label'].split(' · ')[0])
                result['parallel_work'] |= thread.get('display_active_count',0)>=2
                result['plan_observed'] |= bool(thread.get('plan'))
                if thread.get('generation_rate') is not None:
                    result['peak_rates']['generation']=max(thread['generation_rate'],result['peak_rates'].get('generation',0))
                result['silent_command_observed'] |= any(a['kind']=='tool' and a['active'] and a.get('tool_name')=='exec_command' and a['output_chars']==0 for a in thread['activities'])
                for key,rate in thread['token_rates'].items():
                    if rate is not None:
                        result['peak_rates'][key]=max(rate,result['peak_rates'].get(key,0))
            time.sleep(.1)
        assert all(t in rpc.completed for t in ids),'Real product turns timed out'
        approved=rpc.request('thread/start',{'model':model,'cwd':str(run),'ephemeral':False,
                    'approvalPolicy':'untrusted','sandbox':'read-only',
                    'developerInstructions':'Bounded telemetry test. Request only the harmless print command. If declined, stop without retrying.'})['thread']['id']
        rpc.request('turn/start',{'threadId':approved,'input':[{'type':'text','text':'只调用一次 exec_command 执行 python3 -B -c \'print("PRODUCT_APPROVAL")\'。如果被拒绝，直接报告不重试。'}],'effort':'low','summary':'auto'})
        deadline=time.monotonic()+75
        while time.monotonic()<deadline and approved not in rpc.completed:
            samples.append(read_snapshot(runtime))
            time.sleep(.1)
        approvals=[e for e in rpc.events if e.get('method','').endswith('/requestApproval') and e.get('params',{}).get('threadId')==approved]
        resolved=[e for e in rpc.events if e.get('method')=='serverRequest/resolved' and e.get('params',{}).get('threadId')==approved]
        result['approval_resolved']=bool(approvals and resolved and approved in rpc.completed)
        # Observe a long-lived real tool while the consumer disappears and reattaches.
        background=new_thread(rpc,run,model,'产品恢复实测')
        rpc.request('turn/start',{'threadId':background,'input':[{'type':'text','text':'只执行一次shell命令 python3 -B -c \'import time; print("PRODUCT_RECOVERY_BEGIN",flush=True); time.sleep(12); print("PRODUCT_RECOVERY_END",flush=True)\' 。调用工具时yield_time_ms设1000，让它后台运行，然后用write_stdin等它结束，再用一句话回答。不要读写文件不要联网。'}],'effort':'low','summary':'auto'})
        stopped=False
        restart_at=None
        deadline=time.monotonic()+90
        while time.monotonic()<deadline and background not in rpc.completed:
            snapshot=read_snapshot(runtime)
            samples.append(snapshot)
            thread=next((t for t in snapshot.get('threads',[]) if t['id']==background),None)
            if thread and any(a['kind']=='tool' and a['active'] for a in thread['activities']) and not stopped:
                stop(runtime)
                stopped=True
                restart_at=time.monotonic()+2
                result['consumer_stopped_during_tool']=True
            if stopped and restart_at and time.monotonic()>=restart_at:
                start(runtime)
                restart_at=None
            if stopped and thread and thread['connected'] and 'observation_gap' in thread['quality']:
                result['recovery']=True
            time.sleep(.1)
        assert background in rpc.completed,'Recovery turn timed out'
        time.sleep(.6)
        result['statuses']={t:rpc.completed.get(t,{}).get('status') for t in [*ids,approved,background]}
        result['snapshot']=read_snapshot(runtime)
        result['phases']={k:sorted(v) for k,v in result['phases'].items()}
        result['display_labels']={k:sorted(v) for k,v in result['display_labels'].items()}
        result['config_unchanged']=before==hashlib.sha256((home/'config.toml').read_bytes()).hexdigest()
        result['final_rates_cleared']=all(t.get('generation_rate') is None for t in result['snapshot'].get('threads',[]))
        result['passed']=all(x=='completed' for x in result['statuses'].values()) and result['recovery'] and result['peak_rates'].get('generation',0)>0 and result['peak_rates'].get('tool_input',0)>0 and result['parallel_work'] and result['plan_observed'] and result['approval_resolved'] and result['silent_command_observed'] and result['final_rates_cleared']
    finally:
        if process.poll() is None:
            process.stdin.close()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
        stderr.close()
        stop(runtime)
        atomic_json(run/'result.json',result)
        atomic_json(run/'snapshots.json',samples)
    print(json.dumps({k:v for k,v in result.items() if k!='snapshot'},ensure_ascii=False))
    return 0 if result.get('passed') else 1


if __name__=='__main__':
    sys.exit(main())
