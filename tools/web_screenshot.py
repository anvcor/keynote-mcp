"""Screenshot a web page at an exact slide size with headless Chrome (DevTools protocol), optionally after running JS.

    python tools/web_screenshot.py URL OUT.png [--js file.js] [--wait 5] [--scale 2] [--crop-top 68]
                                              [--width 1920] [--height 1080] [--chrome PATH]

Why: the built-in / extension browsers only hand screenshots back to the model, not to a file, and selenium needs a
matching chromedriver (often not downloadable here). This talks to Chrome directly over the remote-debugging websocket.
  * --scale 2 renders at deviceScaleFactor 2 -> 3840x2160 for the 4K video export (layout identical to 1920x1080).
  * --js: a JS file evaluated after load (awaitPromise), e.g. to pick a lens in a <select> and dispatch 'change';
    its return value is printed. The page gets another --wait seconds afterwards.
  * --crop-top N cuts N CSS px off the top (GitHub's header bar is 68), result keeps the full width.
Uses a throw-away profile (not logged in), so pages look like the public view.
Needs: trio, trio-websocket (pip), Chrome or Edge.
"""
import argparse
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request

import trio
from trio_websocket import open_websocket_url

CHROMES = [r'C:\Program Files\Google\Chrome\Application\chrome.exe',
           r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
           '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('url')
    ap.add_argument('out')
    ap.add_argument('--js', default='')
    ap.add_argument('--wait', type=float, default=5)
    ap.add_argument('--scale', type=float, default=1)
    ap.add_argument('--crop-top', type=int, default=0)
    ap.add_argument('--width', type=int, default=1920)
    ap.add_argument('--height', type=int, default=1080)
    ap.add_argument('--port', type=int, default=9333)
    ap.add_argument('--chrome', default='')
    a = ap.parse_args()
    chrome = a.chrome or next(c for c in CHROMES if os.path.exists(c))
    prof = tempfile.mkdtemp(prefix='webshot_')
    proc = subprocess.Popen([chrome, '--headless=new', '--remote-debugging-port=%d' % a.port, '--hide-scrollbars',
                             '--window-size=%d,%d' % (a.width, a.height), '--user-data-dir=' + prof, '--no-first-run', 'about:blank'])
    try:
        tabs = None
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen('http://127.0.0.1:%d/json' % a.port))
                break
            except Exception:
                time.sleep(0.5)
        ws_url = [t for t in tabs if t['type'] == 'page'][0]['webSocketDebuggerUrl']

        async def run():
            async with open_websocket_url(ws_url, max_message_size=2 ** 28) as ws:
                n = [0]

                async def call(method, **params):
                    n[0] += 1
                    i = n[0]
                    await ws.send_message(json.dumps({'id': i, 'method': method, 'params': params}))
                    while True:
                        m = json.loads(await ws.get_message())
                        if m.get('id') == i:
                            return m
                await call('Emulation.setDeviceMetricsOverride', width=a.width, height=a.height + a.crop_top,
                           deviceScaleFactor=a.scale, mobile=False)
                await call('Page.navigate', url=a.url)
                await trio.sleep(a.wait)
                if a.js:
                    r = await call('Runtime.evaluate', expression=open(a.js, encoding='utf-8').read(), awaitPromise=True, returnByValue=True)
                    print(json.dumps(r.get('result', {}), ensure_ascii=False)[:2000])
                    await trio.sleep(a.wait)
                clip = dict(x=0, y=a.crop_top, width=a.width, height=a.height, scale=1)
                r = await call('Page.captureScreenshot', format='png', clip=clip)
                with open(a.out, 'wb') as f:
                    f.write(base64.b64decode(r['result']['data']))
                print('saved', a.out)
        trio.run(run)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            pass
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == '__main__':
    main()
