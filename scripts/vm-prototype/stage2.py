#!/usr/bin/env python3
"""Stage 2 smoke test: one VM, one serial scenario through the Rust `deck executor` developer CLI.

Needs a frozen base (see vmproto.py freeze-base) and `cargo build`. It creates a private overlay in the
runtime directory, boots it, binds the guest executor to one bot, runs the scenario, saves and restores,
then kills and cold boots to check a lost in-flight command. Synthetic data only. No model calls.
It writes results.json to the runtime directory and always kills the VM it started.
"""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import vmproto as v

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BOT = "bot-a"
sys.path.insert(0, str(HERE / "guest"))
import deck_executor as de  # noqa: E402


class Smoke:
    def __init__(self, a):
        self.p = v.Paths(a.dir)
        self.port = a.fixture_port
        self.deck = a.deck
        self.data = self.p.root / "host-data"
        self.results = {"steps": {}}
        self.guest_id = None

    def common(self):
        vm = v.read_vm(self.p)
        return ["--bot", BOT, "--endpoint", str(self.p.exec), "--data-dir", str(self.data), "--peer-pid", str(vm["pid"]),
                "--guest-id", self.guest_id, "--timeout-secs", "15"]

    def deck_cmd(self, sub, *args, rest=(), timeout=90):
        cmd = [self.deck, "executor", sub, *self.common(), *args]
        if rest:
            cmd += ["--", *rest]
        t = time.monotonic()
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        out = None
        for line in reversed(r.stdout.splitlines()):
            try:
                out = json.loads(line)
                break
            except ValueError:
                pass
        epoch = None
        for line in r.stderr.splitlines():
            if line.startswith("connected: epoch"):
                epoch = {"epoch": int(line.split()[2].rstrip(",")), "max_epoch_before": int(line.split("before")[1].split(",")[0])}
        return {"rc": r.returncode, "json": out, "epoch": epoch, "stderr": r.stderr.strip()[-300:], "wall_s": round(time.monotonic() - t, 2)}

    def journal(self):
        r = subprocess.run([self.deck, "executor", "show", "--bot", BOT, "--data-dir", str(self.data)], capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout)

    def guest(self, script):
        return v.Qga(self.p.qga).sh(script, timeout=30)

    def step(self, name, **facts):
        self.results["steps"][name] = facts
        print(name, json.dumps(facts, default=str), flush=True)

    def ok(self, cond, what):
        if not cond:
            raise SystemExit(f"FAILED: {what}")

    def boot(self):
        v.start_qemu(self.p, "run", self.port)
        v.Qga(self.p.qga).ping_ready(90)

    def run(self, a):
        p = self.p
        p.mk()
        v.create_overlay(p, a.base)
        self.boot()
        info = v.install_executor(p, BOT)
        self.guest_id = info["guest_id"]
        r = self.deck_cmd("reconcile")
        self.ok(r["rc"] == 0 and r["epoch"]["epoch"] == 1, "first connect")
        self.step("connect", epoch=r["epoch"], bound_bot=BOT)

        # 1. A command as the desktop user, an artifact, and a guest side effect.
        script = ("echo x >> $HOME/workspace/side-effects; id -un > $DECK_OUT/who.txt; id -u > $DECK_OUT/uid.txt; "
                  "head -c 200000 /dev/urandom > $DECK_OUT/blob.bin; echo done")
        c1 = ["--id", "s2-c1", "--task", "s2", "--deadline-secs", "30"]
        r = self.deck_cmd("run", *c1, rest=["/bin/sh", "-c", script])
        j = r["json"]
        self.ok(r["rc"] == 0 and j["state"] == "succeeded" and j["stdout"].strip() == "done", f"c1 {r}")
        f = self.deck_cmd("fetch", "--id", "s2-c1")
        files = {Path(x).name: Path(x) for x in f["json"]["artifacts"]}
        guest_sha = self.guest("sha256sum /home/deck/workspace/out/s2-c1/blob.bin")[1].split()[0]
        host_sha = hashlib.sha256(files["blob.bin"].read_bytes()).hexdigest()
        who = files["who.txt"].read_text().strip()
        self.ok(host_sha == guest_sha and who == "deck" and files["uid.txt"].read_text().strip() != "0", "artifact or user")
        store_ok = all(str(x).startswith(str(self.data / "bots" / BOT / "artifacts" / "s2-c1")) for x in files.values())
        self.step("run_and_artifacts", state=j["state"], ran_as=who, artifact_names=sorted(files), checksum_match=host_sha == guest_sha,
                  stored_under_bot_store=store_ok, epoch=r["epoch"])

        # 2. A long command: detach, query from a new connection, cancel.
        r = self.deck_cmd("run", "--id", "s2-long", "--task", "s2", "--deadline-secs", "120", "--wait-secs", "0",
                          rest=["/bin/sh", "-c", "sleep 300 & echo $! > $HOME/workspace/child.pid; wait"])
        self.ok(r["json"]["state"] in ("sent", "running"), f"long {r}")
        time.sleep(1)
        st = self.deck_cmd("status", "--id", "s2-long")
        self.ok(st["json"]["state"] == "running", f"status {st}")
        child = int(self.guest("cat /home/deck/workspace/child.pid")[1])
        proc_user = self.guest(f"ps -o user= -p {child}")[1].strip()
        t = time.monotonic()
        cx = self.deck_cmd("cancel", "--id", "s2-long", "--wait-secs", "15")
        took = round(time.monotonic() - t, 2)
        gone = self.guest(f"kill -0 {child} 2>/dev/null && echo alive || echo gone")[1].strip()
        self.ok(cx["json"]["state"] == "cancelled" and cx["json"]["cancel_state"] == "confirmed" and gone == "gone", f"cancel {cx}")
        self.step("status_and_cancel", status_while_running=st["json"]["state"], status_epoch=st["epoch"], child_user=proc_user,
                  cancel_request_to_confirmed_s_single_sample=took, final=cx["json"]["state"], cancel_state=cx["json"]["cancel_state"],
                  child_after_cancel=gone)

        # 3. A deadline stops the process group too.
        r = self.deck_cmd("run", "--id", "s2-dl", "--task", "s2", "--deadline-secs", "2",
                          rest=["/bin/sh", "-c", "sleep 300 & sleep 300"])
        self.ok(r["json"]["state"] == "timed_out", f"deadline {r}")
        left = self.guest("pgrep -u deck -f 'sleep 300' || echo none")[1].strip()
        self.ok(left == "none", "deadline left a process")
        self.step("deadline", state=r["json"]["state"], processes_left=left)

        # 4. Replay and conflict: no second side effect.
        r = self.deck_cmd("run", *c1, rest=["/bin/sh", "-c", script])
        r2 = self.deck_cmd("run", *c1, rest=["/bin/sh", "-c", "echo changed"])
        lines = int(self.guest("wc -l < /home/deck/workspace/side-effects")[1])
        self.ok(r["json"]["state"] == "succeeded" and lines == 1 and r2["rc"] != 0 and "payload conflict" in r2["stderr"], f"replay {r} {r2}")
        self.step("replay", state=r["json"]["state"], side_effect_lines=lines, conflict_rejected=r2["rc"] != 0, epoch=r["epoch"])

        # 5. The guest rejects a stale hello and a wrong bot, using the raw protocol.
        raw = self.raw_probe()
        self.step("raw_fencing", **raw)

        # 6. Save and restore: epochs advance; the old outcome is returned; nothing runs again.
        pending = [c["command_id"] for c in self.journal()["commands"] if c["state"] in ("intent", "sent", "running")]
        self.ok(not pending, f"not quiescent: {pending}")
        before = self.deck_cmd("reconcile")["epoch"]
        save = v.save_vm(self.p)
        restore = v.restore_vm(self.p, self.port)
        r = self.deck_cmd("run", *c1, rest=["/bin/sh", "-c", script])
        lines = int(self.guest("wc -l < /home/deck/workspace/side-effects")[1])
        after = self.deck_cmd("run", "--id", "s2-after", "--task", "s2", "--deadline-secs", "20", rest=["/bin/echo", "after-restore"])
        self.ok(r["json"]["state"] == "succeeded" and lines == 1 and r["epoch"]["epoch"] > before["epoch"], f"restore {r}")
        self.ok(r["epoch"]["max_epoch_before"] == before["epoch"] and after["json"]["state"] == "succeeded", "restore fence")
        self.step("save_restore", epoch_before_save=before, epoch_after_restore=r["epoch"], prior_outcome=r["json"]["state"],
                  side_effect_lines=lines, command_after_restore=after["json"]["state"], save_total_s=save["total_s"], restore_to_usable_s=restore["to_usable_s"])

        # 7. Kill the VM with a command in flight; cold boot; the outcome is unknown and is not retried.
        r = self.deck_cmd("run", "--id", "s2-crash", "--task", "s2", "--deadline-secs", "120", "--wait-secs", "0",
                          rest=["/bin/sh", "-c", "echo x >> $HOME/workspace/crash-effects; sync; sleep 300"])
        time.sleep(1)
        v.kill_vm(self.p)
        self.boot()
        rc = self.deck_cmd("reconcile")
        again = self.deck_cmd("run", "--id", "s2-crash", "--task", "s2", "--deadline-secs", "120", "--wait-secs", "0",
                              rest=["/bin/sh", "-c", "echo x >> $HOME/workspace/crash-effects; sync; sleep 300"])
        effects = int(self.guest("wc -l < /home/deck/workspace/crash-effects")[1])
        left = self.guest("pgrep -u deck -f 'sleep 300' || echo none")[1].strip()
        self.ok(again["json"]["state"] == "unknown" and effects == 1 and left == "none", f"crash {again}")
        self.step("kill_and_cold_boot", state_after_reconcile=again["json"]["state"], detail=again["json"]["detail"], epoch=rc["epoch"],
                  effect_lines_not_repeated=effects, processes_left=left)
        shown = self.journal()
        self.results["journal_final"] = {c["command_id"]: [c["state"], c["detail"], c["cancel_state"]] for c in shown["commands"]}
        self.results["journal_event_kinds"] = sorted({e["kind"] for e in shown["events"]})

    def raw_probe(self):
        """Talk to the executor socket directly, as a host with a wrong or stale identity would."""
        out = {}
        for name, bot, epoch in (("stale_epoch", BOT, 1), ("wrong_bot", "other-bot", 10_000)):
            s = socket.socket(socket.AF_UNIX)
            s.settimeout(10)
            s.connect(str(self.p.exec))
            s.sendall(de.encode_frame({"type": "hello", "id": "h", "proto": 1, "bot_id": bot, "epoch": epoch, "nonce": "n"}))
            rd, code = de.FrameReader(), None
            while code is None:
                for m in rd.feed(s.recv(65536)):
                    if m.get("type") == "error":
                        code = m["code"]
            s.close()
            out[name] = code
            self.ok(code == name, f"raw {name}: {code}")
        return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(v.default_dir()), help="runtime directory outside Git (default: $DECK_VM_DIR)")
    ap.add_argument("--base", required=True, help="frozen base .qcow2")
    ap.add_argument("--deck", default=str(ROOT / "target/debug/deck"))
    ap.add_argument("--fixture-port", type=int, default=28931)
    a = ap.parse_args()
    s = Smoke(a)
    code = 0
    try:
        s.run(a)
        s.results["result"] = "PASS"
    except BaseException as e:  # noqa: BLE001
        s.results["result"] = f"FAIL: {e}"
        code = 1
    finally:
        killed = v.kill_vm(s.p)
        s.results["vm_killed_at_end"] = killed
        (s.p.root / "results.json").write_text(json.dumps(s.results, indent=1, default=str))
        print(s.results["result"])
    sys.exit(code)


if __name__ == "__main__":
    main()
