#!/usr/bin/env python3
"""Serve the swarm web dashboard.  python3 dashweb.py [PORT]   then open http://127.0.0.1:8765/  (Ctrl-C stops it).

Serves only dash.html, swarm.json, events.jsonl, queue/ and val/ from this directory, plus /sys.json
(memory, load and test slots, built from /proc and slots/ by dash.py's machine()), on localhost,
standard library only.
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
    def do_GET(self):
        p = self.path.split("?")[0]
        p = "/dash.html" if p == "/" else p
        if p == "/sys.json":
            return self.send_sys()
        if p not in ("/dash.html", "/events.jsonl", "/swarm.json") and not p.startswith(("/queue/", "/val/")):
            return self.send_error(404)
        self.path = p
        super().do_GET()

    def send_sys(self):
        try:
            body = json.dumps(MACHINE()).encode()
        except Exception:
            return self.send_error(503)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
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
