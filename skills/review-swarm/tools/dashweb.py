#!/usr/bin/env python3
"""Serve the swarm web dashboard.  python3 dashweb.py [PORT]   then open http://127.0.0.1:8765/  (Ctrl-C stops it).

Serves only dash.html, swarm.json, areas.json, events.jsonl, queue/ and val/ from this directory, plus /sys.json
(memory, load and test slots, built from /proc and slots/ by dash.py's machine()), on localhost,
standard library only. Requests whose Host header is not this server's own address are refused, so
a web page open in the same browser cannot read the findings through DNS rebinding.
"""
import functools, http.server, importlib.util, json, os, sys

D = os.path.dirname(os.path.realpath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765


def load_machine():
    """machine() from dash.py next to this file, or None if dash.py does not load (the page then shows no panel)."""
    try:
        spec = importlib.util.spec_from_file_location("dash", os.path.join(D, "dash.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.machine
    except Exception:
        return None


MACHINE = load_machine()


class Handler(http.server.SimpleHTTPRequestHandler):
    def allowed(self):
        """The path to serve, after the Host and allowlist checks; None once an error was sent."""
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost", "[::1]"):
            self.send_error(403, "wrong Host")
            return None
        p = self.path.split("?")[0]
        p = "/dash.html" if p == "/" else p
        if p != "/sys.json" and p not in ("/dash.html", "/events.jsonl", "/swarm.json", "/areas.json") \
                and not p.startswith(("/queue/", "/val/")):
            self.send_error(404)
            return None
        return p

    def do_GET(self):
        p = self.allowed()
        if p is None:
            return
        if p == "/sys.json":
            return self.send_sys()
        self.path = p
        super().do_GET()

    def do_HEAD(self):
        p = self.allowed()
        if p is None:
            return
        if p == "/sys.json":
            return self.send_sys(head=True)
        self.path = p
        super().do_HEAD()

    def send_sys(self, head=False):
        try:
            body = json.dumps(MACHINE()).encode()
        except Exception:
            return self.send_error(503)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(Handler, directory=D))
    print(f"swarm dashboard: http://127.0.0.1:{PORT}/   (Ctrl-C stops)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
