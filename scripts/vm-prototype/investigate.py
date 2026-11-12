#!/usr/bin/env python3
"""Owned, synthetic guest idle attribution. Raw output must stay outside Git."""
import argparse
import json
from pathlib import Path
import time
import vmproto as v
import stage3

SNAPSHOT = r'''python3 - <<'GUEST'
import json,os
from pathlib import Path
out={}
for p in Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  s=(p/'stat').read_text(); t=s.rsplit(')',1)[1].split()
  out[p.name]={'name':s.split('(',1)[1].rsplit(')',1)[0],'start':int(t[19]),'ticks':int(t[11])+int(t[12])}
 except (OSError,ValueError,IndexError):pass
print(json.dumps({'hz':os.sysconf('SC_CLK_TCK'),'processes':out}))
GUEST'''


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base',required=True)
    ap.add_argument('--output',required=True)
    a=ap.parse_args()
    root=Path(a.output).resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[2]):raise ValueError('use an external evidence directory')
    root.mkdir(mode=0o700)
    p=v.Paths(root)
    records=[]
    def record(kind,**data):
        item={'time_ns':time.time_ns(),'monotonic':time.monotonic(),'kind':kind,**data}
        records.append(item)
        with (root/'trace.jsonl').open('a') as f:f.write(json.dumps(item)+'\n')
    def guest(script):
        code,out,err=v.Qga(p.qga).sh(script)
        if code:raise RuntimeError(out + err)
        return out
    try:
        v.create_overlay(p,a.base)
        v.start_qemu(p,'run',29651)
        v.Qga(p.qga).ping_ready(90)
        guest('while ! pgrep -x nano >/dev/null || ! pgrep -x bare-mcp >/dev/null; do sleep 1; done')
        time.sleep(15)
        for phase in ['editor-open','editor-closed']:
            if phase=='editor-closed':guest('pkill -u deck -x nano; sleep 3')
            for n in range(3):
                before=json.loads(guest(SNAPSHOT))
                vm=v.read_vm(p); pid=vm['pid']
                t=time.monotonic(); ticks=stage3.tree_ticks(pid)
                time.sleep(20)
                wall=time.monotonic()-t
                vm_cpu=100*(stage3.tree_ticks(pid)-ticks)/stage3.TICK/wall
                after=json.loads(guest(SNAPSHOT))
                deltas=[]
                for pid,cur in after['processes'].items():
                    prev=before['processes'].get(pid)
                    if prev and prev['start']==cur['start']:
                        deltas.append({'pid':pid,'name':cur['name'],'cpu_percent':100*(cur['ticks']-prev['ticks'])/after['hz']/wall})
                record('interval',phase=phase,round=n+1,seconds=wall,vm_cpu_percent=vm_cpu,
                       vm_rss_mib=v.tree_rss_kib(vm['pid'])/1024,
                       processes=sorted(deltas,key=lambda x:-x['cpu_percent']),before=before,after=after)
    finally:
        vm=v.read_vm(p)
        if vm and v.proc_ok(p,vm['pid']):v.kill_vm(p)
        record('cleanup',vm_running=bool((vm:=v.read_vm(p)) and v.proc_ok(p,vm['pid'])))

if __name__=='__main__':main()
