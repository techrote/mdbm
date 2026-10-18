"""Geometry-aware art preserves corner lighting, cell styles and host masks."""
import copy

import pytest

import mdbm as m


def frame_theme(slot='outer'):
    raw=copy.deepcopy(m.MINIMAL_THEME)
    raw['styles'].update({f'c{i}':{'fg':f'#{i*9:02x}aabb','bg':f'#{i*9:02x}1122'} for i in range(25)})
    raw['art']={'frame':{'rows':['ABCDE','FGHIJ','KLMNO','PQRST','UVWXY'],
        'style_grid':[' '.join(f'c{y*5+x}' for x in range(5)) for y in range(5)]}}
    raw['geometry']={'border':1,'divider':2}
    raw['layers']=[{'id':'frame','slot':slot,'art':'frame','fit':'frame',
                    'frame_insets':[1,1,1,1] if slot=='outer' else [0,0,0,0]}]
    return raw


@pytest.mark.parametrize('width,height',[(80,24),(144,58),(200,60)])
def test_fitted_outer_corners_keep_identity_and_background(width,height):
    theme=m.ThemeCompiler().compile(frame_theme())
    cfg=m.load_config(raw=m.DEFAULT_CONFIG)
    geometry=m.solve_geometry(width,height,m.LayoutState(),cfg,theme)
    canvas=m.Canvas(width,height)
    m.ThemePainter(theme).paint(canvas,geometry,m.VisualContext(width,height))
    for x,y,glyph,index in [(0,1,'A',0),(width-1,1,'E',4),
                            (0,height-2,'U',20),(width-1,height-2,'Y',24)]:
        cell=canvas.get(x,y)
        assert cell.glyph==glyph
        assert cell.ink.bg==f'#{index*9:02x}1122'
    assert canvas.get(width//2,1).glyph in 'BCD'
    assert not canvas.budget_exhausted


def test_compact_frame_is_clipped_to_remaining_decoration():
    theme=m.ThemeCompiler().compile(frame_theme())
    geo=m.solve_geometry(40,12,m.LayoutState(),m.load_config(raw=m.DEFAULT_CONFIG),theme)
    canvas=m.Canvas(40,12)
    m.ThemePainter(theme).paint(canvas,geo,m.VisualContext(40,12))
    for y in range(12):
        for x in range(40):
            if canvas.get(x,y).glyph!=' ':
                assert any(rect.contains(x,y) for rect in geo.slots['outer'])
    assert not canvas.budget_exhausted


@pytest.mark.parametrize('slot,axis', [('divider.vertical','left_ratio'),('divider.horizontal','view_ratio')])
def test_divider_lighting_spans_both_sides_of_protected_handle(slot,axis):
    theme=m.ThemeCompiler().compile(frame_theme(slot))
    cfg=m.load_config(raw=m.DEFAULT_CONFIG)
    geo=m.solve_geometry(144,58,m.LayoutState(),cfg,theme)
    handle=geo.dividers[axis]
    canvas=m.Canvas(144,58)
    canvas.put(handle.x,handle.y,'!',m.HOST_FOCUS,100)
    m.ThemePainter(theme).paint(canvas,geo,m.VisualContext(144,58))
    assert canvas.get(handle.x,handle.y).glyph=='!'
    if axis=='left_ratio':
        assert canvas.get(handle.x-2,handle.y).glyph=='A'
        assert canvas.get(handle.right+1,handle.y).glyph=='E'
    else:
        assert canvas.get(handle.x,handle.y-2).glyph=='A'
        assert canvas.get(handle.x,handle.bottom+1).glyph=='U'


def test_frame_does_not_overwrite_content_or_protected_cells():
    theme=m.ThemeCompiler().compile(frame_theme())
    geo=m.solve_geometry(80,24,m.LayoutState(),m.load_config(raw=m.DEFAULT_CONFIG),theme)
    canvas=m.Canvas(80,24)
    canvas.put(0,1,'!',m.HOST_FOCUS,100)
    content=geo.contents[m.Tile.EDIT]
    canvas.put(content.x,content.y,'Q',m.HOST_FOCUS,10)
    m.ThemePainter(theme).paint(canvas,geo,m.VisualContext(80,24))
    assert canvas.get(0,1).glyph=='!'
    assert canvas.get(content.x,content.y).glyph=='Q'


@pytest.mark.parametrize('grid', [None, [], ['c0'], ['c0 c1 c2 c3 c4']*4,
    ['missing missing missing missing missing']*5, [17]*5])
def test_invalid_style_grid_is_rejected(grid):
    raw=frame_theme();raw['art']['frame']['style_grid']=grid
    with pytest.raises(m.ThemeError):m.ThemeCompiler().compile(raw)


@pytest.mark.parametrize('change', [
    {'fit':'execute'}, {'frame_insets':[3,1,3,1]}, {'offset':[1,0]},
    {'slot':'behind-content'}, {'slot':'divider'},
    {'fit':'none','frame_insets':[1,1,1,1]},
])
def test_invalid_frame_contract_is_rejected(change):
    raw=frame_theme();raw['layers'][0].update(change)
    with pytest.raises(m.ThemeError):m.ThemeCompiler().compile(raw)


def test_frame_offset_rules_and_tracks_are_rejected():
    raw=frame_theme()
    raw['rules']=[{'target':'layer.frame','set':{'offset':[1,1]}}]
    with pytest.raises(m.ThemeError):m.ThemeCompiler().compile(raw)
    del raw['rules']
    raw['animations']=[{'id':'move','target':'layer.frame','property':'offset',
                        'values':[[0,0],[1,1]],'duration':2.0}]
    with pytest.raises(m.ThemeError):m.ThemeCompiler().compile(raw)


def test_style_cells_share_the_decoded_resource_budget():
    raw=copy.deepcopy(m.MINIMAL_THEME)
    raw['art']={f'a{i}':{'rows':['x'*128]*128,
        'style_grid':[' '.join(['text']*128)]*128} for i in range(9)}
    with pytest.raises(m.ThemeError,match='262144'):
        m.ThemeCompiler().compile(raw)
