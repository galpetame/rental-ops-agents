"""A small HTTP face for the fleet core, so n8n (or anything else) can call it.

    python server.py [port]      default 8787; state lives in memory, for demos

    POST /message  {msg_id, channel, customer, text}   offline proposer, then the core
    POST /action   {msg_id, channel, action, args}     an LLM proposed the action; the core decides
    GET  /report?day=N                                 daily report
    GET  /tickets                                      open tickets for people
    POST /tickets/resolve {id}
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from agent import run
from fleet_core import new_fleet


def make_handler(fleet):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/report":
                return self._send(200, fleet.daily_report(int(parse_qs(u.query).get("day", ["0"])[0])))
            if u.path == "/tickets":
                return self._send(200, [t for t in fleet.exceptions if t["status"] == "open"])
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
                if self.path == "/message":
                    return self._send(200, run(fleet, body["msg_id"], body["channel"], body["customer"], body["text"]))
                if self.path == "/action":
                    return self._send(200, fleet.handle(body["msg_id"], body["channel"], body["action"], **body.get("args", {})))
                if self.path == "/tickets/resolve":
                    fleet.resolve(body["id"])
                    return self._send(200, {"ok": True})
            except (KeyError, ValueError, TypeError) as e:
                return self._send(400, {"error": f"bad request: {e!r}"})
            self._send(404, {"error": "not found"})
    return H


def serve(port=8787, fleet=None):
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(fleet or new_fleet()))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    print(f"fleet core on http://127.0.0.1:{port}")
    serve(port).serve_forever()
