#!/usr/bin/env python3
"""Stage 1 desktop VM prototype: setup, boot, save, restore, kill, capture.

One QEMU/KVM guest (Arch cloud image, sway, bare). Standard library only.
State lives in a runtime directory outside Git: --dir or $DECK_VM_DIR.
The guest has no host mounts and no network except one forward to a loopback fixture.
Host control uses unix sockets only (QMP and the QEMU guest agent).
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGE_URL = "https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2"
FIXTURE_ADDR = "10.0.2.100"  # address the guest uses for the fixture
VM_NAME = "deck-stage1"
DISK_SIZE = "16G"
LIFETIME_S = 3 * 3600
MEM_MAX = "3500M"


def default_dir():
    return Path(os.environ.get("DECK_VM_DIR", "/data/tmp/deck-vm-stage1"))


class Paths:
    def __init__(self, root):
        self.root = Path(root)
        self.images = self.root / "images"
        self.base = self.images / "base.qcow2"
        self.disk = self.root / "disk.qcow2"
        self.seed = self.root / "seed"
        self.run = self.root / "run"
        self.state = self.root / "state"
        self.art = self.root / "artifacts"
        self.vm = self.run / "vm.json"
        self.qmp = self.run / "qmp.sock"
        self.qga = self.run / "qga.sock"
        self.pidfile = self.run / "qemu.pid"
        self.console = self.run / "console.log"
        self.statefile = self.state / "vm.state"
        self.statemeta = self.state / "state.json"

    def mk(self):
        for p in (self.images, self.seed, self.run, self.state, self.art):
            p.mkdir(parents=True, exist_ok=True)


# ---------- pure helpers (unit tested) ----------

def qemu_cmd(p, mode, fixture_port, incoming=False, seed_iso=None):
    """Build the QEMU argument list. mode is 'setup' (internet, seed disk) or 'run' (restricted)."""
    if mode == "setup":
        net = "user,id=n0,restrict=off"
    else:
        net = f"user,id=n0,restrict=on,guestfwd=tcp:{FIXTURE_ADDR}:80-cmd:socat - TCP:127.0.0.1:{fixture_port}"
    cmd = [
        "qemu-system-x86_64", "-name", f"{VM_NAME},debug-threads=on", "-no-user-config", "-nodefaults",
        "-machine", "q35,accel=kvm", "-cpu", "host", "-smp", "2", "-m", "2048",
        "-rtc", "base=utc,clock=host",
        "-display", "none", "-device", "virtio-vga,xres=1280,yres=800",
        "-device", "virtio-keyboard-pci", "-device", "virtio-tablet-pci",
        "-drive", f"file={p.disk},if=virtio,format=qcow2,cache=writeback,discard=unmap",
        "-netdev", net, "-device", "virtio-net-pci,netdev=n0",
        "-device", "virtio-serial-pci",
        "-chardev", f"socket,id=qga,path={p.qga},server=on,wait=off",
        "-device", "virtserialport,chardev=qga,name=org.qemu.guest_agent.0",
        "-qmp", f"unix:{p.qmp},server=on,wait=off",
        "-serial", f"file:{p.console}",
        "-pidfile", str(p.pidfile), "-no-reboot",
    ]
    if mode == "setup":
        cmd += ["-drive", f"file={seed_iso},media=cdrom,readonly=on,if=virtio"]
    if incoming:
        cmd += ["-incoming", "defer"]
    return cmd


def scope_cmd(inner, lifetime=LIFETIME_S, mem_max=MEM_MAX):
    """Wrap a command in a user systemd scope with a memory cap and a lifetime."""
    return ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={mem_max}", "-p", "MemorySwapMax=0",
            "-p", f"RuntimeMaxSec={lifetime}", "--"] + inner


def disk_fingerprint(path):
    st = os.stat(path)
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "ino": st.st_ino}


def state_matches_disk(meta, disk_path):
    """Saved memory is valid only against the disk as it was at save time."""
    return meta.get("disk") == disk_fingerprint(disk_path)


def is_our_vm(cmdline, name=VM_NAME, root=None):
    """True only for a qemu process started by this harness (name and runtime dir match)."""
    args = cmdline.split("\0") if isinstance(cmdline, str) else cmdline
    ok = bool(args) and os.path.basename(args[0]).startswith("qemu-system") and any(a.startswith(f"{name},") or a == name for a in args)
    if root is not None:
        ok = ok and any(str(root) in a for a in args)
    return ok


def rss_kib(status_text):
    for line in status_text.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return 0


def allocated_bytes(path):
    return os.stat(path).st_blocks * 512


# ---------- process control ----------

def read_vm(p):
    return json.loads(p.vm.read_text()) if p.vm.exists() else None


def proc_ok(p, pid):
    try:
        return is_our_vm(Path(f"/proc/{pid}/cmdline").read_bytes().decode(), root=p.root)
    except OSError:
        return False


def tree_pids(pid):
    """The qemu pid and all its descendants (dedicated helpers)."""
    out = [pid]
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                stat = (d / "stat").read_text().rsplit(")", 1)[1].split()
                if int(stat[1]) in out:
                    out.append(int(d.name))
            except (OSError, IndexError):
                pass
    return out


def tree_rss_kib(pid):
    total = 0
    for q in tree_pids(pid):
        try:
            total += rss_kib(Path(f"/proc/{q}/status").read_text())
        except OSError:
            pass
    return total


def alive(pid):
    """True if the process exists and is not a zombie."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def wait_gone(pid, timeout):
    t = time.time()
    while time.time() - t < timeout:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


# ---------- QMP and guest agent ----------

class Qmp:
    def __init__(self, path, timeout=10):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(timeout)
        self.s.connect(str(path))
        self.f = self.s.makefile("rw")
        self.f.readline()
        self.cmd("qmp_capabilities")

    def cmd(self, name, **args):
        self.f.write(json.dumps({"execute": name, "arguments": args}) + "\n")
        self.f.flush()
        while True:
            m = json.loads(self.f.readline())
            if "return" in m:
                return m["return"]
            if "error" in m:
                raise RuntimeError(f"qmp {name}: {m['error']}")

    def close(self):
        self.f.close()
        self.s.close()


class Qga:
    """One connection per call, always led by guest-sync so stale replies are dropped."""

    def __init__(self, path):
        self.path = str(path)
        self.n = int(time.time() * 1000) % 2**31

    def _open(self, timeout):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(timeout)
        s.connect(self.path)
        return s, s.makefile("rw")

    def call(self, name, timeout=10, **args):
        s, f = self._open(timeout)
        try:
            self.n += 1
            f.write("\xff" + json.dumps({"execute": "guest-sync", "arguments": {"id": self.n}}) + "\n")
            f.flush()
            while True:
                if json.loads(f.readline().lstrip("\xff")).get("return") == self.n:
                    break
            f.write(json.dumps({"execute": name, "arguments": args}) + "\n")
            f.flush()
            m = json.loads(f.readline())
            if "error" in m:
                raise RuntimeError(f"qga {name}: {m['error']}")
            return m.get("return")
        finally:
            f.close()
            s.close()

    def ping_ready(self, timeout):
        """Wait until the agent answers. Returns seconds waited."""
        t = time.time()
        while time.time() - t < timeout:
            try:
                self.call("guest-ping", timeout=1)
                return time.time() - t
            except (OSError, ValueError, RuntimeError):
                time.sleep(0.1)
        raise TimeoutError("guest agent not ready")

    def exec(self, argv, stdin=None, timeout=30):
        a = {"path": argv[0], "arg": argv[1:], "capture-output": True}
        if stdin is not None:
            a["input-data"] = base64.b64encode(stdin.encode()).decode()
        pid = self.call("guest-exec", **a)["pid"]
        t = time.time()
        while time.time() - t < timeout:
            st = self.call("guest-exec-status", pid=pid)
            if st["exited"]:
                dec = lambda k: base64.b64decode(st.get(k, "")).decode(errors="replace")
                return st.get("exitcode", -1), dec("out-data"), dec("err-data")
            time.sleep(0.05)
        raise TimeoutError(f"guest command timed out: {argv}")

    def sh(self, script, **kw):
        return self.exec(["/bin/sh", "-c", script], **kw)

    def pull(self, guest_path, host_path):
        h = self.call("guest-file-open", path=guest_path, mode="r")
        sha = hashlib.sha256()
        try:
            with open(host_path, "wb") as out:
                while True:
                    r = self.call("guest-file-read", handle=h, count=1 << 20)
                    b = base64.b64decode(r["buf-b64"])
                    out.write(b)
                    sha.update(b)
                    if r["eof"]:
                        break
        finally:
            self.call("guest-file-close", handle=h)
        return sha.hexdigest()


# ---------- lifecycle ----------

def start_qemu(p, mode, fixture_port, incoming=False, seed_iso=None):
    p.mk()
    for f in (p.qmp, p.qga, p.pidfile):
        f.unlink(missing_ok=True)
    if mode == "run" and not p.disk.exists():
        sys.exit("no disk: run setup first")
    log = open(p.run / "qemu.log", "ab")
    proc = subprocess.Popen(scope_cmd(qemu_cmd(p, mode, fixture_port, incoming, seed_iso)), stdout=log, stderr=log,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    t = time.time()
    while not (p.pidfile.exists() and p.qmp.exists()):
        if proc.poll() is not None:
            sys.exit(f"qemu exited early ({proc.returncode}); see {p.run / 'qemu.log'}")
        if time.time() - t > 20:
            sys.exit("qemu did not create its pidfile")
        time.sleep(0.02)
    pid = int(p.pidfile.read_text())
    p.vm.write_text(json.dumps({"pid": pid, "launcher_pid": proc.pid, "mode": mode, "started": time.time()}))
    return pid


def cmd_setup(a, p):
    p.mk()
    if not p.base.exists():
        sha_line = urllib.request.urlopen(IMAGE_URL + ".SHA256").read().decode().split()[0]
        print("downloading base image", file=sys.stderr)
        tmp = p.base.with_suffix(".part")
        urllib.request.urlretrieve(IMAGE_URL, tmp)
        h = hashlib.sha256(tmp.read_bytes()).hexdigest()
        if h != sha_line:
            tmp.unlink()
            sys.exit(f"checksum mismatch: {h} != {sha_line}")
        tmp.rename(p.base)
    bare = shutil.which("bare-mcp")
    if not bare:
        sys.exit("bare-mcp not on PATH")
    if p.disk.exists():
        sys.exit(f"{p.disk} exists; run clean --disk first")
    subprocess.run(["qemu-img", "convert", "-O", "qcow2", str(p.base), str(p.disk)], check=True)
    subprocess.run(["qemu-img", "resize", str(p.disk), DISK_SIZE], check=True)
    stage = p.seed / "cidata"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    g = HERE / "guest"
    for f in ("user-data", "setup-guest.sh", "sway-config", "bare-session", "bare-rpc", "as-deck"):
        shutil.copy(g / f, stage / f)
    shutil.copy(bare, stage / "bare-mcp")
    (stage / "meta-data").write_text("instance-id: deck-stage1\nlocal-hostname: deck-stage1\n")
    iso = p.seed / "seed.iso"
    subprocess.run(["genisoimage", "-quiet", "-output", str(iso), "-volid", "cidata", "-joliet", "-rock", str(stage)], check=True)
    pid = start_qemu(p, "setup", a.fixture_port, seed_iso=iso)
    t = time.time()
    print(f"setup VM pid {pid}; waiting for guest provisioning (poweroff)", file=sys.stderr)
    wait_gone(pid, a.timeout)
    if alive(pid):
        kill_vm(p)
        sys.exit("setup timed out")
    print(f"setup finished in {time.time() - t:.0f}s")
    p.vm.unlink(missing_ok=True)


def kill_vm(p):
    vm = read_vm(p)
    if not vm or not proc_ok(p, vm["pid"]):
        print("no matching VM process")
        return False
    os.kill(vm["pid"], signal.SIGKILL)
    wait_gone(vm["pid"], 5)
    p.vm.unlink(missing_ok=True)
    return True


def cmd_start(a, p):
    t0 = time.time()
    pid = start_qemu(p, "run", a.fixture_port)
    Qga(p.qga).ping_ready(a.timeout)
    print(json.dumps({"pid": pid, "boot_to_agent_s": round(time.time() - t0, 2)}))


def save_vm(p, qga=None, sync=True):
    """Quiesce, migrate RAM and devices to a file, quit QEMU. Returns timings."""
    vm = read_vm(p)
    if not vm or not proc_ok(p, vm["pid"]):
        sys.exit("no matching VM process")
    pid = vm["pid"]
    qga = qga or Qga(p.qga)
    t0 = time.time()
    if sync:
        qga.sh("sync")
    t1 = time.time()
    p.state.mkdir(exist_ok=True)
    p.statefile.unlink(missing_ok=True)
    p.statemeta.unlink(missing_ok=True)
    rss_before = tree_rss_kib(pid)
    q = Qmp(p.qmp)
    q.cmd("migrate", uri=f"file:{p.statefile}")
    while True:
        st = q.cmd("query-migrate")
        if st["status"] == "completed":
            break
        if st["status"] in ("failed", "cancelled"):
            sys.exit(f"migration {st['status']}: {st.get('error-desc')}")
        time.sleep(0.02)
    t2 = time.time()
    q.cmd("quit")
    q.close()
    gone = wait_gone(pid, 15)
    t3 = time.time()
    if not gone:
        sys.exit("qemu did not exit after quit")
    meta = {"saved": t3, "disk": disk_fingerprint(p.disk), "state_bytes": p.statefile.stat().st_size}
    p.statemeta.write_text(json.dumps(meta))
    p.vm.unlink(missing_ok=True)
    return {"quiesce_s": round(t1 - t0, 3), "migrate_s": round(t2 - t1, 3), "exit_s": round(t3 - t2, 3),
            "total_s": round(t3 - t0, 3), "state_bytes": meta["state_bytes"], "rss_kib_before": rss_before}


def restore_vm(p, fixture_port):
    """Start QEMU on the paired disk and load the saved state. Returns timings. Consumes the state."""
    if not p.statemeta.exists() or not p.statefile.exists():
        sys.exit("no saved state")
    meta = json.loads(p.statemeta.read_text())
    if not state_matches_disk(meta, p.disk):
        sys.exit("saved state does not match the disk (disk changed after the save); refusing to restore")
    # The state file is deleted after a successful load: the disk changes from then on.
    t0 = time.time()
    pid = start_qemu(p, "run", fixture_port, incoming=True)
    q = Qmp(p.qmp)
    q.cmd("migrate-incoming", uri=f"file:{p.statefile}")
    while True:
        st = q.cmd("query-status")
        if st["status"] == "running":
            break
        time.sleep(0.02)
    t1 = time.time()
    q.close()
    qga = Qga(p.qga)
    qga.ping_ready(30)
    qga.call("guest-set-time", time=int(time.time() * 1e9))
    code, out, _ = qga.sh("echo restored-ok")
    t2 = time.time()
    p.statefile.unlink()
    p.statemeta.unlink()
    if code != 0 or "restored-ok" not in out:
        sys.exit("guest command failed after restore")
    return {"pid": pid, "load_s": round(t1 - t0, 3), "to_usable_s": round(t2 - t0, 3)}


def cmd_stop(a, p):
    print(json.dumps(save_vm(p)))


def cmd_restore(a, p):
    print(json.dumps(restore_vm(p, a.fixture_port)))


def cmd_kill(a, p):
    sys.exit(0 if kill_vm(p) else 1)


def cmd_status(a, p):
    vm = read_vm(p)
    live = bool(vm) and proc_ok(p, vm["pid"])
    out = {"running": live, "state_saved": p.statefile.exists()}
    if live:
        out["rss_kib_tree"] = tree_rss_kib(vm["pid"])
    if p.disk.exists():
        out["disk_allocated_bytes"] = allocated_bytes(p.disk)
    print(json.dumps(out))


def cmd_exec(a, p):
    code, out, err = Qga(p.qga).sh(a.script)
    sys.stdout.write(out)
    sys.stderr.write(err)
    sys.exit(code)


def cmd_capture(a, p):
    """Real desktop capture two ways: QEMU screendump (host side) and grim (guest compositor)."""
    p.art.mkdir(parents=True, exist_ok=True)
    tag = a.tag or time.strftime("%H%M%S")
    t = time.time()
    q = Qmp(p.qmp)
    q.cmd("screendump", filename=str(p.art / f"desktop-qemu-{tag}.png"), format="png")
    q.close()
    t1 = time.time()
    qga = Qga(p.qga)
    code, _, err = qga.sh(f"/usr/local/bin/as-deck grim /tmp/desktop-{tag}.png")
    sha = qga.pull(f"/tmp/desktop-{tag}.png", str(p.art / f"desktop-grim-{tag}.png"))
    t2 = time.time()
    print(json.dumps({"qemu_screendump_s": round(t1 - t, 3), "grim_and_pull_s": round(t2 - t1, 3), "grim_exit": code, "grim_sha256": sha}))


def cmd_clean(a, p):
    vm = read_vm(p)
    if vm and proc_ok(p, vm["pid"]):
        sys.exit("VM still running; stop or kill it first")
    for f in (p.vm, p.qmp, p.qga, p.pidfile, p.statefile, p.statemeta):
        f.unlink(missing_ok=True)
    if a.disk:
        p.disk.unlink(missing_ok=True)
    print("cleaned")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(default_dir()), help="runtime directory outside Git (default: $DECK_VM_DIR)")
    ap.add_argument("--fixture-port", type=int, default=int(os.environ.get("DECK_VM_FIXTURE_PORT", "18080")))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="download the image, provision the guest, power off")
    s.add_argument("--timeout", type=int, default=1500)
    s.set_defaults(f=cmd_setup)
    s = sub.add_parser("start", help="cold boot the disk")
    s.add_argument("--timeout", type=int, default=120)
    s.set_defaults(f=cmd_start)
    sub.add_parser("stop", help="save RAM and devices, then exit QEMU").set_defaults(f=cmd_stop)
    sub.add_parser("restore", help="restore the saved state onto its paired disk").set_defaults(f=cmd_restore)
    sub.add_parser("kill", help="SIGKILL our VM only, after checking its identity").set_defaults(f=cmd_kill)
    sub.add_parser("status", help="print VM and storage status").set_defaults(f=cmd_status)
    s = sub.add_parser("exec", help="run a shell script in the guest")
    s.add_argument("script")
    s.set_defaults(f=cmd_exec)
    s = sub.add_parser("capture", help="capture the desktop")
    s.add_argument("--tag")
    s.set_defaults(f=cmd_capture)
    s = sub.add_parser("clean", help="remove sockets and saved state; --disk also removes the disk")
    s.add_argument("--disk", action="store_true")
    s.set_defaults(f=cmd_clean)
    a = ap.parse_args(argv)
    a.f(a, Paths(a.dir))


if __name__ == "__main__":
    main()
