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
import re
import shutil
import signal
import shlex
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGE_URL = "https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2"
FIXTURE_ADDR = "10.0.2.100"  # address the guest uses for the fixture
VM_NAME = "deck-stage1"
DISK_SIZE = "16G"
LIFETIME_S = 3 * 3600
MEM_MAX = "3500M"


def default_bases():
    return Path(os.environ.get("DECK_VM_BASES", default_dir().parent / "vm-bases"))


def default_dir():
    return Path(os.environ.get("DECK_VM_DIR", str(Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "deck" / "vm-stage1")))


class Paths:
    def __init__(self, root):
        self.root = Path(root)
        self.images = self.root / "images"
        self.base = self.images / "base.qcow2"
        self.disk = self.root / "disk.qcow2"
        self.diskmeta = self.disk.with_suffix(".json")
        self.seed = self.root / "seed"
        self.run = self.root / "run"
        self.state = self.root / "state"
        self.art = self.root / "artifacts"
        self.vm = self.run / "vm.json"
        self.qmp = self.run / "qmp.sock"
        self.qga = self.run / "qga.sock"
        self.exec = self.run / "exec.sock"  # host end of the guest executor's virtio-serial port
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
    if mode == "run":
        # The executor channel. A unix socket in the runtime directory, owned by this user. No network path.
        cmd += ["-chardev", f"socket,id=exec,path={p.exec},server=on,wait=off",
                "-device", "virtserialport,chardev=exec,name=org.deck.executor.0"]
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


def state_matches_disk(meta, disk_path, base=None):
    """Saved memory is valid only against the disk as it was at save time, on the same base image."""
    return meta.get("disk") == disk_fingerprint(disk_path) and meta.get("base") == base


def state_uri(codec, path, direction):
    """Migration URI for a state file. 'none' is a plain file; 'zstd-N' and 'lz4' pipe through the installed tool."""
    q = shlex.quote(str(path))
    if codec == "none":
        return f"file:{path}"
    if codec == "lz4":
        if not shutil.which("lz4"):
            sys.exit("lz4 is required for this saved-state codec")
        return f"exec:lz4 -q -f - {q}" if direction == "save" else f"exec:lz4 -q -d -c {q}"
    level = codec.removeprefix("zstd-")
    if not (codec.startswith("zstd-") and level.isdigit() and 1 <= int(level) <= 19):
        sys.exit(f"unknown state codec {codec!r}")
    if not shutil.which("zstd"):
        sys.exit("zstd is required for this saved-state codec")
    return f"exec:zstd -q -f -{level} -o {q}" if direction == "save" else f"exec:zstd -q -d -c {q}"


# ---------- base images and overlays ----------

def read_json(path):
    return json.loads(Path(path).read_text())


def write_disk_meta(p, meta):
    p.diskmeta.write_text(json.dumps(meta))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def backing_file(path):
    """The backing path stored in a qcow2 header, or None. Reads the header only; opens no image."""
    with open(path, "rb") as f:
        h = f.read(20)
        if h[:4] != b"QFI\xfb":
            sys.exit(f"{path} is not a qcow2 image")
        off, size = struct.unpack(">QI", h[8:20])
        if not off:
            return None
        f.seek(off)
        return f.read(size).decode()


def base_path(bases_dir, base_id):
    return Path(bases_dir) / f"{base_id}.qcow2"


def is_frozen_base(path):
    path = Path(path)
    for q in (path, path.resolve()):
        side = q.with_suffix(".json")
        if side.exists() and read_json(side).get("kind") == "base":
            return True
    return path.exists() and not os.access(path, os.W_OK)


def freeze_base(p, bases_dir):
    """Turn a freshly provisioned, never-booted standalone disk into a read-only shared base. Moves the disk."""
    if not p.disk.exists():
        sys.exit("no disk to freeze")
    vm = read_vm(p)
    if vm and proc_ok(p, vm["pid"]):
        sys.exit("VM still running; stop it first")
    if p.statefile.exists() or p.statemeta.exists():
        sys.exit("saved state exists for this disk; refusing to freeze")
    meta = read_json(p.diskmeta) if p.diskmeta.exists() else {}
    if meta.get("kind") != "standalone" or meta.get("provisioned") != "setup":
        sys.exit("disk has no setup provenance record; only a disk made by 'setup' in this runtime can become a base")
    if meta.get("run_booted"):
        sys.exit("disk was booted in run mode and may hold sessions or personal data; provision a fresh one")
    if backing_file(p.disk):
        sys.exit("disk has a backing file; a base must be standalone")
    digest = sha256_file(p.disk)
    base_id = digest[:16]
    dest = base_path(bases_dir, base_id)
    if dest.exists():
        sys.exit(f"base {base_id} exists; refusing to replace it")
    Path(bases_dir).mkdir(parents=True, exist_ok=True)
    virtual = json.loads(subprocess.run(["qemu-img", "info", "-U", "--output=json", str(p.disk)], check=True, capture_output=True, text=True).stdout)["virtual-size"]
    shutil.move(str(p.disk), dest)
    os.chmod(dest, 0o444)
    info = {"kind": "base", "id": base_id, "sha256": digest, "virtual_size": virtual, "frozen": time.time(),
            "fingerprint": disk_fingerprint(dest), "provenance": meta}
    dest.with_suffix(".json").write_text(json.dumps(info))
    p.diskmeta.unlink()
    return info


def create_overlay(p, base):
    """Create the private writable qcow2 on top of a frozen base. The base is verified by full digest here, once."""
    base = Path(base).resolve()
    side = base.with_suffix(".json")
    info = read_json(side) if side.exists() else {}
    if info.get("kind") != "base" or info.get("id") != base.stem:
        sys.exit(f"{base} is not a frozen base")
    if disk_fingerprint(base) != info["fingerprint"]:
        sys.exit("base changed since it was frozen")
    if sha256_file(base) != info["sha256"]:
        sys.exit("base digest does not match its record")
    if p.disk.exists() or p.diskmeta.exists():
        sys.exit(f"{p.disk} exists; run clean --disk first")
    p.mk()
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-b", str(base), "-F", "qcow2", str(p.disk)], check=True)
    write_disk_meta(p, {"kind": "overlay", "created": time.time(),
                        "base": {"id": info["id"], "path": str(base), "fingerprint": info["fingerprint"]}})
    return info


def check_disk(p):
    """Cheap pre-boot check (no hashing): the recorded base is present, unchanged and is the backing file."""
    meta = read_json(p.diskmeta) if p.diskmeta.exists() else {}
    backing = backing_file(p.disk)
    if meta.get("kind") == "overlay":
        b = meta["base"]
        if not Path(b["path"]).exists():
            sys.exit(f"base {b['id']} is missing")
        if disk_fingerprint(b["path"]) != b["fingerprint"]:
            sys.exit(f"base changed since the overlay was created ({b['id']})")
        if backing != b["path"]:
            sys.exit("backing file differs from the recorded base")
    elif backing:
        sys.exit("disk has a backing file but no base record; refusing to boot it")


def disk_base_id(p):
    meta = read_json(p.diskmeta) if p.diskmeta.exists() else {}
    return meta.get("base", {}).get("id") if meta.get("kind") == "overlay" else None


def allocated_tree(path):
    path = Path(path)
    return sum(allocated_bytes(f) for f in ([path] if path.is_file() else path.rglob("*")) if f.is_file())


def storage_report(*runtimes):
    """Allocated bytes of each disk, saved state and every distinct base. Virtual sizes are not counted."""
    parts, seen_bases = {}, set()
    for p in runtimes:
        if p.disk.exists():
            parts[f"{p.root.name}/disk"] = allocated_bytes(p.disk)
        if p.state.exists():
            parts[f"{p.root.name}/state"] = allocated_tree(p.state)
        meta = read_json(p.diskmeta) if p.diskmeta.exists() else {}
        if meta.get("kind") == "overlay":
            base = Path(meta["base"]["path"]).resolve()
            if base not in seen_bases:
                parts["base" if not seen_bases else f"base:{base}"] = allocated_bytes(base)
                seen_bases.add(base)
    return {"parts": parts, "total_bytes": sum(parts.values())}


def cgroup_dir(pid):
    try:
        return Path("/sys/fs/cgroup") / Path(f"/proc/{pid}/cgroup").read_text().strip().split("::", 1)[1].lstrip("/")
    except (OSError, IndexError):
        return None


def cpu_usec(cg):
    """CPU time of everything in the VM's scope, children included. None when unreadable."""
    try:
        for line in (cg / "cpu.stat").read_text().splitlines():
            if line.startswith("usage_usec"):
                return int(line.split()[1])
    except (OSError, AttributeError):
        pass
    return None


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

    def push(self, guest_path, data):
        """Write bytes to a guest file as root. Bootstrap only: ordinary commands use the executor."""
        h = self.call("guest-file-open", path=guest_path, mode="w")
        try:
            for i in range(0, len(data), 32768):
                self.call("guest-file-write", handle=h, **{"buf-b64": base64.b64encode(data[i:i + 32768]).decode()})
        finally:
            self.call("guest-file-close", handle=h)

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
    vm = read_vm(p)
    if vm and proc_ok(p, vm["pid"]):
        raise RuntimeError("VM already running; refusing to replace its control sockets")
    for f in (p.qmp, p.qga, p.exec, p.pidfile):
        f.unlink(missing_ok=True)
    if mode == "run" and not p.disk.exists():
        sys.exit("no disk: run setup first")
    if mode == "run":
        check_disk(p)
        meta = read_json(p.diskmeta) if p.diskmeta.exists() else {}
        if meta.get("kind") == "standalone" and not meta.get("run_booted"):
            write_disk_meta(p, {**meta, "run_booted": True})  # from here the disk may hold sessions: it can no longer become a base
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


EXECUTOR_FILES = {"deck_executor.py": "/usr/local/bin/deck-executor",
                  "deck-executor.service": "/etc/systemd/system/deck-executor.service",
                  "70-deck-executor.rules": "/etc/udev/rules.d/70-deck-executor.rules"}


def install_executor(p, bot_id):
    """Bootstrap the executor into a running guest through the root test channel, then bind it to one bot.

    The guest identity is made here and stays on the guest disk. It is printed so the host can pin it.
    Refuses to rebind a guest that already has an identity.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", bot_id):
        sys.exit("bot ID must be 1 to 64 characters of A-Za-z0-9_-")
    qga = Qga(p.qga)
    code, out, _ = qga.sh("test -e /home/deck/.local/state/deck-executor/identity.json && echo bound")
    if "bound" in out:
        sys.exit("this guest already has an executor identity; refusing to rebind it")
    for src, dst in EXECUTOR_FILES.items():
        qga.push(dst, (HERE / "guest" / src).read_bytes())
    guest_id = uuid.uuid4().hex
    ident = json.dumps({"bot_id": bot_id, "guest_id": guest_id, "max_epoch": 0})
    code, _, err = qga.sh("chmod 755 /usr/local/bin/deck-executor && "
                          "runuser -u deck -- sh -c 'umask 077 && mkdir -p ~/.local/state/deck-executor ~/workspace "
                          "&& cat > ~/.local/state/deck-executor/identity.json' && "
                          "udevadm control --reload && udevadm trigger --subsystem-match=virtio-ports --action=add && udevadm settle && "
                          "systemctl daemon-reload && systemctl enable --now deck-executor.service", stdin=ident, timeout=60)
    if code != 0:
        sys.exit(f"executor install failed: {err.strip()[:300]}")
    info = {"bot_id": bot_id, "guest_id": guest_id}
    (p.root / "executor.json").write_text(json.dumps(info))
    return info


def cmd_install_executor(a, p):
    print(json.dumps(install_executor(p, a.bot)))


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
    for f in ("user-data", "setup-guest.sh", "sway-config", "bare-session", "bare-rpc", "as-deck", *EXECUTOR_FILES):
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
    write_disk_meta(p, {"kind": "standalone", "provisioned": "setup", "run_booted": False, "finished": time.time(),
                        "guest_files_sha256": {f.name: hashlib.sha256(f.read_bytes()).hexdigest()[:16] for f in sorted((HERE / "guest").iterdir())}})


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


def save_vm(p, qga=None, sync=True, codec="none"):
    """Quiesce, migrate RAM and devices to a file, quit QEMU. Returns timings, CPU time and sampled peak RSS."""
    uri = state_uri(codec, p.statefile, "save")
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
    cg = cgroup_dir(pid)
    cpu0 = cpu_usec(cg)
    peak, last = rss_before, 0
    base = disk_base_id(p)
    q = Qmp(p.qmp)
    q.cmd("migrate", uri=uri)
    deadline = time.monotonic() + 60
    while True:
        if time.monotonic() >= deadline:
            q.cmd("migrate_cancel")
            q.close()
            raise TimeoutError("save did not complete within 60 seconds")
        st = q.cmd("query-migrate")
        if st["status"] == "completed":
            break
        if st["status"] in ("failed", "cancelled"):
            sys.exit(f"migration {st['status']}: {st.get('error-desc')}")
        if time.time() - last > 0.1:
            peak, last = max(peak, tree_rss_kib(pid)), time.time()
        time.sleep(0.02)
    t2 = time.time()
    q.cmd("quit")
    q.close()
    cpu1 = cpu_usec(cg)
    while cg and cg.exists() and time.time() - t2 < 30:  # the last reading before the scope vanishes; a compressor may outlive QEMU
        cpu1 = cpu_usec(cg) or cpu1
        time.sleep(0.02)
    gone = wait_gone(pid, 15)
    t3 = time.time()
    if not gone:
        sys.exit("qemu did not exit after quit")
    meta = {"saved": t3, "disk": disk_fingerprint(p.disk), "base": base, "codec": codec, "state_bytes": p.statefile.stat().st_size}
    p.statemeta.write_text(json.dumps(meta))
    p.vm.unlink(missing_ok=True)
    return {"quiesce_s": round(t1 - t0, 3), "migrate_s": round(t2 - t1, 3), "exit_s": round(t3 - t2, 3),
            "total_s": round(t3 - t0, 3), "state_bytes": meta["state_bytes"], "rss_kib_before": rss_before, "codec": codec,
            "peak_rss_kib_sampled": peak, "cpu_s": None if None in (cpu0, cpu1) else round((cpu1 - cpu0) / 1e6, 3)}


def restore_vm(p, fixture_port):
    """Start QEMU on the paired disk and load the saved state. Returns timings. Consumes the state."""
    if not p.statemeta.exists() or not p.statefile.exists():
        sys.exit("no saved state")
    meta = json.loads(p.statemeta.read_text())
    codec = meta.get("codec", "none")
    uri = state_uri(codec, p.statefile, "load")
    if not state_matches_disk(meta, p.disk, disk_base_id(p)):
        sys.exit("saved state does not match the disk or its base (changed after the save); refusing to restore")
    # The state file is deleted after a successful load: the disk changes from then on.
    t0 = time.time()
    pid = start_qemu(p, "run", fixture_port, incoming=True)
    q = Qmp(p.qmp)
    cg = cgroup_dir(pid)
    peak, last = tree_rss_kib(pid), 0
    q.cmd("migrate-incoming", uri=uri)
    deadline = time.monotonic() + 60
    while True:
        if time.monotonic() >= deadline:
            q.close()
            kill_vm(p)
            raise TimeoutError("restore did not complete within 60 seconds")
        st = q.cmd("query-status")
        if st["status"] == "running":
            break
        if time.time() - last > 0.1:
            peak, last = max(peak, tree_rss_kib(pid)), time.time()
        time.sleep(0.02)
    t1 = time.time()
    peak = max(peak, tree_rss_kib(pid))
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
    cpu = cpu_usec(cg)
    return {"pid": pid, "load_s": round(t1 - t0, 3), "to_usable_s": round(t2 - t0, 3), "codec": codec,
            "peak_rss_kib_sampled": peak, "cpu_s_since_launch": None if cpu is None else round(cpu / 1e6, 3)}


def cmd_stop(a, p):
    print(json.dumps(save_vm(p, codec=a.codec)))


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
        out["storage"] = storage_report(p)
        out["base"] = disk_base_id(p)
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
    if a.disk and is_frozen_base(p.disk):
        sys.exit(f"{p.disk} is a frozen base; refusing to delete it")
    for f in (p.vm, p.qmp, p.qga, p.exec, p.pidfile, p.statefile, p.statemeta):
        f.unlink(missing_ok=True)
    if a.disk:
        p.disk.unlink(missing_ok=True)
        p.diskmeta.unlink(missing_ok=True)
    print("cleaned")


def cmd_freeze_base(a, p):
    print(json.dumps(freeze_base(p, a.bases)))


def cmd_overlay(a, p):
    print(json.dumps(create_overlay(p, a.base)))


def cmd_verify_base(a, p):
    base = Path(a.base)
    info = read_json(base.with_suffix(".json"))
    ok = sha256_file(base) == info["sha256"]
    print(json.dumps({"id": info["id"], "sha256_matches": ok}))
    sys.exit(0 if ok else 1)


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
    s = sub.add_parser("stop", help="save RAM and devices, then exit QEMU")
    s.add_argument("--codec", default="none", help="state file codec: none (default) or zstd-N; restore reads it from the saved record")
    s.set_defaults(f=cmd_stop)
    sub.add_parser("restore", help="restore the saved state onto its paired disk").set_defaults(f=cmd_restore)
    sub.add_parser("kill", help="SIGKILL our VM only, after checking its identity").set_defaults(f=cmd_kill)
    sub.add_parser("status", help="print VM and storage status").set_defaults(f=cmd_status)
    s = sub.add_parser("exec", help="run a shell script in the guest")
    s.add_argument("script")
    s.set_defaults(f=cmd_exec)
    s = sub.add_parser("install-executor", help="bootstrap the guest executor into the running VM and bind it to one bot")
    s.add_argument("--bot", required=True)
    s.set_defaults(f=cmd_install_executor)
    s = sub.add_parser("capture", help="capture the desktop")
    s.add_argument("--tag")
    s.set_defaults(f=cmd_capture)
    s = sub.add_parser("clean", help="remove sockets and saved state; --disk also removes the disk")
    s.add_argument("--disk", action="store_true")
    s.set_defaults(f=cmd_clean)
    s = sub.add_parser("freeze-base", help="turn the never-booted disk made by setup into a read-only shared base (moves the disk)")
    s.add_argument("--bases", default=str(default_bases()), help="base store (default: $DECK_VM_BASES)")
    s.set_defaults(f=cmd_freeze_base)
    s = sub.add_parser("overlay", help="create this runtime's private qcow2 on top of a frozen base")
    s.add_argument("--base", required=True, help="path of the frozen base .qcow2")
    s.set_defaults(f=cmd_overlay)
    s = sub.add_parser("verify-base", help="hash a frozen base and compare it with its record")
    s.add_argument("base")
    s.set_defaults(f=cmd_verify_base)
    a = ap.parse_args(argv)
    a.f(a, Paths(a.dir))


if __name__ == "__main__":
    main()
