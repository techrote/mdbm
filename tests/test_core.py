import copy, hashlib, itertools, json, random
from pathlib import Path
import pytest
import mdbm as m

@pytest.mark.parametrize('text', ['abc','a\t界e\u0301','\x1b]52;c;attack\x07','x\u202ereverse','👩\u200d💻','\u0301','x\x00\x9b31m'])
def test_display_is_safe(text):
    rendered=m.display_text(text)
    assert not any(m.unsafe_codepoint(c) for c in rendered)
    assert all(u.width in (1,2,3,4,5,6,7,8) for u in m.display_units(text))
    c=m.Canvas(30,2);c.text(0,0,text,m.Ink(),10)
    assert all(not any(m.unsafe_codepoint(ch) for ch in cell.glyph) for cell in c.cells)

@pytest.mark.parametrize('depth',['truecolour','256','16','mono'])
def test_colour_styles_closed(depth):
    style=m.Ink('#fab123','#172533',bold=True).ptk(depth)
    assert '\x1b' not in style
    if depth=='mono': assert 'fg:' not in style and 'bg:' not in style


def test_wide_overwrite_and_protection():
    c=m.Canvas(8,2); c.put(1,0,'界',m.Ink(),10)
    assert c.get(2,0).continuation
    c.put(2,0,'x',m.Ink(),10)
    assert c.get(1,0).glyph==' ' and c.get(2,0).glyph=='x'
    c.put(3,0,'!',m.Ink(),100)
    for mode in ('replace','underlay','glyph-only','foreground-only','background-only','mask','transparent-cell','dither-blend'):
        c.put(3,0,'x',m.Ink('#ff0000'),0,mode)
        assert c.get(3,0).glyph=='!'
    c.put(2,0,'界',m.Ink(),0)
    assert c.get(3,0).glyph=='!'


def test_canvas_local_damage():
    a=m.Canvas(10,3);b=m.Canvas(10,3)
    b.put(4,1,'x',m.Ink(),10)
    assert b.changed(a)==1
    assert b.changed(b)==0


def test_document_edit_fuzz():
    rng=random.Random(67); d=m.DocumentModel('a\nb\nc\n'); reference=d.text
    for _ in range(1000):
        a,b=sorted((rng.randrange(len(reference)+1),rng.randrange(len(reference)+1)))
        value=''.join(rng.choice('abc\n界é') for _ in range(rng.randrange(8)))
        d.replace_range(a,b,value);reference=reference[:a]+value+reference[b:]
        assert d.text==reference
        lines=reference.split('\n')
        assert d.line_count==len(lines)
        assert [d.line(i) for i in range(d.line_count)]==lines
        assert list(d._line_starts)==[0]+[i+1 for i,ch in enumerate(reference) if ch=='\n']


def test_document_undo_redo_and_dirty():
    d=m.DocumentModel('abc'); d.move_to(3); d.insert('界'); d.insert('\nend')
    assert d.dirty and d.text=='abc界\nend'
    d.undo(); assert d.text=='abc界'
    d.undo(); assert d.text=='abc' and not d.dirty
    d.redo();d.redo();assert d.text=='abc界\nend'


def test_grapheme_edit_selection():
    d=m.DocumentModel('Ae\u0301界Z');d.move_to(3);d.delete(True)
    assert d.text=='A界Z'
    d.move_to(1);d.move('right',True);assert d.selected_text()=='界'
    d.insert('x');assert d.text=='AxZ'


def test_config_individual_fallback(tmp_path):
    c=m.load_config(tmp_path/'missing',{'schema':'mdbm-config/1.0','layout':{'left_ratio':99,'view_ratio':.7,'view_first':'false'},'scripts':{'allowlist':['allowed.py']}})
    assert c.get('layout','left_ratio')==.3 and c.get('layout','view_ratio')==.7
    assert c.get('layout','view_first') is True
    assert len(c.diagnostics)==2


@pytest.mark.parametrize('size',[(1,1),(8,4),(20,8),(40,12),(80,24),(120,40),(200,60)])
@pytest.mark.parametrize('flags',list(itertools.product([False,True],repeat=5)))
def test_all_geometry_modes(size,flags):
    theme=m.ThemeCompiler().compile(copy.deepcopy(m.MINIMAL_THEME));conf=m.load_config(raw=m.DEFAULT_CONFIG)
    left,right,edit,view,first=flags
    state=m.reduce_layout(m.LayoutState(left_visible=left,right_visible=right,edit_visible=edit,view_visible=view,view_first=first),'noop')
    g=m.solve_geometry(*size,state,conf,theme)
    for rect in itertools.chain(g.tiles.values(),g.contents.values(),g.dividers.values()):
        assert rect.w>=0 and rect.h>=0
        if rect.w and rect.h:
            assert rect.x>=0 and rect.y>=0 and rect.right<=size[0] and rect.bottom<=size[1]
    for axis,rect in g.dividers.items(): assert (rect.w if axis=='left_ratio' else rect.h)==1
    tiles=list(g.tiles.values())
    for i,r in enumerate(tiles):
        for other in tiles[i+1:]: assert not r.intersect(other).w or not r.intersect(other).h


def test_visibility_memories(workspace):
    w=workspace;w.memories[m.Tile.EDIT].scroll=3;w.doc.move_to(4,True)
    selection=w.doc.selection
    for cmd in ('tile.swap','tile.switch_left','tile.hide','tile.toggle_right','tile.restore'):
        w.dispatch(cmd);w.frame(120,40)
    assert w.doc.selection==selection
    assert w.registry.parity_errors(w.hits)==[]


def test_contents_and_compact_render(workspace):
    w=workspace;w.dispatch('tile.contents')
    for size in [(120,40),(40,12),(20,8),(8,4)]:
        w.frame(*size)
        assert w.registry.parity_errors(w.hits)==[]
    w.dispatch('tile.restore');w.dispatch('tile.toggle_left');w.dispatch('tile.toggle_right');w.frame(40,12)
    assert any(h.command=='tile.restore' for h in w.hits)


def test_binding_diagnostics(workspace):
    w=workspace;w.config.data['bindings']={'f8':'theme.choose','c-p':'file.trash','f9':'not.a.command','banana':'theme.choose'}
    w.build_bindings()
    assert any('Emergency' in d for d in w.diagnostics)
    assert any('unknown command' in d for d in w.diagnostics)
    assert any('Unreachable' in d for d in w.diagnostics)


def test_move_to_snaps_inside_cluster():
    doc=m.DocumentModel('Ae\u0301B\n界')
    doc.move_to(2);assert doc.cursor==1
    doc.move_to(3);assert doc.cursor==3
