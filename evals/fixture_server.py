"""Local fixture site for the deck evals.

It serves the HTML files in fixtures/ and fills in {{name}} from the run values.
It records each POST (form fields and uploads), so a check can assert what the agent sent.
Run it alone for a manual look: python3 fixture_server.py
"""

import email.parser
import email.policy
import html
import http.server
import json
import secrets
import threading
import time
import urllib.parse
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

# Catalog for the pagination task. "Cobalt Lantern" is item 24, on page 3.
PRODUCTS = [
    "Amber Kettle", "Basalt Mug", "Cedar Shelf", "Dune Rug", "Ember Lamp",
    "Fjord Blanket", "Garnet Vase", "Harbor Clock", "Iris Planter", "Jade Bowl",
    "Kelp Basket", "Linen Throw", "Maple Tray", "Nimbus Pillow", "Onyx Coaster",
    "Pine Stool", "Quartz Candle", "Reed Screen", "Slate Board", "Tidal Print",
    "Umber Jug", "Velvet Chair", "Willow Hook", "Cobalt Lantern", "Yarrow Frame",
    "Zinc Bin", "Alder Bench", "Birch Crate", "Clay Pitcher", "Delta Mirror",
    "Elm Desk", "Flint Bookend", "Grove Mat", "Hazel Cabinet", "Ivory Dish",
    "Juniper Soap", "Koa Spoon", "Lumen Bulb", "Moss Cushion", "Nectar Jar",
    "Opal Sconce", "Pebble Tile", "Quill Holder", "Rowan Ladder", "Sable Towel",
    "Teak Box", "Umbra Shade", "Vine Trellis", "Wren Feeder", "Yew Planter",
]
PER_PAGE = 10


class State:
    """Run values and what the agent sent. Shared between threads."""

    def __init__(self, password=None):
        self.lock = threading.Lock()
        self.vars = {
            "account": "ACC-" + secrets.token_hex(4).upper(),
            "tracking": "TRK-" + secrets.token_hex(3).upper(),
            "welcome": "WEL-" + secrets.token_hex(3).upper(),
            "sku": "SKU-" + str(secrets.randbelow(900000) + 100000),
        }
        self.password = password or secrets.token_urlsafe(12)
        self.sessions = set()
        self.reset()

    def reset(self):
        with self.lock:
            self.posts = []
            self.logins_ok = 0

    def record(self, path, fields, files):
        # The password never goes into the results.
        clean = {
            k: ["[password]" if v == self.password else v for v in vs]
            for k, vs in fields.items()
        }
        with self.lock:
            self.posts.append({"path": path, "fields": clean, "files": files})

    def snapshot(self):
        with self.lock:
            return {"posts": list(self.posts), "logins_ok": self.logins_ok}


def parse_multipart(content_type, body):
    """Return (fields, files) of a multipart/form-data body."""
    head = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(head + body)
    fields, files = {}, []
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if filename is not None:
            files.append({
                "field": name,
                "filename": filename,
                "size": len(data),
                "text": data.decode("utf-8", "replace")[:2000],
            })
        else:
            fields.setdefault(name, []).append(data.decode("utf-8", "replace"))
    return fields, files


def catalog_page(page, sku):
    pages = (len(PRODUCTS) + PER_PAGE - 1) // PER_PAGE
    page = max(1, min(page, pages))
    rows = []
    for i, name in enumerate(PRODUCTS[(page - 1) * PER_PAGE: page * PER_PAGE], (page - 1) * PER_PAGE + 1):
        code = sku if name == "Cobalt Lantern" else f"SKU-{100000 + i * 7919 % 899999}"
        rows.append(f"<tr><td>{i}</td><td>{html.escape(name)}</td><td>{code}</td></tr>")
    nav = []
    if page > 1:
        nav.append(f'<a href="/catalog?page={page - 1}">‹ Previous</a>')
    nav.append(f"<span>Page {page} of {pages}</span>")
    if page < pages:
        nav.append(f'<a href="/catalog?page={page + 1}">Next ›</a>')
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>Catalog</title></head><body>"
        f"<h1>Catalog</h1><table><thead><tr><th>#</th><th>Product</th><th>SKU</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table><nav>{' '.join(nav)}</nav></body></html>"
    )


class Handler(http.server.BaseHTTPRequestHandler):
    state: State  # set by make_server

    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def page(self, text):
        return f"<!doctype html><html><head><meta charset=utf-8><title>Fixture</title></head><body>{text}</body></html>"

    def logged_in(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "session" and v in self.state.sessions:
                return True
        return False

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        query = urllib.parse.parse_qs(url.query)
        if path == "/":
            path = "/index.html"
        if path == "/account":
            if not self.logged_in():
                return self.send(303, "", headers=[("Location", "/login.html")])
            acc = self.state.vars["account"]
            return self.send(200, self.page(f"<h1>Your account</h1><p>Account number: <b>{acc}</b></p>"))
        if path == "/catalog":
            try:
                page = int(query.get("page", ["1"])[0])
            except ValueError:
                page = 1
            return self.send(200, catalog_page(page, self.state.vars["sku"]))
        if path == "/api/shipment":
            time.sleep(4)
            return self.send(200, json.dumps({"tracking": self.state.vars["tracking"]}), "application/json")
        file = (FIXTURES / path.lstrip("/")).resolve()
        if not file.is_relative_to(FIXTURES.resolve()) or not file.is_file():
            return self.send(404, self.page("<h1>Not found</h1>"))
        text = file.read_text()
        for k, v in self.state.vars.items():
            text = text.replace("{{" + k + "}}", v)
        self.send(200, text)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            fields, files = parse_multipart(ctype, body)
        else:
            fields, files = urllib.parse.parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True), []
        self.state.record(path, fields, files)
        first = lambda k: fields.get(k, [""])[0]
        if path == "/login":
            if first("user") == "eval" and first("password") == self.state.password:
                token = secrets.token_hex(16)
                with self.state.lock:
                    self.state.sessions.add(token)
                    self.state.logins_ok += 1
                return self.send(303, "", headers=[
                    ("Location", "/account"),
                    ("Set-Cookie", f"session={token}; Path=/; HttpOnly; SameSite=Lax"),
                ])
            return self.send(401, self.page('<h1>Wrong user or password</h1><a href="/login.html">Try again</a>'))
        if path == "/wizard/submit":
            return self.send(200, self.page(f"<h1>Thanks, {html.escape(first('name'))}</h1><p>Your signup is received.</p>"))
        if path == "/upload":
            names = ", ".join(html.escape(f["filename"]) for f in files) or "nothing"
            return self.send(200, self.page(f"<h1>Upload received</h1><p>Files: {names}</p>"))
        if path == "/drafts/delete":
            return self.send(200, json.dumps({"ok": True}), "application/json")
        self.send(200, self.page("<h1>Received</h1>"))


def make_server(state, port=0):
    handler = type("BoundHandler", (Handler,), {"state": state})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def start(state):
    """Serve in a daemon thread. Returns (server, base_url)."""
    server = make_server(state)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


if __name__ == "__main__":
    st = State()
    srv = make_server(st, 8799)
    print(f"http://127.0.0.1:8799  password: {st.password}  values: {st.vars}")
    srv.serve_forever()
