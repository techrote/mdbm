import copy, itertools, random
from pathlib import Path
import pytest
import mdbm as m


def basic(): return copy.deepcopy(m.MINIMAL_THEME)

@pytest.mark.parametrize('path',sorted((m.APP_DIR/'themes').glob('*.theme')))
def test_reference_themes(path):
    t=m.ThemeCompiler().compile(m.ThemeCompiler.read(path));assert t.layers
    cfg=m.load_config(raw=m.DEFAULT_CONFIG)
    for width,height in [(40,12),(80,24),(120,40),(200,60)]:
        for depth,unicode,motion in [('truecolour',True,'normal'),('256',True,'normal'),('16',True,'reduced'),('mono',False,'off')]:
            ctx=m.VisualContext(width,height,depth,unicode,motion=motion,seed=17)
            g=m.solve_geometry(width,height,m.LayoutState(),cfg,t)
            a=m.Canvas(width,height);b=m.Canvas(width,height)
            m.ThemePainter(t).paint(a,g,ctx);m.ThemePainter(t).paint(b,g,ctx)
            assert a.digest()==b.digest()
            assert a.operations<=a.budget
            if not unicode: assert all(all(ord(ch)<128 for ch in cell.glyph) for cell in a.cells)

@pytest.mark.parametrize('field',['scripts','commands','shortcuts','bindings','python','ansi','unknown'])
def test_executable_and_unknown_fields_rejected(field):
    data=basic();data[field]={'run':'malicious'}
    with pytest.raises(m.ThemeError):m.ThemeCompiler().compile(data)

@pytest.mark.parametrize('char',['\x00','\x1b','\x07','\x9b','\u202e','\u2066','\ud800','\u200d'])
def test_theme_unicode_injection(char):
    data=basic();data['description']='bad'+char
    with pytest.raises(m.ThemeError):m.ThemeCompiler().compile(data)

@pytest.mark.parametrize('field',['glyphs','art','patterns','motifs','masks','styles','palette','ramps','geometry','layers','rules','animations','variants','fallbacks'])
@pytest.mark.parametrize('value',[42,'bad',None,True])
def test_malformed_shapes_are_theme_errors(field,value):
    data=basic();data[field]=value
    with pytest.raises(m.ThemeError):m.ThemeCompiler().compile(data)


def test_cycles_and_legacy():
    a=basic();a['id']='a';a['extends']='b'
    b=basic();b['id']='b';b['extends']='a'
    with pytest.raises(m.ThemeError):m.ThemeCompiler({'a':a,'b':b}).compile(a)
    a=basic();a['palette']={'a':'b','b':'a'}
    with pytest.raises(m.ThemeError):m.ThemeCompiler().compile(a)
    a={'schema':'md-editor-theme/1.0','id':'old','name':'Old','textures':{'mesh':{'kind':'grid','glyphs':['+']}}}
    assert m.ThemeCompiler().compile(a).id=='old'
    a['scripts']=[]
    with pytest.raises(m.ThemeError):m.ThemeCompiler().compile(a)


def test_resource_limits():
    data=basic();data['art']={'a':{'rle':['999999:a'],'legend':{'a':'x'}}}
    with pytest.raises(m.ThemeError):m.ThemeCompiler().compile(data)
    data=basic();data['description']='x'*(m.MAX_STRING_BYTES+1)
    with pytest.raises(m.ThemeError):m.ThemeCompiler().compile(data)
    data=basic();data['patterns']={'dots':{'kind':'noise','density':0.0,'glyphs':['.']}}
    data['layers']=[{'id':f'layer-{i}','slot':'outer','pattern':'dots','offset':[400,400],'repeat':False} for i in range(64)]
    theme=m.ThemeCompiler().compile(data);cfg=m.load_config(raw=m.DEFAULT_CONFIG)
    g=m.solve_geometry(120,40,m.LayoutState(),cfg,theme)
    canvas=m.Canvas(120,40,budget=100)
    m.ThemePainter(theme).paint(canvas,g,m.VisualContext(120,40))
    assert canvas.operations==100 and canvas.budget_exhausted


@pytest.mark.parametrize('kind',sorted(m.PATTERN_KINDS))
def test_pattern_determinism(kind):
    data=basic();data['patterns']={'p':{'kind':kind,'glyphs':['+','.',':']}}
    theme=m.ThemeCompiler().compile(data);pattern=theme.patterns['p']
    cells=[m.pattern_sample(pattern,x,y,20,10,67) for y in range(10) for x in range(20)]
    assert cells==[m.pattern_sample(pattern,x,y,20,10,67) for y in range(10) for x in range(20)]


def test_art_grid_rle_transform():
    data=basic();data.update({'art':{'a':{'grid':['a b','b a'],'legend':{'a':'+','b':'-'}},'b':{'rle':['2:a 2:b'],'legend':{'a':'.','b':':'}}},
      'glyphs':{'signs':{'symbols':['+','-'],'mirror':{'+':'+','-':'-'},'rotate90':{'+':'+','-':'-'}}},
      'layers':[{'id':'a','slot':'outer','art':'a','family':'signs','transform':'mirror'}]})
    theme=m.ThemeCompiler().compile(data)
    assert theme.art['a']==(('+','-'),('-','+')) and theme.art['b']==(('.', '.', ':', ':'),)


def test_motion_and_safe_context():
    data=basic();data['patterns']={'p':{'kind':'grid'}};data['layers']=[{'id':'p','slot':'outer','pattern':'p'}]
    data['animations']=[{'id':'a','target':'layer.p','property':'density','values':[0.0,1.0],'duration':1.0}]
    theme=m.ThemeCompiler().compile(data);painter=m.ThemePainter(theme)
    assert 'text' not in m.VisualContext.__dataclass_fields__ and 'path' not in m.VisualContext.__dataclass_fields__
    ctx=m.VisualContext(80,24,motion='off',time=99)
    assert painter.layer_properties(theme.layers[0],ctx)==painter.layer_properties(theme.layers[0],m.VisualContext(80,24,motion='off'))


def test_spatial_tracks_use_theme_frame_cadence_but_flash_tracks_stay_capped():
    data=basic();data['fps']=12
    data['patterns']={'p':{'kind':'grid','glyphs':['.','+']}}
    data['styles']['alt']={'fg':'#ffffff','bg':'#000000'}
    data['layers']=[{'id':'p','slot':'outer','pattern':'p'}]
    data['animations']=[
        {'id':'move','target':'layer.p','property':'offset','values':[[0,0],[1,0],[2,0],[3,0],[4,0],[5,0]],'duration':1.0,'loop':True},
        {'id':'flash','target':'layer.p','property':'style','values':['border','alt'],'duration':1.0,'loop':True},
    ]
    theme=m.ThemeCompiler().compile(data);p=m.ThemePainter(theme);layer=theme.layers[0]
    a=p.layer_properties(layer,m.VisualContext(80,24,time=0.10,motion='normal'))
    b=p.layer_properties(layer,m.VisualContext(80,24,time=0.20,motion='normal'))
    # Spatial motion advances inside a 0.5 s window.
    assert a['offset'] != b['offset']
    # Flash-like style transitions stay in the same <=2 Hz sample bucket.
    assert a['style'] == b['style']


def test_new_animated_reference_themes_change_and_reduced_motion_freezes():
    for name in ('maintenance-ecosystem','relay-moth-swarm','diagnostic-aurora'):
        path=m.APP_DIR/'themes'/(name+'.theme')
        theme=m.ThemeCompiler().compile(m.ThemeCompiler.read(path));p=m.ThemePainter(theme)
        cfg=m.load_config(raw=m.DEFAULT_CONFIG);g=m.solve_geometry(120,40,m.LayoutState(),cfg,theme)
        a=m.Canvas(120,40);b=m.Canvas(120,40)
        p.paint(a,g,m.VisualContext(120,40,time=0.2,motion='normal',seed=17,focus='edit',immersive=True))
        p.paint(b,g,m.VisualContext(120,40,time=1.1,motion='normal',seed=17,focus='edit',immersive=True))
        assert a.digest()!=b.digest(), name
        c=m.Canvas(120,40);d=m.Canvas(120,40)
        p.paint(c,g,m.VisualContext(120,40,time=0.2,motion='reduced',seed=17,focus='edit',immersive=True))
        p.paint(d,g,m.VisualContext(120,40,time=9.7,motion='reduced',seed=17,focus='edit',immersive=True))
        assert c.digest()==d.digest(), name


def test_event_burst_is_finite_and_cue_scoped():
    theme=m.ThemeCompiler().compile(m.ThemeCompiler.read(m.APP_DIR/'themes'/'diagnostic-aurora.theme'))
    layer=next(x for x in theme.layers if x['id']=='save-arc');p=m.ThemePainter(theme)
    assert not p.layer_properties(layer,m.VisualContext(80,24,time=2,cue='startup'))['visible']
    assert p.layer_properties(layer,m.VisualContext(80,24,time=2.2,cue='save-success',cue_time=2.0))['visible']
    assert not p.layer_properties(layer,m.VisualContext(80,24,time=4.0,cue='save-success',cue_time=2.0))['visible']
