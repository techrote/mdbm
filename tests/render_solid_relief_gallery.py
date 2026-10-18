"""Actual-cell renders plus 50%, 25%, 12.5% and grayscale squint-review sheets."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from build_solid_relief_themes import SPECS
from render_appearance_gallery import main

THEMES = tuple(s['id'] for s in SPECS)


def render():
    main(THEMES, 'solid-relief', 'MDBM / Sculpted environments', 144, 58)
    out = Path(__file__).resolve().parents[1]/'docs'/'screenshots'/'solid-relief'
    font = ImageFont.load_default(size=17)
    overview = Image.new('RGB', (1220, 3*470), '#10151e')
    review = Image.new('RGB', (1440, 6*655), '#10151e')
    for i, spec in enumerate(SPECS):
        with Image.open(out/(spec['id']+'.png')) as full:
            relief = full.copy()
            # This close-up includes a turned outer corner, a tile corner and
            # the adjacent vertical mullion rather than a decorative header.
            full.crop((0,21,800,651)).save(out/(spec['id']+'-detail.png'))
        half = relief.resize((720,609), Image.Resampling.LANCZOS)
        quarter = relief.resize((360,305), Image.Resampling.LANCZOS)
        eighth = relief.resize((180,152), Image.Resampling.LANCZOS)
        x,y=(i%2)*620,(i//2)*470
        ImageDraw.Draw(overview).text((x+8,y+5),spec['name'],font=font,fill='#e4eaf2')
        overview.paste(relief.resize((520,440),Image.Resampling.LANCZOS),(x,y+30))
        y=i*655
        draw=ImageDraw.Draw(review)
        draw.text((8,y+5),spec['name']+' / 50%',font=font,fill='#e4eaf2')
        draw.text((744,y+5),'25%',font=font,fill='#e4eaf2')
        draw.text((1120,y+5),'12.5%',font=font,fill='#e4eaf2')
        review.paste(half,(0,y+32))
        review.paste(quarter,(740,y+32))
        review.paste(eighth,(1120,y+32))
        review.paste(ImageOps.grayscale(quarter).convert('RGB'),(740,y+345))
        draw.text((1120,y+345),'Grayscale value check',font=font,fill='#b8c3cf')
    overview.save(out/'relief-comparison.png')
    review.save(out/'squint-review.png')
    gallery=out/'gallery.html'
    page=gallery.read_text(encoding='utf-8')
    page=page.replace('</main>', '<section><h2>Reduced-scale shading review</h2>'
        '<p>50%, 25%, 12.5%, and grayscale crops of the actual cell render. '
        'The grayscale crop checks value structure; it is not a monochrome terminal simulation.</p>'
        '<a href="squint-review.png"><img src="squint-review.png" alt="Multi-scale relief shading review"></a>'
        '</section></main>')
    gallery.write_text(page,encoding='utf-8')


if __name__=='__main__':
    render()
