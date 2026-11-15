// 在火山引擎「语音合成」控制台页面里批量合成 TTS 小块（见 配音.md）。
// 先在页面里选好复刻音色和语速，再用 javascript_tool 执行本文件内容，然后：
//   __ttsRun([["c01", "第一块文字"], ["c02", "第二块文字"], ...])      // 列表来自 chunks.json
// 进度看 window.__ttsProg（每块 {chars, want, dur, size, saved}；__done 表示跑完）。
// 每次 javascript_tool 调用要在 45 秒内返回，所以 __ttsRun 不 await，轮询 __ttsProg 即可。
// 中途停：window.__ttsStop = true。
// 每块: 写入编辑框 -> 点「合成试听」-> 弹「后付费」提示就点「继续使用」（作者同意按量扣费后才跑）
//       -> 等 <audio> 换成新的 blob -> 取 mp3 -> POST 到本机 tts_recv.py。
window.__ttsRun = async (list, port = 8765) => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  window.__ttsProg = {}; window.__ttsStop = false;
  for (const [id, text] of list) {
    if (window.__ttsStop) { window.__ttsProg[id] = 'stopped'; break; }
    try {
      const ed = document.querySelector('.tiptap.ProseMirror').editor;
      ed.commands.setContent(text, true); await sleep(600);
      const shown = +((document.body.innerText.match(/(\d+)字符/) || [])[1] || 0);
      const prevSrc = (document.querySelector('audio') || {}).src || '';
      [...document.querySelectorAll('button')].find(b => b.offsetParent && /合成试听/.test(b.innerText)).click();
      let m = null;
      for (let i = 0; i < 30 && !m; i++) {
        await sleep(200);
        m = [...document.querySelectorAll('.arco-modal')].find(x => x.offsetParent && /后付费/.test(x.innerText));
      }
      if (m) [...m.querySelectorAll('button')].find(b => b.innerText.trim() === '继续使用').click();
      let a = null;
      for (let i = 0; i < 150; i++) {
        await sleep(400);
        a = document.querySelector('audio');
        if (a && a.src && a.src !== prevSrc && isFinite(a.duration) && a.duration > 0) break;
        a = null;
      }
      if (!a) { window.__ttsProg[id] = 'timeout'; continue; }
      a.pause(); await sleep(800);
      const b = await (await fetch(a.src)).blob();
      // 不要给 fetch 加 targetAddressSpace 选项，加了反而失败
      const saved = await fetch(`http://127.0.0.1:${port}/${id}`, {method: 'POST', body: b})
        .then(r => r.text()).catch(e => 'ERR ' + e.message);
      window.__ttsProg[id] = {chars: shown, want: text.length, dur: +a.duration.toFixed(2), size: b.size, saved};
      await sleep(1000);
    } catch (e) { window.__ttsProg[id] = 'err ' + e; }
  }
  window.__ttsProg.__done = true;
};
// 检查: 有没有失败或字数对不上的块
window.__ttsBad = () => Object.entries(window.__ttsProg).filter(([k, v]) => k[0] === 'c' &&
  (typeof v !== 'object' || v.chars !== v.want || String(v.size) !== v.saved)).map(([k]) => k);
'tts_batch loaded';
