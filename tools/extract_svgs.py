"""Pull every SVG graphic out of a .pptx, one file per (slide, shape), with a CSV of where each sits on its slide.

    python tools/extract_svgs.py deck.pptx OUT_DIR

Why: Keynote's PPTX import does not keep PowerPoint's SVG pictures (it takes the low-res PNG fallback or drops them),
so the Mac side re-renders each SVG to PNG and puts it back at the same place. Output:
  OUT_DIR/pNNN_<shape name>.svg     (shape names like '!!Viltrox35 layout'; '!!' and odd characters made file-safe)
  OUT_DIR/清单.csv                   slide, shape name, file, media part, left/top/width/height in pt (1920x1080 page)
Slides are numbered as PowerPoint numbers them. Identical media parts are written once per slide/shape anyway,
so each row is self-contained.
"""
import csv
import os
import posixpath
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'asvg': 'http://schemas.microsoft.com/office/drawing/2016/SVG/main',
      'rel': 'http://schemas.openxmlformats.org/package/2006/relationships'}
EMU = 12700.0


def slide_order(z):
    pres = ET.fromstring(z.read('ppt/presentation.xml'))
    rels = ET.fromstring(z.read('ppt/_rels/presentation.xml.rels'))
    tgt = {r.get('Id'): r.get('Target') for r in rels}
    ids = [s.get('{%s}id' % NS['r']) for s in pres.find('p:sldIdLst', NS)]
    return ['ppt/' + tgt[i].lstrip('/').replace('ppt/', '') for i in ids]


def main(pptx, out):
    os.makedirs(out, exist_ok=True)
    z = zipfile.ZipFile(pptx)
    rows = []
    for k, part in enumerate(slide_order(z), 1):
        rel_part = os.path.dirname(part) + '/_rels/' + os.path.basename(part) + '.rels'
        rels = {r.get('Id'): r.get('Target') for r in ET.fromstring(z.read(rel_part))}
        root = ET.fromstring(z.read(part))
        for pic in root.iter('{%s}pic' % NS['p']):
            svg = pic.find('.//asvg:svgBlip', NS)
            if svg is None:
                continue
            name = pic.find('p:nvPicPr/p:cNvPr', NS).get('name')
            media = posixpath.normpath(posixpath.join(posixpath.dirname(part), rels[svg.get('{%s}embed' % NS['r'])]))
            off = pic.find('p:spPr/a:xfrm/a:off', NS)
            ext = pic.find('p:spPr/a:xfrm/a:ext', NS)
            safe = re.sub(r'[^\w.\- ]+', '', name).strip().replace(' ', '_') or 'svg'
            fn = 'p%03d_%s.svg' % (k, safe)
            with open(os.path.join(out, fn), 'wb') as f:
                f.write(z.read(media))
            rows.append([k, name, fn, media, round(int(off.get('x')) / EMU, 1), round(int(off.get('y')) / EMU, 1),
                         round(int(ext.get('cx')) / EMU, 1), round(int(ext.get('cy')) / EMU, 1)])
    with open(os.path.join(out, '清单.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['slide', 'shape', 'file', 'media', 'left_pt', 'top_pt', 'width_pt', 'height_pt'])
        w.writerows(rows)
    print(len(rows), 'svg pictures ->', out)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
