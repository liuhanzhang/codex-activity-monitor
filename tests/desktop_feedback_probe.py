"""Finite real-stream regression for Desktop feedback: body plus ephemeral helper."""

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
from codex_progress_monitor.display import render
from codex_progress_monitor.metrics import count_text
from codex_progress_monitor.runtime import atomic_json,read_snapshot,runtime_dir


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--with-tool',action='store_true')
    parser.add_argument('--effort',default='low')
    args=parser.parse_args()
    os.umask(0o077)
    home=args.home.resolve();run=args.run_dir.resolve()
    run.mkdir(parents=True,exist_ok=False)
    before=hashlib.sha256((home/'config.toml').read_bytes()).hexdigest()
    runtime=runtime_dir(run/'rt');start(runtime)
    env={k:v for k,v in os.environ.items() if not k.startswith(('CODEX_','PREFLIGHT_'))}
    env.update(CODEX_HOME=str(home),CODEX_SQLITE_HOME=str(home/'sqlite'),CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED='1')
    diagnostics=(run/'stderr.txt').open('w')
    process=subprocess.Popen([sys.executable,'-m','codex_progress_monitor.cli','--runtime',str(runtime),'bridge','--','-c','features.code_mode_host=true','app-server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=diagnostics,text=True,bufsize=1,env=env,cwd=run)
    rpc=RPC(process,run/'protocol.jsonl')
    samples=[];result={'scope':'owned real stdio body and ephemeral-helper regression; no UI submission'}
    try:
        rpc.request('initialize',{'clientInfo':{'name':'desktop_feedback_regression','version':'0.1'},'capabilities':{'experimentalApi':True}})
        rpc.send({'method':'initialized'})
        model=rpc.request('config/read',{'includeLayers':False})['config']['model']
        body=new_thread(rpc,run,model,'采集修复验证')
        helper=rpc.request('thread/start',{'model':model,'cwd':str(run),'ephemeral':True,'approvalPolicy':'never','sandbox':'read-only','threadSource':'thread_name'})['thread']['id']
        targets=[body,helper]
        rpc.request('turn/start',{'threadId':body,'input':[{'type':'text','text':'不调用任何工具，用中文写约1500字，详细解释 TCP 的连接建立、可靠传输和关闭过程。仅输出正文。'}],'effort':args.effort,'summary':'auto'})
        rpc.request('turn/start',{'threadId':helper,'input':[{'type':'text','text':'不调用工具，为任务“解释 TCP 连接建立与关闭”生成一个简短中文标题，只输出标题。'}],'effort':'low','summary':'auto'})
        if args.with_tool:
            tool=new_thread(rpc,run,model,'采集修复工具验证')
            second_helper=rpc.request('thread/start',{'model':model,'cwd':str(run),'ephemeral':True,'approvalPolicy':'never','sandbox':'read-only','threadSource':'thread_name'})['thread']['id']
            targets.extend((tool,second_helper))
            rpc.request('turn/start',{'threadId':tool,'input':[{'type':'text','text':'只调用一次 tools.exec_command 执行 python3 -B -c \'import time; print("START", flush=True); time.sleep(20); print("END", flush=True)\'，yield_time_ms 设1000，随后用write_stdin等命令结束。禁止读写文件或联网，完成后简短说明结果。'}],'effort':args.effort,'summary':'auto'})
            rpc.request('turn/start',{'threadId':second_helper,'input':[{'type':'text','text':'不调用工具，为任务“执行 Python 延时命令”生成一个简短中文标题，只输出标题。'}],'effort':'low','summary':'auto'})
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            samples.append(read_snapshot(runtime))
            command_ends=[e for e in rpc.events if e.get('method')=='item/completed' and e.get('params',{}).get('threadId')==tool and e.get('params',{}).get('item',{}).get('type')=='commandExecution'] if args.with_tool else []
            if all(t in rpc.completed for t in targets) and (not args.with_tool or command_ends):break
            time.sleep(.1)
        time.sleep(.5)
        final=read_snapshot(runtime)
        text=''.join(e.get('params',{}).get('item',{}).get('text','') for e in rpc.events if e.get('method')=='item/completed' and e.get('params',{}).get('threadId')==body and e.get('params',{}).get('item',{}).get('type')=='agentMessage')
        thread=next(t for t in final['threads'] if t['id']==body)
        streams=[s for a in thread['activities'] for s in a['streams'] if s['kind']=='text']
        rates=[t['token_rates']['text'] for s in samples for t in s['threads'] if t['id']==body and t['status']=='inProgress' and t['token_rates']['text'] is not None]
        other=next(t for t in final['threads'] if t['id']==helper)
        result.update(model=model,effort=args.effort,turn_statuses=[rpc.completed.get(t,{}).get('status') for t in targets],
                      body_positive_rate_samples=sum(r>0 for r in rates),body_peak_rate=max(rates,default=0),
                      helper_ephemeral=other['ephemeral'],default_visible_rows=render(final)[1],
                      body_final_tokens=sum(s['tokens'] or 0 for s in streams),body_expected_tokens=count_text(text,'o200k_base')[0],
                      body_final_sha256=hashlib.sha256(text.encode()).hexdigest(),
                      body_quality=thread['quality'],dropped_packets=sum(b.get('dropped_packets',0) for b in final['backends'].values()),
                      parse_errors=sum(b.get('parse_errors',0) for b in final['backends'].values()),
                      backend_health=list(final['backends'].values()),counters=final.get('counters'),
                      all_quality={t['id']:t['quality'] for t in final['threads']},
                      config_unchanged=before==hashlib.sha256((home/'config.toml').read_bytes()).hexdigest())
        if args.with_tool:
            result['tool_phases']=sorted({t['phase'] for s in samples for t in s['threads'] if t['id']==tool})
            result['tool_max_seconds']=max((t['phase_seconds'] or 0 for s in samples for t in s['threads'] if t['id']==tool and t['phase']=='tool'),default=0)
            result['command_exit_codes']=[e['params']['item'].get('exitCode') for e in command_ends]
        result['passed']=(all(s=='completed' for s in result['turn_statuses']) and result['body_positive_rate_samples']>10
                          and result['body_final_tokens']==result['body_expected_tokens'] and result['helper_ephemeral'] is True
                          and result['default_visible_rows']==(2 if args.with_tool else 1) and not any(result['all_quality'].values()) and not result['dropped_packets']
                          and not result['parse_errors'] and result['config_unchanged'])
        if args.with_tool:
            result['passed']=result['passed'] and result['tool_max_seconds']>=15 and result['command_exit_codes']==[0]
    finally:
        if process.poll() is None:
            process.stdin.close()
            try:process.wait(timeout=12)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=8)
        diagnostics.close();stop(runtime)
        atomic_json(run/'result.json',result);atomic_json(run/'snapshots.json',samples)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result.get('passed') else 1


if __name__=='__main__':
    sys.exit(main())
