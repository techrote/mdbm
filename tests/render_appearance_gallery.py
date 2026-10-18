"""Render the six appearance themes using MDBM's real cell compositor.

Run from the source checkout. Pillow is needed for PNGs; existing test/runtime
dependencies supply the application. No golden baselines are changed.
"""
import html
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render_support import scene

THEMES = (
    'botanical-ledger', 'tidal-observatory', 'ember-foundry',
    'polar-blueprint', 'velvet-nocturne', 'desert-cartography',
)


def main(themes=THEMES, folder='appearance', title='Six MDBM appearance themes', width=120, height=40):
    from PIL import Image, ImageDraw, ImageFont

    root = Path(__file__).resolve().parents[1]
    out = root / 'docs' / 'screenshots' / folder
    out.mkdir(parents=True, exist_ok=True)
    font_pairs = (
        (Path('C:/Windows/Fonts/consola.ttf'), Path('C:/Windows/Fonts/consolab.ttf')),
        (Path('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'),
         Path('/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf')),
        (Path('/System/Library/Fonts/Menlo.ttc'), Path('/System/Library/Fonts/Menlo.ttc')),
    )
    pair = next((p for p in font_pairs if all(f.exists() for f in p)), None)
    if pair is None:
        raise SystemExit('Install Consolas, DejaVu Sans Mono, or Menlo to render PNGs.')
    normal, bold = (ImageFont.truetype(str(p), 16) for p in pair)
    cw, ch = 10, 21
    thumb_height = round(height * ch * 600 / (width * cw))
    card_height = thumb_height + 34
    cards = []
    sections = []
    with tempfile.TemporaryDirectory() as tmp:
        for tid in themes:
            w, canvas = scene(Path(tmp), tid, 'truecolour', width, height)
            if w.theme.id != tid:
                raise RuntimeError(f'Theme failed to load: {tid}')
            frame = Image.new('RGB', (width*cw, height*ch))
            draw = ImageDraw.Draw(frame)
            for y in range(canvas.height):
                for x in range(canvas.width):
                    c = canvas.cells[y*canvas.width+x]
                    draw.rectangle((x*cw,y*ch,(x+1)*cw-1,(y+1)*ch-1),
                                   fill=c.ink.bg or '#10151e')
            for y in range(canvas.height):
                for x in range(canvas.width):
                    c = canvas.cells[y*canvas.width+x]
                    if c.continuation:
                        continue
                    # Block art must fill its cell. A font advance smaller than cw
                    # otherwise introduces false seams into solid ANSI/NFO shapes.
                    fg = c.ink.fg or '#d8e3ec'
                    if c.glyph in ('\u2588', '\u2580', '\u2584'):
                        top = y*ch + (ch//2 if c.glyph == '\u2584' else 0)
                        bottom = y*ch + (ch//2-1 if c.glyph == '\u2580' else ch-1)
                        draw.rectangle((x*cw, top, (x+1)*cw-1, bottom), fill=fg)
                    elif c.glyph in ('\u2591', '\u2592', '\u2593'):
                        density = {'\u2591': 1, '\u2592': 2, '\u2593': 3}[c.glyph]
                        for dy in range(ch):
                            for dx in range(cw):
                                if ((dx % 2)*2 + (dy % 2)) < density:
                                    draw.point((x*cw+dx, y*ch+dy), fill=fg)
                    else:
                        draw.text((x*cw,y*ch), c.glyph, font=bold if c.ink.bold else normal,
                                  fill=fg)
                    if c.ink.underline:
                        draw.line((x*cw,y*ch+18,(x+1)*cw-1,y*ch+18),
                                  fill=c.ink.fg or '#d8e3ec')
            frame.save(out / (tid+'.png'))
            card = Image.new('RGB', (600, card_height), '#121822')
            ImageDraw.Draw(card).text((14, 9), w.theme.name, font=bold, fill='#eef2f6')
            card.paste(frame.resize((600, thumb_height), Image.Resampling.LANCZOS), (0, 34))
            cards.append(card)
            sections.append(f'<section><h2>{html.escape(w.theme.name)}</h2>'
                            f'<p><code>--theme {tid}</code></p>'
                            f'<a href="{tid}.png"><img src="{tid}.png" '
                            f'alt="Actual MDBM compositor: {html.escape(w.theme.name)}"></a></section>')
    sheet = Image.new('RGB', (1220, ((len(cards)+1)//2)*(card_height+20)-20), '#121822')
    for i, card in enumerate(cards):
        sheet.paste(card, ((i % 2)*620, (i // 2)*(card_height+20)))
    sheet.save(out / 'comparison.png')
    page = ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>'
            'body{background:#121822;color:#eef2f6;font:16px system-ui;margin:32px}'
            'main{max-width:1240px;margin:auto}section{margin:40px 0}img{width:100%;height:auto}'
            'p{line-height:1.6;color:#bbc6d3}code{color:#a7e7d4}</style><main>'
            f'<h1>{html.escape(title)}</h1><p>Actual compositor snapshots at '
            f'{width} × {height} cells with fixed source, focus, seed and time. Click a preview '
            'for full size. Animated themes are shown as still frames. Terminal font '
            'rendering can differ; these previews are not native terminal acceptance tests.</p>'
            + ''.join(sections) + '</main></html>')
    (out / 'gallery.html').write_text(page, encoding='utf-8')
    print(out / 'gallery.html')


if __name__ == '__main__':
    main()
