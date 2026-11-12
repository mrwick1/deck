#!/usr/bin/env python3
"""Deck guest executor, protocol v1. Standard library only.

Runs as the desktop user, never as root. It serves one host connection at a time over a
virtio-serial port (or a unix socket in tests). See docs/stage2-results.md for the protocol.

The executor never logs argv or output. It writes a durable receipt for every command before
it starts the process. It never runs a command twice: a repeated command ID returns the receipt.
"""
import argparse
import base64
import collections
import errno
import hashlib
import json
import os
import re
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time

PROTO = 1
MAGIC = b"DK"
HEADER = 7  # magic(2) version(1) length(4, big endian)
MAX_FRAME = 256 * 1024
MAX_ARGV = 64
MAX_ARG_BYTES = 16 * 1024
MAX_DEADLINE_MS = 3_600_000
TAIL_BYTES = 16 * 1024
SPOOL_BYTES = 1 << 20
MAX_ARTIFACTS = 16
MAX_ARTIFACT_BYTES = 8 << 20
CHUNK_MAX = 128 * 1024
MAX_RUNNING = 8
STALL_S = 5.0
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_DEVICE = "/dev/virtio-ports/org.deck.executor.0"
LIMITS = {"max_frame": MAX_FRAME, "max_deadline_ms": MAX_DEADLINE_MS, "tail_bytes": TAIL_BYTES,
          "max_artifact_bytes": MAX_ARTIFACT_BYTES, "chunk_max": CHUNK_MAX}


class ProtocolError(Exception):
    pass


# ---------- pure helpers (unit tested) ----------

def exec_digest(bot_id, task_id, command_id, deadline_ms, argv):
    """SHA-256 over length-prefixed fields. The host computes the same value. Epoch is not part of it."""
    h = hashlib.sha256(b"deck-exec-v1")
    for part in [bot_id, task_id, command_id, str(deadline_ms), str(len(argv)), *argv]:
        b = part.encode()
        h.update(struct.pack(">I", len(b)))
        h.update(b)
    return h.hexdigest()


def encode_frame(msg):
    body = json.dumps(msg, separators=(",", ":")).encode()
    if len(body) > MAX_FRAME:
        raise ValueError("frame too large")
    return MAGIC + bytes([PROTO]) + struct.pack(">I", len(body)) + body


class FrameReader:
    """Accumulates bytes and yields decoded messages. Raises ProtocolError on any malformed frame."""

    def __init__(self):
        self.buf = bytearray()
        self.touched = time.monotonic()

    def reset(self):
        self.buf.clear()

    def feed(self, data):
        self.buf += data
        self.touched = time.monotonic()
        out = []
        while len(self.buf) >= HEADER:
            if bytes(self.buf[:2]) != MAGIC:
                raise ProtocolError("bad magic")
            if self.buf[2] != PROTO:
                raise ProtocolError("unsupported version")
            n = struct.unpack(">I", bytes(self.buf[3:7]))[0]
            if n > MAX_FRAME:
                raise ProtocolError("frame too large")
            if len(self.buf) < HEADER + n:
                break
            body = bytes(self.buf[HEADER:HEADER + n])
            del self.buf[:HEADER + n]
            try:
                msg = json.loads(body)
            except ValueError:
                raise ProtocolError("bad json")
            if not isinstance(msg, dict):
                raise ProtocolError("frame is not an object")
            out.append(msg)
        return out


def group_members(pgid):
    """Live (not zombie) processes in a process group."""
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as f:
                    rest = f.read().rsplit(")", 1)[1].split()
                if int(rest[2]) == pgid and rest[0] != "Z":
                    out.append(int(d))
            except (OSError, IndexError, ValueError):
                pass
    return out


def wait_group_gone(pgid, timeout, leader=None):
    end = time.monotonic() + timeout
    while True:
        if leader is not None:
            leader.poll()  # reap the leader so it does not linger as a zombie
        if not group_members(pgid):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.02)


def cgroup_root():
    """This process's cgroup v2 directory when it may create children there, else None.

    The service unit sets Delegate=yes so that the desktop user owns its cgroup.
    """
    try:
        with open("/proc/self/cgroup") as f:
            rel = next(l.strip()[3:] for l in f if l.startswith("0::"))
    except (OSError, StopIteration):
        return None
    path = "/sys/fs/cgroup" + rel
    ok = os.access(os.path.join(path, "cgroup.procs"), os.W_OK) and os.path.exists(os.path.join(path, "cgroup.kill"))
    return path if ok and os.access(path, os.W_OK | os.X_OK) else None


class Cg:
    """One cgroup per command. Every descendant stays inside, also after setsid or a double fork."""

    def __init__(self, root, cid):
        self.path = os.path.join(root, f"deck-cmd-{cid}")
        os.mkdir(self.path, 0o700)
        self.procs_fd = os.open(os.path.join(self.path, "cgroup.procs"), os.O_WRONLY | os.O_CLOEXEC)

    def enter(self):
        """Run in the child between fork and exec: write 0 to move the calling process."""
        os.write(self.procs_fd, b"0")

    def pids(self):
        try:
            with open(os.path.join(self.path, "cgroup.procs")) as f:
                return [int(x) for x in f.read().split()]
        except OSError:
            return []

    def populated(self):
        try:
            with open(os.path.join(self.path, "cgroup.events")) as f:
                return "populated 1" in f.read()
        except OSError:
            return False

    def signal(self, sig):
        for pid in self.pids():
            try:
                os.kill(pid, sig)
            except OSError:
                pass

    def kill(self):
        try:
            with open(os.path.join(self.path, "cgroup.kill"), "w") as f:
                f.write("1")
        except OSError:
            self.signal(signal.SIGKILL)

    def wait_empty(self, timeout, leader=None):
        end = time.monotonic() + timeout
        while True:
            if leader is not None:
                leader.poll()
            if not self.populated():
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.02)

    def close(self):
        os.close(self.procs_fd)
        try:
            os.rmdir(self.path)
        except OSError:
            pass


def desktop_env(uid=None, runtime=None):
    """Session variables found on disk. The model never supplies them."""
    uid = os.getuid() if uid is None else uid
    r = runtime or f"/run/user/{uid}"
    env = {}

    def sock(name):
        try:
            st = os.stat(os.path.join(r, name))
            return stat.S_ISSOCK(st.st_mode) and st.st_uid == uid
        except OSError:
            return False

    try:
        names = sorted(os.listdir(r))
    except OSError:
        return env
    env["XDG_RUNTIME_DIR"] = r
    wl = [n for n in names if re.fullmatch(r"wayland-\d+", n) and sock(n)]
    sw = [n for n in names if re.fullmatch(r"sway-ipc\.\d+\.\d+\.sock", n) and sock(n)]
    if wl:
        env["WAYLAND_DISPLAY"] = wl[0]
    if sw:
        env["SWAYSOCK"] = os.path.join(r, sw[0])
    if sock("bus"):
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={os.path.join(r, 'bus')}"
    return env


def write_json_atomic(path, obj):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def scan_artifacts(outdir):
    """Register regular files directly inside outdir. Returns (artifacts, rejected). Never follows symlinks."""
    arts, rejected = [], []
    try:
        dfd = os.open(outdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return arts, rejected
    try:
        for name in sorted(os.listdir(dfd)):
            if not NAME_RE.match(name):
                rejected.append({"name": name[:64], "reason": "bad_name"})
                continue
            if len(arts) >= MAX_ARTIFACTS:
                rejected.append({"name": name, "reason": "too_many"})
                continue
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
            except OSError as e:
                rejected.append({"name": name, "reason": "symlink" if e.errno == errno.ELOOP else "unreadable"})
                continue
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    rejected.append({"name": name, "reason": "not_regular"})
                elif st.st_uid != os.geteuid():
                    rejected.append({"name": name, "reason": "wrong_owner"})
                elif st.st_size > MAX_ARTIFACT_BYTES:
                    rejected.append({"name": name, "reason": "too_large"})
                else:
                    h = hashlib.sha256()
                    n = 0
                    while chunk := os.read(fd, 1 << 20):
                        h.update(chunk)
                        n += len(chunk)
                    arts.append({"handle": f"a{len(arts)}", "name": name, "size": n, "sha256": h.hexdigest(),
                                 "ino": st.st_ino, "mtime_ns": st.st_mtime_ns})
            finally:
                os.close(fd)
    finally:
        os.close(dfd)
    return arts, rejected


# ---------- connection ----------

class Session:
    """One host connection. The epoch is set by hello and fences every later message."""

    def __init__(self, fd):
        self.fd = fd
        self.epoch = None
        self.dead = False
        self.wlock = threading.Lock()

    def send(self, msg):
        data = encode_frame(msg)
        with self.wlock:
            if self.dead:
                return False
            end = time.monotonic() + 2.0
            view = memoryview(data)
            while view:
                left = end - time.monotonic()
                if left <= 0 or not select.select([], [self.fd], [], left)[1]:
                    self.dead = True  # the host is not reading; drop. The receipt is the record.
                    return False
                try:
                    view = view[os.write(self.fd, view[:4096]):]
                except OSError as e:
                    if e.errno in (errno.EAGAIN, errno.EINTR):
                        continue
                    self.dead = True
                    return False
        return True


class Command:
    def __init__(self):
        self.cancel = threading.Event()
        self.grace = 2.0


class Executor:
    def __init__(self, state_dir, workspace, cgroups=True, runtime=None):
        self.state = os.path.abspath(state_dir)
        self.cg_root = cgroup_root() if cgroups else None
        self.runtime = runtime
        self.workspace = os.path.abspath(workspace)
        self.receipts_dir = os.path.join(self.state, "receipts")
        self.spool_dir = os.path.join(self.state, "spool")
        self.out_root = os.path.join(self.workspace, "out")
        for d in (self.receipts_dir, self.spool_dir, self.out_root, os.path.join(self.workspace, "work")):
            os.makedirs(d, mode=0o700, exist_ok=True)
        self.lock = threading.RLock()
        self.running = {}
        self.session = None
        with open("/proc/sys/kernel/random/boot_id") as f:
            self.boot_id = f.read().strip()
        self.ident = None
        p = os.path.join(self.state, "identity.json")
        if os.path.exists(p):
            with open(p) as f:
                self.ident = json.load(f)
            self.ident.setdefault("max_epoch", 0)
        self.recover()

    # --- durable state ---

    def _rpath(self, command_id):
        return os.path.join(self.receipts_dir, f"{command_id}.json")

    def load(self, command_id):
        try:
            with open(self._rpath(command_id)) as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def save(self, rc):
        write_json_atomic(self._rpath(rc["command_id"]), rc)

    def recover(self):
        """A receipt still marked running belongs to a process this executor cannot supervise any more."""
        for fn in sorted(os.listdir(self.receipts_dir)):
            if not fn.endswith(".json"):
                continue
            rc = self.load(fn[:-5])
            if not rc or rc["state"] != "running":
                continue
            confirmed = True
            pgid = rc.get("pgid")
            if pgid and rc.get("boot_id") == self.boot_id and group_members(pgid):
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    pass
                confirmed = wait_group_gone(pgid, 3.0)
            rc.update(state="interrupted", finished_at=time.time(), detail="executor_restarted", group_confirmed=confirmed)
            self.save(rc)

    def _save_ident(self):
        write_json_atomic(os.path.join(self.state, "identity.json"), self.ident)

    # --- message handling ---

    def err(self, s, m, code, message="", **extra):
        s.send({"type": "error", "id": m.get("id"), "epoch": s.epoch, "code": code, "message": message, **extra})

    def handle(self, s, m):
        t = m.get("type")
        if t == "hello":
            return self.hello(s, m)
        if s.epoch is None:
            return self.err(s, m, "no_hello")
        if m.get("epoch") != s.epoch:
            return self.err(s, m, "stale_epoch", f"connection epoch is {s.epoch}", current=s.epoch)
        fn = {"exec": self.exec_, "status": self.status, "cancel": self.cancel, "artifact_get": self.artifact_get}.get(t)
        if not fn:
            return self.err(s, m, "unknown_type")
        try:
            fn(s, m)
        except ProtocolError as e:
            self.err(s, m, "bad_request", str(e))

    def hello(self, s, m):
        if self.ident is None:
            return self.err(s, m, "unprovisioned")
        if m.get("proto") != PROTO:
            return self.err(s, m, "unsupported_version")
        if m.get("bot_id") != self.ident["bot_id"]:
            return self.err(s, m, "wrong_bot")
        ep, nonce = m.get("epoch"), m.get("nonce")
        if not isinstance(ep, int) or isinstance(ep, bool) or ep < 1 or not isinstance(nonce, str) or len(nonce) > 128:
            return self.err(s, m, "bad_request", "epoch or nonce invalid")
        with self.lock:
            before = self.ident["max_epoch"]
            if ep <= before:
                return self.err(s, m, "stale_epoch", f"guest has seen epoch {before}", current=before)
            self.ident["max_epoch"] = ep
            self._save_ident()
            s.epoch = ep
            self.session = s
        s.send({"type": "hello_ack", "id": m.get("id"), "proto": PROTO, "bot_id": self.ident["bot_id"],
                "guest_id": self.ident["guest_id"], "epoch": ep, "nonce": nonce, "max_epoch_before": before,
                "boot_id": self.boot_id, "limits": LIMITS})

    @staticmethod
    def public(rc):
        return {k: v for k, v in rc.items() if k not in ("pgid", "boot_id")}

    def exec_(self, s, m):
        argv, deadline = m.get("argv"), m.get("deadline_ms")
        cid, tid = m.get("command_id"), m.get("task_id")
        if not (isinstance(cid, str) and ID_RE.match(cid) and isinstance(tid, str) and ID_RE.match(tid)):
            raise ProtocolError("bad command or task id")
        if m.get("bot_id") != self.ident["bot_id"]:
            return self.err(s, m, "wrong_bot")
        if not (isinstance(argv, list) and 1 <= len(argv) <= MAX_ARGV and all(isinstance(a, str) and "\0" not in a and len(a.encode()) <= MAX_ARG_BYTES for a in argv) and argv[0]):
            raise ProtocolError("bad argv")
        if not (isinstance(deadline, int) and not isinstance(deadline, bool) and 1 <= deadline <= MAX_DEADLINE_MS):
            raise ProtocolError("deadline out of range")
        digest = exec_digest(self.ident["bot_id"], tid, cid, deadline, argv)
        if m.get("digest") != digest:
            return self.err(s, m, "bad_digest", command_id=cid)
        with self.lock:
            rc = self.load(cid)
            if rc:
                if rc["digest"] != digest:
                    return self.err(s, m, "payload_conflict", "command ID reused with different arguments", command_id=cid)
                return s.send({"type": "exec_ack", "id": m.get("id"), "epoch": s.epoch, "command_id": cid,
                               "state": rc["state"], "duplicate": True, "receipt": self.public(rc)})
            if len(self.running) >= MAX_RUNNING:
                return self.err(s, m, "busy", command_id=cid)
            rc = {"command_id": cid, "task_id": tid, "bot_id": self.ident["bot_id"], "digest": digest, "state": "running",
                  "exec_epoch": s.epoch, "started_at": time.time(), "boot_id": self.boot_id, "deadline_ms": deadline,
                  "cancel": {"requested_at": None, "accepted": False, "confirmed": False}}
            self.save(rc)  # durable before the process exists
            cmd = self.running[cid] = Command()
            threading.Thread(target=self.run_command, args=(rc, argv, cmd), daemon=True).start()
        s.send({"type": "exec_ack", "id": m.get("id"), "epoch": s.epoch, "command_id": cid, "state": "running",
                "duplicate": False, "receipt": self.public(rc)})

    def status(self, s, m):
        cid = m.get("command_id")
        if not (isinstance(cid, str) and ID_RE.match(cid)):
            raise ProtocolError("bad command id")
        rc = self.load(cid)
        s.send({"type": "status_reply", "id": m.get("id"), "epoch": s.epoch, "command_id": cid, "found": rc is not None,
                "receipt": self.public(rc) if rc else None})

    def cancel(self, s, m):
        cid = m.get("command_id")
        if not (isinstance(cid, str) and ID_RE.match(cid)):
            raise ProtocolError("bad command id")
        with self.lock:
            rc = self.load(cid)
            if rc is None:
                return s.send({"type": "cancel_ack", "id": m.get("id"), "epoch": s.epoch, "command_id": cid, "ack": "unknown_command"})
            if rc["state"] != "running" or cid not in self.running:
                return s.send({"type": "cancel_ack", "id": m.get("id"), "epoch": s.epoch, "command_id": cid,
                               "ack": "already_terminal", "state": rc["state"]})
            rc["cancel"].update(requested_at=rc["cancel"]["requested_at"] or time.time(), accepted=True)
            self.save(rc)
            g = m.get("grace_ms", 2000)
            self.running[cid].grace = max(0, min(int(g), 10_000)) / 1000 if isinstance(g, int) else 2.0
            self.running[cid].cancel.set()
        # "accepted" means a stop was requested. Termination is confirmed only by a later result.
        s.send({"type": "cancel_ack", "id": m.get("id"), "epoch": s.epoch, "command_id": cid, "ack": "accepted", "state": "running"})

    def artifact_get(self, s, m):
        cid, handle, off, n = m.get("command_id"), m.get("handle"), m.get("offset"), m.get("length")
        if not (isinstance(cid, str) and ID_RE.match(cid) and isinstance(handle, str) and isinstance(off, int) and isinstance(n, int)
                and off >= 0 and 1 <= n <= CHUNK_MAX):
            raise ProtocolError("bad artifact request")
        rc = self.load(cid)
        art = next((a for a in (rc or {}).get("artifacts", []) if a["handle"] == handle), None)
        if not art:
            return self.err(s, m, "unknown_artifact", command_id=cid)
        try:
            dfd = os.open(os.path.join(self.out_root, cid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fd = os.open(art["name"], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
            finally:
                os.close(dfd)
        except OSError:
            return self.err(s, m, "artifact_changed", command_id=cid)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or (st.st_ino, st.st_size, st.st_mtime_ns) != (art["ino"], art["size"], art["mtime_ns"]):
                return self.err(s, m, "artifact_changed", command_id=cid)
            data = os.pread(fd, n, off)
        finally:
            os.close(fd)
        s.send({"type": "artifact_chunk", "id": m.get("id"), "epoch": s.epoch, "command_id": cid, "handle": handle,
                "offset": off, "data": base64.b64encode(data).decode(), "eof": off + len(data) >= art["size"]})

    # --- running a command ---

    def terminate(self, proc, grace, cg=None):
        """TERM the command, then KILL it. Returns True only when no live member remains.

        With a cgroup, every descendant counts, also one that left the process group.
        """
        pgid = proc.pid
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            pass
        if cg:
            cg.signal(signal.SIGTERM)
            if cg.wait_empty(grace, proc):
                return True
            cg.kill()
            return cg.wait_empty(3.0, proc)
        if wait_group_gone(pgid, grace, proc):
            return True
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
        return wait_group_gone(pgid, 3.0, proc)

    @staticmethod
    def pump(pipe, spool_path, box):
        """Drain a pipe so the child never blocks. Keep a bounded spool file and a bounded tail."""
        tail = collections.deque()
        tail_len = total = 0
        with open(spool_path, "wb") as spool:
            while chunk := os.read(pipe.fileno(), 65536):
                if total < SPOOL_BYTES:
                    spool.write(chunk[:SPOOL_BYTES - total])
                total += len(chunk)
                tail.append(chunk)
                tail_len += len(chunk)
                while tail_len - len(tail[0]) >= TAIL_BYTES:
                    tail_len -= len(tail.popleft())
        pipe.close()
        data = b"".join(tail)[-TAIL_BYTES:]
        box.update(bytes=total, tail=data.decode(errors="replace"), truncated=total > len(data))

    def run_command(self, rc, argv, cmd):
        cid = rc["command_id"]
        outdir = os.path.join(self.out_root, cid)
        os.makedirs(outdir, mode=0o700, exist_ok=True)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", self.workspace), "LANG": "C.UTF-8",
               **desktop_env(runtime=self.runtime),
               "DECK_OUT": outdir, "DECK_COMMAND_ID": cid, "DECK_TASK_ID": rc["task_id"], "DECK_WORKSPACE": self.workspace}
        cg = None
        if self.cg_root:
            try:
                cg = Cg(self.cg_root, cid)
            except OSError:
                cg = None  # an old cgroup of the same ID, or no permission: fall back to the process group
        rc["containment"] = "cgroup" if cg else "process_group"
        outs = {"out": {}, "err": {}}
        reason, exit_code, sig, confirmed, detail = None, None, None, True, None
        try:
            proc = subprocess.Popen(argv, cwd=os.path.join(self.workspace, "work"), env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
                                    pass_fds=(cg.procs_fd,) if cg else (), preexec_fn=cg.enter if cg else None)
        except OSError as e:
            proc = None
            reason, detail = "spawn_failed", f"spawn failed: {errno.errorcode.get(e.errno, e.errno)}"
        if proc:
            with self.lock:
                rc["pgid"] = proc.pid
                self.save(rc)
            pumps = [threading.Thread(target=self.pump, args=(proc.stdout, os.path.join(self.spool_dir, f"{cid}.out"), outs["out"]), daemon=True),
                     threading.Thread(target=self.pump, args=(proc.stderr, os.path.join(self.spool_dir, f"{cid}.err"), outs["err"]), daemon=True)]
            for t in pumps:
                t.start()
            deadline = time.monotonic() + rc["deadline_ms"] / 1000
            while True:
                try:
                    code = proc.wait(timeout=0.05)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if cmd.cancel.is_set():
                    reason = "cancelled"
                elif time.monotonic() >= deadline:
                    reason = "timed_out"
                if reason:
                    confirmed = self.terminate(proc, cmd.grace if reason == "cancelled" else 1.0, cg)
                    code = proc.poll()
                    break
            if reason is None:
                # The leader exited. Anything still in its group is left over from this command: stop it.
                left = cg.populated() if cg else bool(group_members(proc.pid))
                confirmed = not left or self.terminate(proc, 0.0, cg)
            if code is not None:
                exit_code, sig = (code, None) if code >= 0 else (None, -code)
            for t in pumps:
                t.join(3.0)
        if cg:
            cg.close()
        arts, rejected = scan_artifacts(outdir)
        with self.lock:
            if reason in ("cancelled", "timed_out"):
                state = reason if confirmed else "termination_unconfirmed"
            elif reason == "spawn_failed":
                state = "failed"
            elif not confirmed:
                state = "termination_unconfirmed"
            else:
                state = "succeeded" if exit_code == 0 else "failed"
            if os.path.basename(argv[0]) == "bare-call" and reason in ("cancelled", "timed_out"):
                # The bridge can stop while the persistent browser still completes its request.
                # Confirming bridge termination does not establish the external action's outcome.
                state, detail = "interrupted", "browser_outcome_unknown"
            if reason == "cancelled":
                rc["cancel"]["confirmed"] = bool(confirmed)
            rc.update(state=state, finished_at=time.time(), exit_code=exit_code, signal=sig, group_confirmed=bool(confirmed),
                      detail=detail, stdout=outs["out"].get("tail", ""), stderr=outs["err"].get("tail", ""),
                      stdout_bytes=outs["out"].get("bytes", 0), stderr_bytes=outs["err"].get("bytes", 0),
                      truncated=bool(outs["out"].get("truncated") or outs["err"].get("truncated")),
                      artifacts=arts, rejected_artifacts=rejected)
            self.save(rc)
            self.running.pop(cid, None)
            s = self.session
        if s:
            s.send({"type": "result", "epoch": s.epoch, "exec_epoch": rc["exec_epoch"], "command_id": cid, "receipt": self.public(rc)})

    # --- transport ---

    def serve(self, fd):
        """Read frames from one connection until EOF or a protocol error."""
        s = Session(fd)
        reader = FrameReader()
        try:
            while True:
                r = select.select([fd], [], [], 1.0)[0]
                if not r:
                    if reader.buf and time.monotonic() - reader.touched > STALL_S:
                        reader.reset()  # a half frame from a dropped connection
                    continue
                try:
                    data = os.read(fd, 65536)
                except OSError as e:
                    if e.errno in (errno.EAGAIN, errno.EINTR):
                        continue
                    return
                if not data:
                    return
                for m in reader.feed(data):
                    self.handle(s, m)
        except ProtocolError:
            return
        finally:
            s.dead = True
            with self.lock:
                if self.session is s:
                    self.session = None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", default=os.path.expanduser("~/.local/state/deck-executor"))
    ap.add_argument("--workspace", default=os.path.expanduser("~/workspace"))
    ap.add_argument("--runtime-dir", help="desktop runtime directory (tests); default /run/user/UID")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--device", default=DEFAULT_DEVICE, help="virtio-serial port")
    g.add_argument("--listen-unix", help="serve a unix socket instead (tests)")
    a = ap.parse_args(argv)
    if os.geteuid() == 0:
        sys.exit("refusing to run as root")
    ex = Executor(a.state, a.workspace, runtime=a.runtime_dir)
    if a.listen_unix:
        srv = socket.socket(socket.AF_UNIX)
        if os.path.exists(a.listen_unix):
            os.unlink(a.listen_unix)
        srv.bind(a.listen_unix)
        os.chmod(a.listen_unix, 0o600)
        srv.listen(1)
        print("ready", flush=True)
        while True:
            c, _ = srv.accept()
            ex.serve(c.fileno())
            c.close()
    else:
        while not os.path.exists(a.device):
            time.sleep(0.5)
        fd = os.open(a.device, os.O_RDWR | os.O_CLOEXEC)
        while True:
            ex.serve(fd)
            time.sleep(0.2)  # EOF: the host closed. Wait for the next connection.


if __name__ == "__main__":
    main()
