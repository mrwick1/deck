#!/usr/bin/env python3
"""Serial, synthetic one-bot benchmarks with private, timestamped raw evidence.

Observer sampling adds overhead. This is not a real-model or capacity benchmark.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import stage3
import vmproto as v


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while b := f.read(1024 * 1024):
            h.update(b)
    return h.hexdigest()


def sample(pid):
    """Read only process counters. Never read environment variables or file contents."""
    p = Path(f'/proc/{pid}')
    try:
        stat = p.joinpath('stat').read_text().rsplit(')', 1)[1].split()
        status = p.joinpath('status').read_text()
        fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
        out = {'pid': pid, 'name': fields['Name'].strip(), 'state': fields['State'].strip(),
               'start_ticks': int(stat[19]), 'cpu_ticks': int(stat[11]) + int(stat[12]),
               'minor_faults': int(stat[7]), 'major_faults': int(stat[9]),
               'rss_kib': int(fields.get('VmRSS', '0 kB').split()[0]),
               'threads': int(fields['Threads']), 'fds': len(list(p.joinpath('fd').iterdir()))}
        for filename in ('io', 'smaps_rollup'):
            try:
                lines = p.joinpath(filename).read_text().splitlines()
                out[filename] = {k: int(val.split()[0]) for line in lines if ':' in line
                                 for k, val in [line.split(':', 1)]}
            except (OSError, ValueError):
                out[filename] = None
        return out
    except (OSError, ValueError, KeyError, IndexError):
        return None


def run(a):
    root = Path(a.output).resolve()
    repo = Path(__file__).resolve().parents[2]
    if root.is_relative_to(repo):
        raise ValueError("raw benchmark evidence must stay outside the repository")
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(root, 0o700)
    lock = threading.Lock()
    started = time.monotonic()
    def log(kind, **data):
        with lock, (root / 'trace.jsonl').open('a') as f:
            f.write(json.dumps({'wall_ns': time.time_ns(), 'elapsed_s': time.monotonic() - started,
                                'kind': kind, **data}, default=str) + '\n')
    log('metadata', revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        binary_sha256=digest(a.deck), base_sha256=digest(a.base), python=sys.version,
        benchmark_sha256=digest(__file__), scenario_sha256=digest(stage3.__file__),
        kernel=os.uname().release, cpus=os.cpu_count(), ticks_per_second=os.sysconf('SC_CLK_TCK'),
        workload='stage3 synthetic browser/shell/file, viewer, idle save, restored reply',
        limits='1-second observer samples; transient peaks and completed short processes can be missed')
    original_popen, original_call, original_window = subprocess.Popen, stage3.Api.call, stage3.window
    owned = []
    server_dirs = []
    def popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        owned.append(child)
        log('process_start', pid=child.pid, argv=args[0] if args else kwargs.get('args'), cwd=kwargs.get('cwd'))
        if kwargs.get('cwd') and Path(kwargs['cwd']).joinpath('deck.toml').exists():
            server_dirs.append(Path(kwargs['cwd']))
        return child
    def call(api, method, path, body=None, timeout=30):
        t = time.monotonic()
        try:
            result = original_call(api, method, path, body, timeout)
            log('api', method=method, path=path, body=body, result=result, seconds=time.monotonic()-t)
            return result
        except Exception as e:
            log('api_error', method=method, path=path, error=str(e), seconds=time.monotonic()-t)
            raise
    def window(label, secs, pid, paths, api):
        log('window_begin', label=label)
        if (vm := v.read_vm(paths)) and v.proc_ok(paths, vm['pid']):
            log('guest_processes', label=label, processes=v.Qga(paths.qga).sh('ps -eo pid,ppid,comm,pcpu,rss,nlwp --sort=-pcpu'))
        result = original_window(label, secs, pid, paths, api)
        log('window_end', **result)
        return result
    subprocess.Popen, stage3.Api.call, stage3.window = popen, call, window
    stop = threading.Event()
    def observe():
        while not stop.wait(1):
            pids = set()
            for child in list(owned):
                if child.poll() is None:
                    pids.update(v.tree_pids(child.pid))
            for runtime in root.glob('run-*'):
                if (vm := v.read_vm(v.Paths(runtime))) and v.proc_ok(v.Paths(runtime), vm['pid']):
                    pids.update(v.tree_pids(vm['pid']))
            log('sample', processes=[s for pid in sorted(pids) if (s := sample(pid))],
                host_load=os.getloadavg(), host_meminfo=Path('/proc/meminfo').read_text())
    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    results = []
    try:
        for n in range(1, a.rounds + 1):
            runtime = root / f'run-{n}'
            stage3.Model.calls = 0
            sys.argv = ['stage3.py', '--base', a.base, '--deck', a.deck, '--dir', str(runtime),
                        '--fixture-port', '29641', '--server-port', '29642', '--model-port', '29643']
            log('round_begin', round=n)
            stage3.main()
            result = json.loads((runtime / 'results.json').read_text())
            results.append(result)
            log('round_end', round=n, result=result)
            if server_dirs:
                source = server_dirs[-1]
                shutil.copy2(source / 'server.log', runtime / 'server.log')
                shutil.copytree(source / 'data', runtime / 'host-data')
            if not result['ok']:
                raise RuntimeError(f'round {n} failed')
    except BaseException as e:
        log('run_error', error=repr(e))
        raise
    finally:
        stop.set()
        observer.join()
        subprocess.Popen, stage3.Api.call, stage3.window = original_popen, original_call, original_window
        log('cleanup', processes=[{'pid': c.pid, 'returncode': c.poll()} for c in owned])
        (root / 'summary.json').write_text(json.dumps(results, indent=2))
    print(f'Evidence: {root}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', required=True)
    ap.add_argument('--deck', required=True, help='release binary built from the benchmark revision')
    ap.add_argument('--output', required=True, help='new private directory outside Git')
    ap.add_argument('--rounds', type=int, default=3, choices=range(1, 11))
    run(ap.parse_args())
