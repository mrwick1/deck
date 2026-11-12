#!/usr/bin/env python3
"""Stage 1 scenario: boot, drive bare, capture, five save/restore cycles, kill, cold boot.

Needs a provisioned runtime directory (vmproto.py setup). Writes results.json there.
Uses the synthetic fixture only. Does not start a daemon; stops the VM and fixture at the end.
"""
import argparse
import hashlib
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import vmproto as v

HERE = Path(__file__).resolve().parent
UID_DIR = "/run/user/1001"
CSV_SHA = hashlib.sha256(b"item,qty\nwidget,3\ngadget,5\n").hexdigest()
NOTE = "UNSAVED-NOTE-7f3a"
DISK_MARK = "disk-marker-9c21"
FIX = "http://10.0.2.100"


class Guest:
    def __init__(self, p):
        self.p, self.q, self.n = p, v.Qga(p.qga), 0

    def sh(self, s, **kw):
        return self.q.sh(s, **kw)

    def deck(self, s, **kw):
        return self.q.exec(["/usr/local/bin/as-deck", "sh", "-c", s], **kw)

    def desktop_ready(self, timeout=90):
        t = time.time()
        while time.time() - t < timeout:
            try:
                c, o, _ = self.deck(f"pgrep -x nano >/dev/null && pgrep -x bare-mcp >/dev/null && test -p {UID_DIR}/bare/in && swaymsg -t get_version >/dev/null && echo up", timeout=5)
                if "up" in o:
                    return time.time() - t
            except (OSError, ValueError, RuntimeError, TimeoutError):
                pass
            time.sleep(0.2)
        raise TimeoutError("desktop not ready")

    def rpc(self, method, params=None, notify=False):
        m = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            m["params"] = params
        if not notify:
            self.n += 1
            m["id"] = self.n
        c, o, e = self.q.exec(["/usr/local/bin/as-deck", "bare-rpc", "60"], stdin=json.dumps(m), timeout=90)
        return json.loads(o) if o.strip() else None

    def bare_init(self):
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "stage1", "version": "0"}})
        self.rpc("notifications/initialized", notify=True)

    def tool(self, name, **a):
        r = self.rpc("tools/call", {"name": name, "arguments": a})
        if not r or "result" not in r:
            raise RuntimeError(f"bare {name}: {r}")
        return "\n".join(x.get("text", "") for x in r["result"]["content"])

    def ref(self, snap, role, name):
        m = re.search(rf'\[(e\d+)\] {role} "{name}"', snap)
        if not m:
            raise RuntimeError(f"no {role} {name!r} in snapshot:\n{snap}")
        return m.group(1)


def fixture_up(port):
    try:
        return urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2).read() == b"ok"
    except OSError:
        return False


def fixture_start(p, port, secret_port):
    pidf = p.run / "fixture.pid"
    if fixture_up(port):
        return False
    p.run.mkdir(parents=True, exist_ok=True)
    log = open(p.run / "fixture.log", "ab")
    proc = subprocess.Popen([sys.executable, "-I", str(HERE / "fixture.py"), "--port", str(port), "--secret-port", str(secret_port)],
                            stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    pidf.write_text(str(proc.pid))
    for _ in range(50):
        if fixture_up(port):
            return True
        time.sleep(0.1)
    sys.exit("fixture did not start")


def fixture_stop(p):
    pidf = p.run / "fixture.pid"
    if not pidf.exists():
        return
    pid = int(pidf.read_text())
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode()
    except OSError:
        cmd = ""
    if "fixture.py" in cmd:  # identity: our fixture script
        os.kill(pid, signal.SIGTERM)
        v.wait_gone(pid, 5)
    pidf.unlink()


def nano_identity(g):
    _, o, _ = g.sh("pid=$(pgrep -x nano); echo $pid $(awk '{print $22}' /proc/$pid/stat)")
    return o.strip()


def foot_crop_hash(g):
    """Hash of the terminal window pixels: shows the unsaved text is still on screen."""
    script = ("g=$(swaymsg -t get_tree | python3 -c \"import sys,json\n"
              "def w(n):\n"
              " if n.get('app_id')=='foot': print('%d,%d %dx%d'%(n['rect']['x'],n['rect']['y'],n['rect']['width'],n['rect']['height'])); return\n"
              " for c in n.get('nodes',[])+n.get('floating_nodes',[]): w(c)\n"
              "w(json.load(sys.stdin))\"); grim -g \"$g\" - | sha256sum | cut -c1-16")
    return g.deck(script)[1].strip()


def checks(g, expect_volatile=True, ref_nano=None):
    """Return a dict of marker checks. expect_volatile True: nano text and bare tab must be present."""
    r = {}
    r["nano_process_same"] = ref_nano is not None and nano_identity(g) == ref_nano
    r["notes_file_not_written"] = g.sh("test ! -s /home/deck/notes.txt && echo y")[1].strip() == "y"
    r["disk_marker"] = g.sh("cat /home/deck/disk-marker")[1].strip() == DISK_MARK
    r["shell_file"] = g.sh("cat /home/deck/shell-created.txt")[1].strip() == "created-in-guest"
    r["downloaded_file_sha"] = g.sh("sha256sum /home/deck/Downloads/report.csv | cut -d' ' -f1")[1].strip() == CSV_SHA
    if expect_volatile:
        try:
            r["bare_tab_url"] = "/account" in g.tool("eval", js="location.href")
            snap = g.tool("snapshot")
            r["bare_logged_in"] = "Signed in as demo" in g.tool("read") or "Signed in as demo" in snap
            r["bare_cookie"] = "fx_session" in g.tool("eval", js="document.cookie")
        except RuntimeError as e:
            r["bare_error"] = str(e)[:200]
    return r


def med(xs):
    return round(statistics.median(xs), 3) if xs else None


def run(a):
    p = v.Paths(a.dir)
    p.mk()
    out = {"host": {"kernel": os.uname().release, "cpus": os.cpu_count(),
                    "mem_available_kib_start": int(re.search(r"MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1)),
                    "loadavg": os.getloadavg()[0]},
           "versions": {"qemu": subprocess.run(["qemu-system-x86_64", "--version"], capture_output=True, text=True).stdout.split("\n")[0],
                        "bare_mcp_sha256": hashlib.sha256(Path(subprocess.run(["which", "bare-mcp"], capture_output=True, text=True).stdout.strip()).read_bytes()).hexdigest()[:16]}}
    vm = v.read_vm(p)
    if vm and v.proc_ok(p, vm["pid"]):
        v.kill_vm(p)
    p.statefile.unlink(missing_ok=True)
    p.statemeta.unlink(missing_ok=True)
    started_fixture = fixture_start(p, a.fixture_port, a.fixture_port + 1)

    # ---- cold boot ----
    t0 = time.time()
    pid = v.start_qemu(p, "run", a.fixture_port)
    g = Guest(p)
    agent_s = g.q.ping_ready(120)
    g.sh("true")
    cmd_s = time.time() - t0
    desk_s = time.time() - t0 + g.desktop_ready()
    out["cold_boot"] = {"launch_to_guest_command_s": round(cmd_s, 2), "launch_to_desktop_ready_s": round(desk_s, 2)}
    out["config"] = {"vcpus": 2, "ram_mib": 2048, "cmdline": " ".join(v.qemu_cmd(p, "run", a.fixture_port))}
    out["guest"] = g.sh("uname -r; pacman -Q sway foot grim wtype nano qemu-guest-agent | tr '\\n' ';'")[1]

    # ---- boundary smoke ----
    ok_fix = g.sh(f"curl -s -m 5 {FIX}/health")[1] == "ok"
    leaks = [g.sh(f"curl -s -m 4 {u}")[1] for u in (f"http://10.0.2.2:{a.fixture_port + 1}/", f"http://10.0.2.100:{a.fixture_port + 1}/", f"http://10.0.2.2:{a.fixture_port}/health")]
    mounts = g.sh("grep -E '9p|virtiofs|fuse' /proc/mounts | wc -l")[1].strip()
    out["boundary"] = {"fixture_reachable": ok_fix, "host_marker_leaked": any("HOST-ONLY" in x for x in leaks),
                       "loopback_fixture_via_gateway_reachable": leaks[2] == "ok", "host_share_mounts": mounts}

    # ---- shell + bare + capture ----
    g.sh("printf created-in-guest > /home/deck/shell-created.txt; printf %s " + DISK_MARK + " > /home/deck/disk-marker; mkdir -p /home/deck/Downloads /home/deck/shots; chown -R deck:deck /home/deck; sync")
    g.bare_init()
    g.tool("open", url=FIX + "/", window=True, downloads_dir="/home/deck/Downloads")
    snap = g.tool("snapshot")
    g.tool("act", op="click", ref=g.ref(snap, "link", "Download report"))
    time.sleep(1.5)
    out["downloads_tool"] = g.tool("downloads")[:300]
    g.tool("act", op="click", ref=g.ref(snap, "link", "Sign in"))
    snap = g.tool("snapshot")
    for label, text in (("User", "demo"), ("Password", "demo-pass")):
        role = "textbox"
        g.tool("act", op="type", ref=g.ref(snap, role, label), text=text)
    g.tool("act", op="click", ref=g.ref(snap, "button", "Sign in"))
    out["after_login"] = g.tool("snapshot", mode="full")[:300]
    # unsaved text in the terminal editor: focus the terminal, type, never save
    g.deck("swaymsg '[app_id=foot] focus'")
    time.sleep(0.5)
    g.deck(f"wtype '{NOTE}'")
    time.sleep(0.5)
    ref_nano = nano_identity(g)
    shot = g.tool("screenshot", path="/home/deck/shots/bare-page.png")
    h_bare = g.q.pull("/home/deck/shots/bare-page.png", str(p.art / "bare-page.png"))
    h_guest = g.sh("sha256sum /home/deck/shots/bare-page.png | cut -d' ' -f1")[1].strip()
    g.q.pull(f"/home/deck/Downloads/report.csv", str(p.art / "report.csv"))
    out["artifacts"] = {"bare_screenshot_sha_match": h_bare == h_guest, "download_sha_match": hashlib.sha256((p.art / "report.csv").read_bytes()).hexdigest() == CSV_SHA,
                        "bare_screenshot_reply": shot[:120]}
    out["capture_after_setup"] = json.loads(subprocess.run([sys.executable, "-I", str(HERE / "vmproto.py"), "--dir", str(p.root), "capture", "--tag", "ready"], capture_output=True, text=True).stdout)
    out["checks_before_cycles"] = checks(g, ref_nano=ref_nano)
    foot_h = foot_crop_hash(g)

    # ---- capture rate ----
    q = v.Qmp(p.qmp)
    ts = []
    for i in range(10):
        t = time.time()
        q.cmd("screendump", filename=str(p.art / "rate.png"), format="png")
        ts.append(time.time() - t)
    q.close()
    out["screendump_10_s"] = [round(x, 3) for x in ts]

    # ---- idle RSS after 60 s ----
    time.sleep(60)
    out["idle_rss_kib_60s"] = v.tree_rss_kib(pid)
    out["disk_allocated_after_workflow_bytes"] = v.allocated_bytes(p.disk)
    out["disk_virtual_bytes"] = json.loads(subprocess.run(["qemu-img", "info", "-U", "--output=json", str(p.disk)], capture_output=True, text=True).stdout)["virtual-size"]

    # ---- five save/restore cycles ----
    cycles = []
    for i in range(1, a.cycles + 1):
        c = {"cycle": i}
        c["pre"] = checks(g, ref_nano=ref_nano)
        g.q.sh("true")
        s = v.save_vm(p, qga=g.q)
        c["save"] = s
        c["vm_gone"] = not v.proc_ok(p, pid) and not v.alive(pid)
        c["helpers_gone"] = subprocess.run(["pgrep", "-f", f"{v.VM_NAME}|socat - TCP:127.0.0.1:{a.fixture_port}"], capture_output=True).returncode != 0
        c["resident_after_save_kib"] = 0 if c["vm_gone"] else v.tree_rss_kib(pid)
        c["state_files_bytes"] = sum(f.stat().st_size for f in p.state.iterdir())
        c["fixture_alive"] = fixture_up(a.fixture_port)
        r = v.restore_vm(p, a.fixture_port)
        c["restore"] = r
        pid = r["pid"]
        g = Guest(p)
        g.n = 1000 * i
        g.desktop_ready(10)
        c["post"] = checks(g, ref_nano=ref_nano)
        c["terminal_pixels_same"] = foot_crop_hash(g) == foot_h
        c["rss_after_restore_kib"] = v.tree_rss_kib(pid)
        t = time.time()
        c["capture_after_restore"] = json.loads(subprocess.run([sys.executable, "-I", str(HERE / "vmproto.py"), "--dir", str(p.root), "capture", "--tag", f"cycle{i}"], capture_output=True, text=True).stdout)
        cycles.append(c)
        print(f"cycle {i}: save {s['total_s']}s restore {r['to_usable_s']}s state {s['state_bytes'] >> 20} MiB", flush=True)
    out["cycles"] = cycles
    sv = [c["save"]["total_s"] for c in cycles]
    rs = [c["restore"]["to_usable_s"] for c in cycles]
    sz = [c["save"]["state_bytes"] for c in cycles]
    out["summary"] = {"save_s": sv, "save_median": med(sv), "save_max": max(sv), "restore_s": rs, "restore_median": med(rs), "restore_max": max(rs),
                      "state_bytes": sz, "state_max": max(sz),
                      "all_markers_survived": all(all(x for x in c["post"].values()) for c in cycles)}

    # ---- kill and cold boot ----
    g.sh(f"printf %s after-last-restore > /home/deck/post-restore-marker; sync")
    vm = v.read_vm(p)
    out["kill"] = {"identity_checked": v.proc_ok(p, vm["pid"]), "parent_pid": int(Path(f"/proc/{vm['pid']}/stat").read_text().rsplit(')', 1)[1].split()[1])}
    v.kill_vm(p)
    out["kill"]["gone"] = not v.alive(vm["pid"])
    t0 = time.time()
    pid = v.start_qemu(p, "run", a.fixture_port)
    g = Guest(p)
    g.q.ping_ready(120)
    g.desktop_ready()
    out["cold_boot_after_kill_s"] = round(time.time() - t0, 2)
    g.bare_init()
    after = checks(g, ref_nano=ref_nano)
    after["post_restore_marker"] = g.sh("cat /home/deck/post-restore-marker")[1].strip() == "after-last-restore"
    after["nano_text_on_screen_hash_same"] = foot_crop_hash(g) == foot_h
    out["after_kill"] = after
    out["after_kill"]["bare_sessions"] = g.tool("sessions")[:200]
    subprocess.run([sys.executable, "-I", str(HERE / "vmproto.py"), "--dir", str(p.root), "capture", "--tag", "after-kill"], capture_output=True)

    # ---- cleanup ----
    v.kill_vm(p)
    if started_fixture or a.stop_fixture:
        fixture_stop(p)
    out["final"] = {"vm_running": bool(v.read_vm(p)), "fixture_up": fixture_up(a.fixture_port)}
    (p.root / "results.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out["summary"], indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(v.default_dir()))
    ap.add_argument("--fixture-port", type=int, default=18080)
    ap.add_argument("--cycles", type=int, default=5)
    ap.add_argument("--stop-fixture", action="store_true")
    run(ap.parse_args())
