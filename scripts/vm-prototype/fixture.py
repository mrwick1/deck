#!/usr/bin/env python3
"""Synthetic fixture service for the Stage 1 desktop VM prototype.

Serves fixed pages on 127.0.0.1. All data is synthetic.
With --secret-port it also serves a host-only marker on a second port,
which the guest must not reach.
"""
import argparse
import functools
import hashlib
import re
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

CSV = b"item,qty\nwidget,3\ngadget,5\n"
USER, PASSWORD = "demo", "demo-pass"  # synthetic
SESSIONS = set()

PAGE = "<!doctype html><title>{t}</title><h1>{t}</h1>{b}"

# Bounded synthetic routes for the memory benchmark. Fixed sizes; nothing grows with requests.
MEM_PAGES, MEM_PAGE_BYTES = 40, 32 * 1024
MEM_DOWNLOADS, MEM_DOWNLOAD_BYTES = 8, 2 << 20
MEM_END_MARKER = "MEM-END-MARKER-5b1e"
_MEM_ROUTE = re.compile(r"/mem/(page|dl)/(\d{1,2})")


def mem_page(n):
    """Page n as bytes of exactly MEM_PAGE_BYTES. Text only: a target=_blank link to the end page and a download link."""
    head = f'<!doctype html><title>Mem {n}</title><h1>Mem page {n}</h1><a href="/mem/end" target="_blank">Open end page</a> <a href="/mem/dl/{n % MEM_DOWNLOADS}">Download file</a><pre>'
    tail = "</pre>"
    line = f"page {n:02d} synthetic filler line for the memory benchmark\n".encode()
    fill = (line * (MEM_PAGE_BYTES // len(line) + 1))[:MEM_PAGE_BYTES - len(head) - len(tail)]
    return head.encode() + fill + tail.encode()


@functools.lru_cache(maxsize=MEM_DOWNLOADS)
def mem_download(k):
    """Download k as exactly MEM_DOWNLOAD_BYTES of deterministic bytes."""
    block = b"".join(hashlib.sha256(f"deck-mem-{k}-{i}".encode()).digest() for i in range(128))
    return block * (MEM_DOWNLOAD_BYTES // len(block))


def mem_download_sha(k):
    return hashlib.sha256(mem_download(k)).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="text/html", headers=()):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def signed_in(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return "fx_session" in c and c["fx_session"].value in SESSIONS

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self.send(200, PAGE.format(t="Deck fixture", b='<p>Synthetic page.</p><a href="/login">Sign in</a> <a href="/files/report.csv">Download report</a> <a href="/account">Account</a>'))
        elif path == "/login":
            self.send(200, PAGE.format(t="Sign in", b='<form method="post" action="/login"><label>User <input name="user"></label><label>Password <input name="password" type="password"></label><button type="submit">Sign in</button></form>'))
        elif path == "/account":
            b = f"<p id=state>Signed in as {USER}</p>" if self.signed_in() else "<p id=state>Signed out</p>"
            self.send(200, PAGE.format(t="Account", b=b))
        elif path == "/files/report.csv":
            self.send(200, CSV, "text/csv", [("Content-Disposition", 'attachment; filename="report.csv"')])
        elif path == "/mem/end":
            self.send(200, PAGE.format(t=MEM_END_MARKER, b=f"<p id=state>{MEM_END_MARKER}</p>"))
        elif path == "/mem/":
            links = " ".join(f'<a href="/mem/page/{n}">p{n}</a>' for n in range(MEM_PAGES))
            self.send(200, PAGE.format(t="Mem index", b=links))
        elif (m := _MEM_ROUTE.fullmatch(path)) and int(m[2]) < (MEM_PAGES if m[1] == "page" else MEM_DOWNLOADS):
            n = int(m[2])
            if m[1] == "page":
                self.send(200, mem_page(n))
            else:
                self.send(200, mem_download(n), "application/octet-stream", [("Content-Disposition", f'attachment; filename="mem-{n}.bin"')])
        elif path == "/health":
            self.send(200, "ok", "text/plain")
        else:
            self.send(404, PAGE.format(t="Not found", b=""))

    def do_POST(self):
        if self.path != "/login":
            return self.send(404, "no", "text/plain")
        n = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(n).decode())
        if form.get("user") == [USER] and form.get("password") == [PASSWORD]:
            tok = secrets.token_hex(8)
            SESSIONS.add(tok)
            self.send(303, "", headers=[("Location", "/account"), ("Set-Cookie", f"fx_session={tok}; Path=/; Max-Age=86400")])
        else:
            self.send(401, PAGE.format(t="Sign in failed", b=""))

    def log_message(self, fmt, *args):
        print(self.address_string(), fmt % args, flush=True)


class Secret(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"HOST-ONLY-MARKER")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--secret-port", type=int, help="serve a host-only marker here")
    a = ap.parse_args()
    if a.secret_port:
        s = ThreadingHTTPServer(("127.0.0.1", a.secret_port), Secret)
        threading.Thread(target=s.serve_forever, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
