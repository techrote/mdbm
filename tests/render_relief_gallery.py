"""Actual-cell previews and a close-up sheet of the three ASCII sculptures."""
from pathlib import Path
from render_appearance_gallery import main

THEMES = ('iron-intaglio', 'obsidian-reliquary', 'glacier-monolith')

if __name__ == '__main__':
    main(THEMES, 'relief', 'MDBM / Sculpted ASCII relief', 140, 58)
    from PIL import Image, ImageDraw, ImageFont
    out = Path(__file__).resolve().parents[1] / 'docs' / 'screenshots' / 'relief'
    # Twelve rows of actual outer chrome, excluding the application menu.
    strip_height = 12 * 21
    sheet = Image.new('RGB', (1400, 3*(strip_height+38)), '#121822')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=20)
    for index, tid in enumerate(THEMES):
        y = index*(strip_height+38)
        draw.text((16, y+8), tid.replace('-', ' ').title(), font=font, fill='#eef2f6')
        with Image.open(out / (tid+'.png')) as frame:
            sheet.paste(frame.crop((0, 21, 1400, 21+strip_height)), (0, y+38))
    sheet.save(out / 'relief-detail.png')
