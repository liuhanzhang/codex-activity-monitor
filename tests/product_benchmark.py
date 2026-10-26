"""Local transport overhead probe, not a model latency benchmark."""
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile
import time

from codex_activity_monitor.bridge import Observer


def main():
    with tempfile.TemporaryDirectory(prefix='cam-bench-',dir='/tmp') as directory:
        runtime=Path(directory)
        actual=runtime/'fake'
        actual.write_text('#!'+sys.executable+'\nimport sys\nwhile True:\n data=sys.stdin.buffer.read(65536)\n if not data:break\n sys.stdout.buffer.write(data);sys.stdout.buffer.flush()\n')
        actual.chmod(0o700)
        payload=(b'{"id":1,"result":{}}\n'*110000)
        report={'scope':'synthetic 2.31 MB stdio echo, collector absent; no model calls','runs':{}}
        for name,cmd in [('original',[str(actual),'app-server']),('bridge',[sys.executable,'-m','codex_activity_monitor.cli','--runtime',str(runtime),'bridge','--real-cli',str(actual),'--','app-server'])]:
            records=[]
            for _ in range(3):
                began=time.perf_counter()
                value=subprocess.run(['/usr/bin/time','-l',*cmd],input=payload,capture_output=True,timeout=20)
                elapsed=(time.perf_counter()-began)*1000
                assert value.stdout==payload and value.returncode==0
                rss=re.search(rb'(\d+)\s+maximum resident set size',value.stderr)
                records.append({'elapsed_ms':round(elapsed,2),'rss_bytes':int(rss[1]) if rss else None,'stdout_bytes':len(value.stdout)})
            report['runs'][name]=records
        report['median_overhead_ms']=round(statistics.median(r['elapsed_ms'] for r in report['runs']['bridge'])-statistics.median(r['elapsed_ms'] for r in report['runs']['original']),2)
        observer=Observer(runtime,'b','/tmp',actual,queue_size=4)
        began=time.perf_counter()
        for _ in range(2000):observer.offer('stdout',b'x'*65536)
        report['stalled_observer']={'offers':2000,'queued_packets':observer.packets.qsize(),'queued_bytes':observer.queued_bytes,'dropped_packets':observer.dropped,'offer_total_ms':round((time.perf_counter()-began)*1000,2)}
        assert observer.packets.qsize()==4 and observer.dropped==1996
        print(json.dumps(report,indent=2))


if __name__=='__main__':main()
