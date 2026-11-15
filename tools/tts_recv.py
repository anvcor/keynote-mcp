"""本机接收 TTS 小块音频（见 配音.md）。

用法: python tts_recv.py 项目/配音/takes [--port 8765]

Chrome 不允许页面脚本连续触发下载（第二个起就被拦下），所以控制台页面里的
tts_batch.js 把每块合成好的 mp3 POST 到 http://127.0.0.1:<port>/<块号>，这里存成 <块号>.mp3。
只监听 127.0.0.1，只接受 console.volcengine.com 的请求，块号只能是 c01、c49b 这种格式。
重做的块用后缀字母（c49b、c49c），tts_join.py 取字母最大的那一版。
"""
import argparse, os, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("--port", type=int, default=8765)
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
ORIGIN = "https://console.volcengine.com"

class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_POST(self):
        name = self.path.strip("/")
        if not re.fullmatch(r"c\d{2,3}[a-z]?", name) or self.headers.get("Origin") not in (None, ORIGIN):
            self.send_response(400); self._cors(); self.end_headers(); return
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        with open(os.path.join(a.out, name + ".mp3"), "wb") as f:
            f.write(data)
        print(name, len(data), flush=True)
        self.send_response(200); self._cors(); self.end_headers()
        self.wfile.write(str(len(data)).encode())

    def log_message(self, *args):
        pass

print(f"listening on 127.0.0.1:{a.port} -> {a.out}", flush=True)
ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
