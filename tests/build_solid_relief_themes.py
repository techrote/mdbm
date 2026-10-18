"""Bake lit material reliefs into standalone MDBM theme data.

The application receives ordinary art/style/layer records, not executable
shaders. Run intentionally to regenerate these six themes. Pillow measures
glyph coverage; wcwidth excludes glyphs that cannot occupy one terminal cell.
"""
import json
import math
import tomllib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from wcwidth import wcswidth

ROOT = Path(__file__).resolve().parents[1]
TONES = 32
SPECS = (
    dict(id='intaglio-bronze', name='Intaglio / Bronze Housing', parent='iron-intaglio',
         form='bronze', stops=['#080705', '#462619', '#ba7943', '#ffe7ae'],
         ink='#eddfca', accent='#efb26f', link='#a9cdbb',
         grain=".,:;!il/\\+=xvzXG&%#@\u00a4\u00f8\u00d8\u00e6\u00c6\u00df\u00b6\u00a7"),
    dict(id='intaglio-gunmetal', name='Intaglio / Gunmetal Diepress', parent='iron-intaglio',
         form='gunmetal', stops=['#05080c', '#293645', '#8c9daa', '#eff8fc'],
         ink='#dee8ed', accent='#c5d7e4', link='#b2d4c0',
         grain=".,:;!ilI1tfr/\\[]{}<>7YMXZ%#@\u2261\u00b1\u00d7\u00a7\u00b6\u00d0\u00de"),
    dict(id='reliquary-amethyst', name='Reliquary / Amethyst Vault', parent='obsidian-reliquary',
         form='amethyst', stops=['#09060e', '#352242', '#946baf', '#f4d7ff'],
         ink='#e9dfef', accent='#d0a4ed', link='#a9cddd',
         grain=".,:;'!i/\\<>vxzXZMW%#@\u00d7\u00f7\u00b1\u039b\u0394\u03bb\u2261\u2248\u2237\u2234\u2235\u2571\u2572"),
    dict(id='reliquary-basalt', name='Reliquary / Basalt Crypt', parent='obsidian-reliquary',
         form='basalt', stops=['#080909', '#30302e', '#a39b86', '#f0e4c8'],
         ink='#e3dfd2', accent='#d1bea0', link='#a7c4bf',
         grain=".,:;!ijcrsanuoO08BMQW%#@&\u00b0\u00a4\u00a9\u00f8\u00d8\u00e6\u00df\u00b5\u00b6\u00a7"),
    dict(id='monolith-glacial', name='Monolith / Glacial Chamber', parent='glacier-monolith',
         form='glacial', stops=['#040b16', '#173951', '#5da8c4', '#e0faff'],
         ink='#dcecf2', accent='#a2e6fa', link='#c9c0ee',
         grain=".,:`'!il/\\<>xzXZYMW%#@\u00d7\u00f7\u00b1\u2261\u2248\u039b\u0394\u2571\u2572\u2573\u00d8"),
    dict(id='monolith-aurora', name='Monolith / Aurora Grotto', parent='glacier-monolith',
         form='aurora', stops=['#040d12', '#164440', '#68bda8', '#dcffee'],
         ink='#dceee8', accent='#a0ecd0', link='#b9c9ef',
         grain=".,:;!ijrscuoaO08BMQW%#@&\u00b0\u00a4\u00f8\u00d8\u00e6\u00b5\u00a7\u2248\u2237\u2295\u2297"),
)


def clamp(v, lo=0., hi=1.):
    return max(lo, min(hi, v))


def noise(x, y):
    return (math.sin(x*12.9898+y*78.233)*43758.5453) % 1


def colour(stops, value):
    # The material ramp is intentionally nonlinear: most area is midtone,
    # with sparse bright specular rims and small nearly-black cavities.
    pos = clamp(value)*(len(stops)-1)
    index = min(len(stops)-2, int(pos))
    t = pos-index
    a, b = (tuple(int(s[k:k+2], 16) for k in (1, 3, 5)) for s in stops[index:index+2])
    return '#'+''.join(f'{round(x+(y-x)*t):02x}' for x, y in zip(a, b))


def frame_height(form, x, y, width, height, band):
    """One continuous material frame; normals turn naturally around its corners."""
    radius = band*(.35 if form in ('gunmetal','glacial') else .95)
    qx=abs(x-width/2)-(width/2-radius)
    qy=abs(y-height/2)-(height/2-radius)
    distance=radius-(math.hypot(max(qx,0),max(qy,0))+min(max(qx,qy),0))
    if distance<0 or distance>band:
        return 0.
    t=distance/band
    # Outside bevel -> crown -> broad shoulder -> inward wall -> recessed opening.
    crown=min(clamp(t/.23),clamp((1-t)/.28))
    z=.12+2.7*crown
    if form=='bronze':
        z+=.45*math.sin(math.pi*t)
        z-=.35*math.exp(-((t-.57)/.045)**2)
        z-=.22*math.exp(-((t-.18)/.025)**2)
    elif form=='gunmetal':
        z= .12+2.6*min(clamp(t/.14),clamp((1-t)/.18))
        z-=.85*math.exp(-((t-.57)/.07)**6)
        z+=.025*math.sin(x*7)*math.cos(y*6)
    elif form=='amethyst':
        z=.10+3.2*min(clamp(t/.40),clamp((1-t)/.45))
        z-=.48*math.exp(-((t-.71)/.025)**2)
        # Broad asymmetric facets, never a repeating series of gems.
        z+=.35*((x/width)-(y/height))*math.sin(math.pi*t)
    elif form=='basalt':
        z=.12+3.15*math.sin(math.pi*t)**.63
        z+=.11*math.sin(x*1.7+y*.4)*math.cos(y*2.1-x*.5)
        z-=.33*math.exp(-((t-.80)/.045)**2)
    elif form=='glacial':
        z=.10+3.5*min(clamp(t/.52),clamp((1-t)/.48))
        z+=.45*(x/width-y/height)*math.sin(math.pi*t)
        # One oblique fracture traverses the slab instead of tiled crack icons.
        seam=x/width+y/height-.91
        z-=.65*math.exp(-(seam/.018)**2)*math.sin(math.pi*t)
    else:
        flow=.07*math.sin(math.atan2(y-height/2,x-width/2)*3)
        z=.12+2.9*math.sin(math.pi*t)**.65
        z-=.43*math.exp(-((t-.55-flow)/.04)**2)
        z+=.10*math.sin(x*.8+y*.4)*math.sin(math.pi*t)
    return max(.02,z)


def rail_height(form, x, y, width, height, vertical):
    cross=x/width if vertical else y/height
    along=y/height if vertical else x/width
    if not 0<cross<1 or not 0<along<1:
        return 0.
    crown=math.sin(math.pi*cross)
    if form in ('gunmetal','glacial','amethyst'):
        crown=min(cross,1-cross)*2
    # Shared end collars integrate the mullion into the surrounding frame.
    collar=.35*math.exp(-((along-.04)/.035)**2)+.35*math.exp(-((along-.96)/.035)**2)
    groove=.28*math.exp(-((cross-.50)/.055)**2)
    return .12+2.3*crown+collar-groove


def lighting(surface, x, y, form):
    z=surface(x,y)
    if z<=.01:
        return .015,0.,0.
    step=.14
    gx=(surface(x+step,y)-surface(x-step,y))/(2*step)
    gy=(surface(x,y+step)-surface(x,y-step))/(2*step)
    norm=math.sqrt(gx*gx+gy*gy+1)
    nx,ny,nz=-gx/norm,-gy/norm,1/norm
    lx,ly,lz=-.52,-.61,.60
    diffuse=max(0.,nx*lx+ny*ly+nz*lz)
    shadow=1.
    for i in range(1,9):
        d=i*.33
        if surface(x+lx*d,y+ly*d)>z+lz*d+.12:
            shadow=.25
            break
    occlusion=sum(max(0.,surface(x+ox,y+oy)-z-.1) for ox,oy in ((-.6,0),(.6,0),(0,-.6),(0,.6)))
    ao=math.exp(-occlusion*.18)
    spec=max(0.,nx*(-.29)+ny*(-.34)+nz*.895)**(22 if form=='gunmetal' else 12)
    return clamp((.19+.69*diffuse*shadow)*ao+.22*spec*shadow),gx,gy


def glyph_bank(spec, font):
    result = []
    for g in dict.fromkeys(spec['grain']):
        if wcswidth(g) != 1:
            continue
        tile = Image.new('L', (10, 21))
        ImageDraw.Draw(tile).text((0,0), g, font=font, fill=255)
        coverage = sum(tile.tobytes())/(255*210)
        if coverage > .001:
            result.append((coverage, g))
    return [g for _,g in sorted(result)]


def material_atlas(spec, font, width, height, band=None, vertical=False):
    bank=glyph_bank(spec,font)
    physical_width=width/2
    if band is not None:
        surface=lambda x,y:frame_height(spec['form'],x,y,physical_width,height,band)
    else:
        surface=lambda x,y:rail_height(spec['form'],x,y,physical_width,height,vertical)
    cells=[]
    for y in range(height):
        row=[]
        for x in range(width):
            value,gx,gy=lighting(surface,(x+.5)/2,y+.5,spec['form'])
            tone=min(TONES-1,round(value*(TONES-1)))
            rank=clamp(value*.87+(noise(x,y)-.5)*.16)
            glyph=bank[min(len(bank)-1,int(rank*len(bank)))]
            if abs(gx)>1.4 and noise(x+19,y)>.65:
                glyph=chr(0x2571) if gx<0 else chr(0x2572)
            elif spec['form']=='gunmetal' and .3<value<.7 and x%3==0:
                glyph=chr(0x2261) if abs(gy)>abs(gx) else '|'
            row.append((tone,glyph))
        cells.append(row)
    return cells


def build(spec,font):
    q=lambda v:json.dumps(v,ensure_ascii=True)
    parent=tomllib.loads((ROOT/'themes'/(spec['parent']+'.theme')).read_text())
    palette=parent['palette'] | dict(paper=spec['stops'][0],ink=spec['ink'],
        muted=colour(spec['stops'],.70),accent=spec['accent'],heading=spec['accent'],
        code=spec['accent'],link=spec['link'],frame=colour(spec['stops'],.43))
    out=['# Geometry-fitted sculpted environment; requires MDBM frame-fit support.',
         '# Shared upper-left light, connected corners, recessed openings and shaded mullions.',
         '# Source: tests/build_solid_relief_themes.py. Data only; no runtime shaders.',
         'schema = "md-editor-theme/1.1"',f'id = {q(spec["id"])}',f'name = {q(spec["name"])}',
         f'description = {q("Continuous "+spec["form"]+" environment with 32 paired foreground/background tones.")}',
         'seed = 73191','seed_mode = "fixed"','fps = 6','\n[palette]']
    out += [f'{k} = {q(v)}' for k,v in palette.items()]
    out += ['\n[geometry]','outer = [2, 3, 2, 3]','border = 3','divider = 2','spacer = 0']
    for role in ('background','text','muted','border','title','accent','code','heading','link','quote','table','diagnostic','tree','gutter','status','keyword','string','number','comment'):
        style=dict(parent['styles'][role])
        out += [f'\n[styles.{role}]']+[f'{k} = {q(v)}' for k,v in style.items()]
    for i in range(TONES):
        v=i/(TONES-1)
        out += [f'\n[styles.t{i:02}]',f'bg = {q(colour(spec["stops"],v*.88))}',f'fg = {q(colour(spec["stops"],min(1.,v*.90+.16)))}']
    definitions=(('architecture',120,60,6,False),('panel',80,40,6,False),
                 ('vertical',10,60,None,True),('horizontal',120,6,None,False))
    for name,width,height,band,vertical in definitions:
        cells=material_atlas(spec,font,width,height,band,vertical)
        out += [f'\n[art.{name}]',f'rows = {q(["".join(g for _,g in row) for row in cells])}',
                f'style_grid = {q([" ".join(f"t{t:02}" for t,_ in row) for row in cells])}']
    ids=[]
    for slot,name,art,insets in [('outer','architecture','architecture',[6,12,6,12]),
            ('tile.edit.chrome','edit','panel',[6,12,6,12]),
            ('tile.view.chrome','view','panel',[6,12,6,12]),
            ('tile.filesystem.chrome','filesystem','panel',[6,12,6,12]),
            ('tile.contents.chrome','contents','panel',[6,12,6,12]),
            ('divider.vertical','vertical-mullion','vertical',[0,0,0,0]),
            ('divider.horizontal','horizontal-mullion','horizontal',[0,0,0,0])]:
        ids.append(name)
        out += ['\n[[layers]]',f'id = {q(name)}',f'slot = {q(slot)}',f'art = {q(art)}',
                'fit = "frame"',f'frame_insets = {q(insets)}','mode = "replace"']
    out += ['\n[[variants]]','when = { high_contrast = true }',f'hide_layers = {q(ids)}',
            '\n[fallbacks.ascii]','glyph = "#"','\n[fallbacks.monochrome]','glyph = "."',
            '\n[fallbacks.narrow]','glyph = "."',f'hide_layers = {q(ids)}',
            '\n[fallbacks.reduced_motion]','glyph = "."']
    path=ROOT/'themes'/(spec['id']+'.theme')
    path.write_text('\n'.join(out)+'\n',encoding='ascii')
    return path


def main():
    candidates=(Path('C:/Windows/Fonts/consola.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'),Path('/System/Library/Fonts/Menlo.ttc'))
    path=next((p for p in candidates if p.exists()),None)
    if path is None:
        raise SystemExit('A monospace font is required to measure glyph coverage.')
    font=ImageFont.truetype(str(path),16)
    for spec in SPECS:
        print(build(spec,font))


if __name__=='__main__':
    main()
