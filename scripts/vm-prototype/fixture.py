#!/usr/bin/env python3
"""Synthetic fixture service for the Stage 1 desktop VM prototype.

Serves fixed pages on 127.0.0.1. All data is synthetic.
With --secret-port it also serves a host-only marker on a second port,
which the guest must not reach.
"""
import argparse
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

CSV = b"item,qty\nwidget,3\ngadget,5\n"
USER, PASSWORD = "demo", "demo-pass"  # synthetic
SESSIONS = set()

PAGE = "<!doctype html><title>{t}</title><h1>{t}</h1>{b}"


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
