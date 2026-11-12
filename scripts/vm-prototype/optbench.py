#!/usr/bin/env python3
"""Measurements for the VM optimizations that need a running guest. Standard library only.

  isolation  two overlays on one frozen base: private markers, base digest, total storage
  capture    one-shot capture audit and a fixed-frame comparison of capture and suppression costs

Both use the synthetic fixture only and stop every VM and helper they start.
"""
import argparse
import hashlib
import json
import struct
import time
import zlib
from pathlib import Path

import stage1 as s1
import vmproto as v


def boot(p, port):
    pid = v.start_qemu(p, "run", port)
    g = s1.Guest(p)
    g.q.ping_ready(120)
    g.desktop_ready()
    return g, pid


def present(g, *paths):
    return {x: g.sh(f"test -e {x} && echo y || echo n")[1].strip() == "y" for x in paths}


def write_private(g, p, tag):
    """A guest-private file, pulled to this runtime's own artifact store."""
    g.sh(f"printf %s private-{tag} > /home/deck/private-{tag}; sync")
    p.art.mkdir(parents=True, exist_ok=True)
    got = g.q.pull(f"/home/deck/private-{tag}", str(p.art / f"private-{tag}"))
    return got == hashlib.sha256(f"private-{tag}".encode()).hexdigest()


def isolation(a):
    pa, pb = v.Paths(a.dir_a), v.Paths(a.dir_b)
    base = Path(v.read_json(pa.diskmeta)["base"]["path"])
    digest = v.read_json(base.with_suffix(".json"))["sha256"]
    out = {"base_id": base.stem, "base_digest_before": v.sha256_file(base) == digest}
    s1.fixture_start(pa, a.fixture_port, a.fixture_port + 1)
    try:
        stage1_files = ("/home/deck/disk-marker", "/home/deck/shell-created.txt", "/home/deck/Downloads/report.csv")
        g, _ = boot(pb, a.fixture_port)
        out["B_first_boot_has_A_files"] = present(g, *stage1_files, "/home/deck/private-A")
        out["B_artifact_ok"] = write_private(g, pb, "B")
        g.q.sh("true")
        out["B_save"] = v.save_vm(pb, qga=g.q)
        g, _ = boot(pa, a.fixture_port)
        out["A_has_own_files_and_not_B"] = present(g, *stage1_files, "/home/deck/private-B")
        out["A_artifact_ok"] = write_private(g, pa, "A")
        g.q.sh("true")
        out["A_save"] = v.save_vm(pa, qga=g.q)
        # A's record against B's state must be refused before any QEMU starts
        keep = pb.statemeta.read_text()
        pb.statemeta.write_text(pa.statemeta.read_text())
        try:
            v.restore_vm(pb, a.fixture_port)
            out["mismatched_restore_refused"] = False
        except SystemExit as e:
            out["mismatched_restore_refused"] = "does not match" in str(e)
        pb.statemeta.write_text(keep)
        r = v.restore_vm(pb, a.fixture_port)
        g = s1.Guest(pb)
        out["B_restored"] = {"to_usable_s": r["to_usable_s"], **present(g, "/home/deck/private-B", "/home/deck/private-A")}
        out["B_save_again"] = v.save_vm(pb, qga=g.q)
        r = v.restore_vm(pa, a.fixture_port)
        g = s1.Guest(pa)
        out["A_restored"] = {"to_usable_s": r["to_usable_s"], **present(g, "/home/deck/private-A", "/home/deck/private-B")}
        out["A_save_again"] = v.save_vm(pa, qga=g.q)
        out["artifacts_separate"] = sorted(f.name for f in pa.art.glob("private-*")) == ["private-A"] and sorted(f.name for f in pb.art.glob("private-*")) == ["private-B"]
        out["storage_with_states"] = v.storage_report(pa, pb)
    finally:
        v.kill_vm(pa)
        v.kill_vm(pb)
        s1.fixture_stop(pa)
    out["base_digest_after"] = v.sha256_file(base) == digest
    for p in (pa, pb):
        p.statefile.unlink(missing_ok=True)
        p.statemeta.unlink(missing_ok=True)
    out["storage_without_states"] = v.storage_report(pa, pb)
    out["base_mode_octal"] = oct(base.stat().st_mode & 0o777)
    print(json.dumps(out, indent=1))
    Path(a.out).write_text(json.dumps(out, indent=1))


def png(ppm):
    """Encode a binary PPM (P6) as PNG with the standard library, level 1."""
    _, dims, _, data = ppm.split(b"\n", 3)  # "P6\nW H\n255\n" then RGB bytes
    w, h = map(int, dims.split())
    raw = b"".join(b"\0" + data[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b"")


def cpu_now(cg):
    return (v.cpu_usec(cg) or 0) / 1e6, time.process_time()


def capture(a):
    p = v.Paths(a.dir)
    s1.fixture_start(p, a.fixture_port, a.fixture_port + 1)
    out = {}
    try:
        g, pid = boot(p, a.fixture_port)
        cg = v.cgroup_dir(pid)
        g.sh("rm -f /tmp/desktop-*.png")
        g.deck("swaymsg '[app_id=foot] focus'")
        # Audit: with nobody asking, nothing captures. No child process, no guest capture process, flat CPU.
        time.sleep(a.settle_s)
        q0, h0 = cpu_now(cg)
        time.sleep(a.idle_s)
        q1, h1 = cpu_now(cg)
        kids = [x for x in v.tree_pids(pid) if x != pid]
        out["idle_audit"] = {"seconds": a.idle_s, "vm_cpu_s": round(q1 - q0, 3), "host_helper_cpu_s": round(h1 - h0, 3),
                             "qemu_child_processes": len(kids), "guest_capture_processes": g.sh("pgrep -c -x 'grim|wf-recorder|wayvnc' || true")[1].strip()}
        # Fixed frames: frames 0..N-1 at a fixed pace; one character is typed before frames in `changes`.
        changes = {int(x) for x in a.changes.split(",")}
        q = v.Qmp(p.qmp)
        out["rounds"] = []
        for rnd in range(a.rounds):
            res = {}
            for mode in ("no-capture-control", "png-every-frame", "png-hash-suppress", "ppm-hash-suppress"):
                tmp = p.run / "frame"
                last, sent, sent_bytes, lat = None, 0, 0, []
                qa, ha = cpu_now(cg)
                t_start = time.time()
                for i in range(a.frames):
                    if i in changes:
                        g.deck(f"wtype {'xyz'[len(changes) % 3]}")
                        time.sleep(0.3)
                    t = time.time()
                    if mode == "no-capture-control":
                        pass
                    elif mode.startswith("png"):
                        q.cmd("screendump", filename=str(tmp.with_suffix(".png")), format="png")
                        data = tmp.with_suffix(".png").read_bytes()
                        digest = hashlib.sha256(data).digest()
                        if mode == "png-every-frame" or digest != last:
                            sent, sent_bytes, last = sent + 1, sent_bytes + len(data), digest
                    else:
                        q.cmd("screendump", filename=str(tmp.with_suffix(".ppm")), format="ppm")
                        raw = tmp.with_suffix(".ppm").read_bytes()
                        digest = hashlib.sha256(raw).digest()
                        if digest != last:
                            sent, sent_bytes, last = sent + 1, sent_bytes + len(png(raw)), digest
                    lat.append(time.time() - t)
                    time.sleep(max(0, a.interval - (time.time() - t)))
                qb, hb = cpu_now(cg)
                elapsed = time.time() - t_start
                res[mode] = {"frames_captured": 0 if mode == "no-capture-control" else a.frames, "frames_transmitted": sent, "bytes_transmitted": sent_bytes,
                             "vm_cpu_s": round(qb - qa, 3),
                             "host_helper_cpu_s": round(hb - ha, 3), "elapsed_s": round(elapsed, 2),
                             "latency_s_median": round(sorted(lat)[len(lat) // 2], 3), "latency_s_max": round(max(lat), 3)}
                g.deck("wtype -k BackSpace -k BackSpace -k BackSpace")  # same start frame for the next mode
                time.sleep(0.3)
            for m in res:  # the control window shares the typing and the guest's own activity
                res[m]["vm_cpu_s_minus_control"] = round(res[m]["vm_cpu_s"] - res["no-capture-control"]["vm_cpu_s"], 3)
            out["rounds"].append(res)
        q.close()
        # After the measurements nothing may keep running that was not there before.
        out["after_audit"] = {"qemu_child_processes": len([x for x in v.tree_pids(pid) if x != pid]),
                              "guest_capture_processes": g.sh("pgrep -c -x 'grim|wf-recorder|wayvnc' || true")[1].strip()}
    finally:
        v.kill_vm(p)
        s1.fixture_stop(p)
    print(json.dumps(out, indent=1))
    Path(a.out).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("isolation")
    s.add_argument("--dir-a", required=True)
    s.add_argument("--dir-b", required=True)
    s.set_defaults(f=isolation)
    s = sub.add_parser("capture")
    s.add_argument("--dir", required=True)
    s.add_argument("--frames", type=int, default=24)
    s.add_argument("--interval", type=float, default=0.5)
    s.add_argument("--changes", default="6,12,18", help="frame numbers that follow a typed character")
    s.add_argument("--idle-s", type=int, default=30)
    s.add_argument("--settle-s", type=int, default=45, help="wait after boot before the idle audit")
    s.add_argument("--rounds", type=int, default=3)
    s.set_defaults(f=capture)
    for s in sub.choices.values():
        s.add_argument("--fixture-port", type=int, default=18180)
        s.add_argument("--out", required=True, help="results file (outside Git)")
    a = ap.parse_args()
    a.f(a)
