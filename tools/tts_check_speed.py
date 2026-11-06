"""逐句测 TTS 配音的语速（音节/秒），找出念得太快的句子。

用法: python3 tts_check_speed.py 配音.mp3 合成用的文字.txt [--all] [--json 输出.json]
  按 >=150 ms 的停顿把音频切成语音段，和文字短句（按音节数估长度）做动态规划对齐，
  逐句算语速。默认只列出比中位数快 15% 以上的句子；--all 列出全部。
  正常 5.6-5.8 音节/秒；8 个音节以上、>=7.0 音节/秒的句子，把所在段落单独重做。
需要 imageio-ffmpeg（pip install --user imageio-ffmpeg）。见 配音.md。"""
import sys, re, math, json, subprocess, audioop
import imageio_ffmpeg
audio, textf = sys.argv[1], sys.argv[2]
SR = 16000; HOP = 160  # 10 ms
raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'quiet', '-i', audio, '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'], capture_output=True).stdout
db = []
for k in range(0, len(raw) - 2 * HOP, 2 * HOP):
    r = audioop.rms(raw[k:k + 2 * HOP], 2)
    db.append(20 * math.log10(r / 32768) if r > 0 else -100)
total = len(db) / 100.0
s = sorted(db); p90 = s[int(0.9 * len(s))]
thr = max(-50.0, p90 - 32)
voiced = [v > thr for v in db]
# pauses >= 150 ms split speech into chunks
chunks = []; i = 0; n = len(voiced)
while i < n:
    if voiced[i]:
        j = i
        while j < n:
            if voiced[j]: j += 1; continue
            k = j
            while k < n and not voiced[k]: k += 1
            if k - j >= 15 or k == n: break
            j = k
        if j - i >= 8: chunks.append((i / 100.0, j / 100.0))
        i = j
    else:
        i += 1
# text -> phrases with syllable estimates
def num_syl(tok):
    if '.' in tok:
        a, b = tok.split('.', 1); return num_syl(a or '0') + 1 + len(b)
    v = int(tok)
    if len(tok) == 4 and 1900 <= v <= 2100: return 4
    if v < 11: return 1
    if v < 20: return 2
    if v < 100: return 2 if v % 10 == 0 else 3
    if v < 1000: return 3 + (0 if v % 100 == 0 else 2)
    return len(tok) * 2
def eng_syl(w):
    lw = w.lower()
    special = {'mm': 2, 'lp': 2, 'pro': 1, 'gm': 2, 'art': 1, 'ii': 1, 'dg': 2, 'dn': 2, 'za': 2, 'spot': 1, 'spt': 3, 'zemax': 2, 'claude': 1, 'github': 2, 'powerpoint': 3, 'field': 1, 'vs': 3, 'lens': 1, 'bench': 1, 'mp': 2, 'nd': 2, 'vd': 2, 'bfl': 3, 'mcp': 3, 'ed': 2, 'fds': 3, 'nbfd': 4, 'cdgm': 4, 'mnbfd': 5}
    if lw in special: return special[lw]
    if w.isupper() and len(w) <= 5: return len(w)
    return max(1, len(re.findall(r'[aeiouy]+', lw)))
def syl(t):
    n = 0
    for m in re.finditer(r'[一-鿿]|\d+(?:\.\d+)?|[A-Za-z]+', t):
        g = m.group(0)
        if '一' <= g[0] <= '鿿': n += 1
        elif g[0].isdigit(): n += num_syl(g)
        else: n += eng_syl(g)
    return n
text = open(textf, encoding='utf-8').read()
phr = [p for p in re.findall(r'[^，。、；：？！\n]+[，。、；：？！]?', text) if p.strip() and syl(p) > 0]
ps = [syl(p) for p in phr]
cd = [b - a for a, b in chunks]
r0 = sum(cd) / sum(ps)  # seconds per syllable overall
# Gale-Church-like DP: groups of 1-3 phrases <-> 1-3 chunks
INF = float('inf'); NP, NC = len(phr), len(chunks)
D = [[INF] * (NC + 1) for _ in range(NP + 1)]; B = [[None] * (NC + 1) for _ in range(NP + 1)]
D[0][0] = 0
for i in range(NP + 1):
    for j in range(NC + 1):
        if D[i][j] == INF: continue
        for a in (1, 2, 3):
            for b in (1, 2, 3):
                if i + a > NP or j + b > NC: continue
                sy = sum(ps[i:i + a]); du = chunks[j + b - 1][1] - chunks[j][0] - sum(chunks[t + 1][0] - chunks[t][1] for t in range(j, j + b - 1))
                c = math.log((du / sy) / r0) ** 2 + 0.15 * (a - 1) + 0.25 * (b - 1)
                if D[i][j] + c < D[i + a][j + b]:
                    D[i + a][j + b] = D[i][j] + c; B[i + a][j + b] = (a, b)
i, j = NP, NC; groups = []
while i > 0 or j > 0:
    a, b = B[i][j]; groups.append((i - a, i, j - b, j)); i -= a; j -= b
groups.reverse()
rows = []
for i0, i1, j0, j1 in groups:
    t0, t1 = chunks[j0][0], chunks[j1 - 1][1]
    speech = sum(chunks[t][1] - chunks[t][0] for t in range(j0, j1))
    sy = sum(ps[i0:i1])
    rows.append({'t0': round(t0, 2), 't1': round(t1, 2), 'text': ''.join(phr[i0:i1]), 'syl': sy, 'rate': round(sy / speech, 2)})
rates = sorted(r['rate'] for r in rows); med = rates[len(rates) // 2]
for r in rows: r['ratio'] = round(r['rate'] / med, 2)
out = {'audio': audio, 'duration': round(total, 2), 'chunks': len(chunks), 'phrases': NP, 'median_rate': med,
       'overall_rate': round(sum(ps) / sum(cd), 2), 'rows': rows}
if '--json' in sys.argv: json.dump(out, open(sys.argv[sys.argv.index('--json') + 1], 'w'), ensure_ascii=False, indent=0)
print('%s  时长 %.1fs  语音段 %d  短句 %d  整体 %.2f 音节/秒  中位 %.2f' % (audio.split('/')[-1], total, len(chunks), NP, out['overall_rate'], med))
for r in rows:
    flag = '  <<< 快' if r['ratio'] >= 1.25 else ('  < 偏快' if r['ratio'] >= 1.15 else '')
    if '--all' in sys.argv or flag:
        print('  %6.2f-%6.2f  %5.2f/s  x%.2f  %s%s' % (r['t0'], r['t1'], r['rate'], r['ratio'], r['text'][:40], flag))
