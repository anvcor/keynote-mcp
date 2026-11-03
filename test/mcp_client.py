"""Tiny MCP stdio client for testing the server end to end.

    python3 test/mcp_client.py            # runs the full real-Keynote test (test/e2e.json steps)
"""
import base64
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class Client:
    def __init__(self, extra_args=(), env=None):
        e = dict(os.environ)
        e.update(env or {})
        self.p = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")] + list(extra_args),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr, env=e)
        self.n = 0

    def request(self, method, params=None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.p.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError("server closed")
        r = json.loads(line)
        if "error" in r:
            raise RuntimeError(r["error"])
        return r["result"]

    def notify(self, method):
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", "method": method}) + "\n").encode())
        self.p.stdin.flush()

    def initialize(self):
        r = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "test", "version": "0"}})
        self.notify("notifications/initialized")
        return r

    def call(self, name, args=None, save_image=None):
        r = self.request("tools/call", {"name": name, "arguments": args or {}})
        text = r["content"][0]["text"]
        if save_image:
            for c in r["content"]:
                if c["type"] == "image":
                    with open(save_image, "wb") as f:
                        f.write(base64.b64decode(c["data"]))
        try:
            data = json.loads(text)
        except ValueError:
            data = text
        return r.get("isError", False), data

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)
