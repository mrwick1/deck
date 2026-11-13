"""Guest secret channel: deliver one protected value into a bot's guest and reconcile its receipt.

The value travels only on the stdin of one guest command (QGA input-data). It is never in argv,
never in a shell string and never in an error message. Every result is metadata: a stable error code or
{ok, bot_id, request_id, label}. Standard library only.

Guest storage (user deck):
  ~/.config/deck/secrets.env           NAME=value lines, mode 0600, directory 0700. bare-call hands it to bare.
  ~/.local/state/deck-secrets/receipts one JSON file per request, mode 0600, directory 0700.
A receipt holds the bot, request, env name and a salted scrypt digest of the value. The digest stays on the guest.
It lets a replay with a changed value fail and lets a crashed delivery reconcile. It is never printed.
"""
import hashlib
import json
import re
import sys

MAX_VALUE = 4096
REQUEST_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")


class SecretError(Exception):
    """Carries a stable code only. The message never includes a value or guest output."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def env_name(label):
    if not isinstance(label, str) or not 1 <= len(label) <= 200 or "\x00" in label:
        raise SecretError("invalid_label")
    if NAME_RE.fullmatch(label):
        return label
    return "DECK_" + hashlib.sha256(label.encode()).hexdigest()[:16].upper()


def check_value(value):
    if not isinstance(value, str) or not value or any(c in value for c in "\x00\r\n"):
        raise SecretError("invalid_value")
    if len(value.encode()) > MAX_VALUE:
        raise SecretError("value_too_large")


def check_request(request_id):
    if not isinstance(request_id, str) or not REQUEST_RE.fullmatch(request_id):
        raise SecretError("invalid_request_id")


# Runs in the guest as user deck: python3 -c GUEST MODE BOT REQUEST. Stdin is JSON {name, value} for deliver.
GUEST = r'''
import fcntl, hashlib, hmac, json, os, re, secrets, stat, sys

mode, bot, rid = sys.argv[1:4]
home = os.path.realpath(os.path.expanduser("~"))
cfg = os.path.join(home, ".config", "deck")
state = os.path.join(home, ".local", "state", "deck-secrets")
rdir = os.path.join(state, "receipts")
os.umask(0o077)

def fail(code):
    print(json.dumps({"ok": False, "error": code}))
    sys.exit(1)

def private_dir(path, tighten=False):
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or os.path.realpath(path) != path:
        fail("unsafe_path")
    if tighten:
        os.chmod(path, 0o700)

def check_file(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        fail("unsafe_path")
    return True

def write_atomic(path, text):
    tmp = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise
    d = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        os.fsync(d)
    finally:
        os.close(d)

def digest(salt, value):
    return hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()

def env_lines(path):
    if not check_file(path):
        return []
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()

def env_value(name):
    for line in env_lines(os.path.join(cfg, "secrets.env")):
        if line.startswith(name + "="):
            return line[len(name) + 1:]
    return None

def set_env(name, value):
    path = os.path.join(cfg, "secrets.env")
    lines = [l for l in env_lines(path) if not l.startswith(name + "=")]
    write_atomic(path, "".join(l + "\n" for l in lines) + f"{name}={value}\n")

def ok(r):
    print(json.dumps({"ok": True, "bot_id": bot, "request_id": rid, "label": r["name"]}))

try:
    ident = os.path.join(home, ".local", "state", "deck-executor", "identity.json")
    with open(ident) as f:
        if json.load(f).get("bot_id") != bot:
            fail("bot_mismatch")
except (OSError, ValueError):
    fail("bot_mismatch")
private_dir(os.path.join(home, ".config"))
private_dir(cfg, True)
private_dir(os.path.join(home, ".local", "state"))
private_dir(state, True)
private_dir(rdir, True)
lock = os.open(os.path.join(state, "lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
fcntl.flock(lock, fcntl.LOCK_EX)
rpath = os.path.join(rdir, rid + ".json")
rec = None
if check_file(rpath):
    with open(rpath) as f:
        rec = json.load(f)
    if rec.get("bot_id") != bot or rec.get("request_id") != rid:
        fail("request_mismatch")

def finish(rec):
    rec["status"] = "done"
    write_atomic(rpath, json.dumps(rec))
    ok(rec)

if mode == "receipt":
    if rec is None:
        fail("not_delivered")
    if rec["status"] == "done":
        ok(rec)
    else:
        cur = env_value(rec["name"])
        if cur is None or not hmac.compare_digest(digest(rec["salt"], cur), rec["digest"]):
            fail("not_delivered")
        finish(rec)
    sys.exit(0)

req = json.loads(sys.stdin.read(16384))
name, value = req["name"], req["value"]
if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", name) or not value or any(c in value for c in "\x00\r\n") or len(value.encode()) > 4096:
    fail("invalid_value")
if rec is not None:
    if rec["name"] != name or not hmac.compare_digest(digest(rec["salt"], value), rec["digest"]):
        fail("request_mismatch")
    if rec["status"] == "done":
        ok(rec)
        sys.exit(0)
else:
    salt = secrets.token_hex(16)
    rec = {"bot_id": bot, "request_id": rid, "name": name, "salt": salt, "digest": digest(salt, value), "status": "pending"}
    write_atomic(rpath, json.dumps(rec))
set_env(name, value)
finish(rec)
'''


def _run(qga, mode, bot_id, request_id, payload=None):
    argv = ["runuser", "-u", "deck", "--", "python3", "-c", GUEST, mode, bot_id, request_id]
    try:
        code, out, _ = qga.exec(argv, stdin=json.dumps(payload) if payload else None, timeout=60)
    except Exception:  # transport messages may echo the request; report a code only
        raise SecretError("transport_error") from None
    try:
        res = json.loads(out)
    except ValueError:
        raise SecretError("guest_error") from None
    if not isinstance(res, dict) or code != 0 or not res.get("ok"):
        err = res.get("error") if isinstance(res, dict) else None
        raise SecretError(err if err in ("bot_mismatch", "request_mismatch", "not_delivered", "unsafe_path", "invalid_value") else "guest_error")
    return {k: res[k] for k in ("ok", "bot_id", "request_id", "label")}


def _check_bot(bot_id):
    if not isinstance(bot_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", bot_id):
        raise SecretError("bot_mismatch")


def deliver(qga, bot_id, request_id, label, value):
    _check_bot(bot_id)
    check_request(request_id)
    name = env_name(label)
    check_value(value)
    return _run(qga, "deliver", bot_id, request_id, {"name": name, "value": value})


def receipt(qga, bot_id, request_id):
    _check_bot(bot_id)
    check_request(request_id)
    return _run(qga, "receipt", bot_id, request_id)


def read_request(stream):
    """Parse the deliver-secret stdin JSON. Returns (label, value). Errors carry a code only."""
    try:
        req = json.loads(stream.read(16384))
    except ValueError:
        raise SecretError("invalid_request") from None
    if not isinstance(req, dict) or set(req) != {"label", "value"}:
        raise SecretError("invalid_request")
    return req["label"], req["value"]
