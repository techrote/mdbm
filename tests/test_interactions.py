import time
import pytest
import mdbm as m


def click(w, hit, button='left'):
    w.mouse('down',hit.rect.x,hit.rect.y,button)
    w.mouse('up',hit.rect.x,hit.rect.y,button)


def editor_xy(w,line,col):
    r=w.geometry.contents[m.Tile.EDIT]
    gutter=min(len(str(w.doc.line_count))+2,max(0,r.w//3))
    return r.x+gutter+col,r.y+line-w.memories[m.Tile.EDIT].scroll


def test_focus_and_land_in_single_click(workspace):
    w=workspace;w.dispatch('tile.filesystem');w.frame(120,40)
    x,y=editor_xy(w,2,3);w.mouse('down',x,y);w.mouse('up',x,y)
    assert w.layout.focus==m.Tile.EDIT and w.doc.cursor==w.doc.line_start(2)+3


def test_right_click_selection_preservation(workspace):
    w=workspace;start=w.doc.line_start(2);w.doc.anchor=start;w.doc.cursor=start+5
    selection=w.doc.selection;x,y=editor_xy(w,2,2)
    w.mouse('down',x,y,'right');assert w.doc.selection==selection and w.overlays
    w.frame(120,40);w.escape();x,y=editor_xy(w,2,9)
    w.mouse('down',x,y,'right');assert w.doc.selection is None and w.doc.cursor==start+9


def test_double_triple_click_and_drag_cancel(workspace):
    w=workspace;x,y=editor_xy(w,2,2)
    for _ in range(2):w.mouse('down',x,y);w.mouse('up',x,y)
    assert w.doc.selected_text()=='alpha'
    w.mouse('down',x,y);w.mouse('up',x,y)
    assert w.doc.selected_text()=='alpha beta\n'
    w.last_click=(0,-1,-1,'',0);before=(w.doc.cursor,w.doc.anchor)
    w.mouse('down',x,y);w.mouse('move',x+6,y);w.escape()
    assert (w.doc.cursor,w.doc.anchor)==before and w.capture is None


@pytest.mark.parametrize('axis',['left_ratio','view_ratio'])
@pytest.mark.parametrize('button',['left','right'])
def test_divider_capture_rollback_commit(workspace,axis,button):
    w=workspace;r=w.geometry.dividers[axis];original=w.layout
    w.mouse('down',r.x,r.y,button)
    x=r.x+9 if axis=='left_ratio' else r.x;y=r.y+3 if axis=='view_ratio' else r.y
    w.mouse('move',x,y,button);assert w.capture and w.layout!=original
    w.escape();assert w.layout==original and w.resize_commits==0
    w.frame(120,40);w.mouse('down',r.x,r.y,button);w.mouse('move',x,y,button);w.frame(120,40)
    w.mouse('up',x,y,button);assert w.resize_commits==1 and w.capture is None
    w.mouse('up',x,y,button);assert w.resize_commits==1


def test_outside_overlay_click_consumed(workspace):
    w=workspace;w.doc.move_to(2,True);before=(w.layout,w.doc.cursor,w.doc.anchor)
    w.open_menu('File');w.frame(120,40)
    r=w.geometry.contents[m.Tile.EDIT];w.mouse('down',r.right-1,r.bottom-1)
    assert not w.overlays and (w.layout,w.doc.cursor,w.doc.anchor)==before


@pytest.mark.parametrize('command',['menu.file','command.palette','theme.choose','file.open','help.shortcuts','edit.replace'])
def test_overlay_buttons_are_direct_targets(workspace,command):
    w=workspace;w.dispatch(command);w.frame(80,24)
    h=next(h for h in reversed(w.hits) if h.command=='overlay.cancel')
    for delta in range(h.rect.w):
        w.mouse('down',h.rect.x+delta,h.rect.y)
        assert not w.overlays
        if delta<h.rect.w-1:w.dispatch(command);w.frame(80,24)


def test_menu_pointer_activation(workspace):
    w=workspace;w.open_menu('Tile');w.frame(120,40)
    overlay=w.overlays[-1];row=next(i for i,x in enumerate(overlay.items) if x.command=='tile.swap')
    hit=next(h for h in w.hits if h.command=='overlay.row' and h.payload['row']==row)
    old=w.layout.view_first;click(w,hit)
    assert not w.overlays and w.layout.view_first!=old


def test_theme_preview_cancel_commit(workspace):
    w=workspace;prior=w.theme.id;w.choose_theme();w.frame(120,40)
    o=w.overlays[-1];i=next(i for i,x in enumerate(o.items) if x.payload=='dense-glitch-mosaic')
    h=next(h for h in w.hits if h.command=='overlay.row' and h.payload['row']==i)
    click(w,h);assert w.theme.id=='dense-glitch-mosaic' and w.overlays
    w.escape();assert w.theme.id==prior
    w.choose_theme();w.frame(120,40);o=w.overlays[-1]
    o.index=next(i for i,x in enumerate(o.items) if x.payload=='neon-circuitry')
    w.overlay_accept();assert w.theme.id=='neon-circuitry' and not w.overlays


def test_submenu_hover_does_not_move_keyboard_selection(workspace):
    w=workspace;w.open_menu('Tile',anchor=(110,35));w.frame(120,40)
    o=w.overlays[-1];o.index=2;i=0;x=o.rect.x+2;y=o.list_y+i
    w.mouse('move',x,y);assert o.index==2
    assert w.hover_due
    w.tick_once(w.hover_due[2]+.01);w.frame(120,40)
    assert len(w.overlays)==2 and o.index==2
    assert all(x.rect.right<=120 and x.rect.bottom<=40 for x in w.overlays)
    w.escape();assert len(w.overlays)==1


def test_palette_filter_keyboard_activation(workspace):
    w=workspace;w.open_palette();w.handle_text('Swap View');w.frame(120,40)
    assert len(w.overlays[-1].items)==1
    before=w.layout.view_first;w.handle_key('enter','input.enter')
    assert w.layout.view_first!=before and not w.overlays


def test_dirty_quit_default_cancel(workspace):
    w=workspace;w.doc.insert('dirty');w.dispatch('file.quit');w.frame(80,24)
    assert w.overlays[-1].kind=='confirm'
    w.handle_key('enter','input.enter');assert not w.exit_requested and w.doc.dirty
    w.dispatch('file.quit');o=w.overlays[-1]
    index=next(i for i,(label,_) in enumerate(o.actions) if label=='Discard')
    w.overlay_action({'row':index});assert w.exit_requested


def test_preview_click_preserves_selection_and_rejects_stale(workspace):
    w=workspace;w.doc.anchor=0;w.doc.cursor=3;before=w.doc.selection
    w.view_point({'row':4,'button':'left','x':0,'y':0})
    assert w.doc.selection==before
    old=w.preview;generation=w.doc.generation;width=w.preview_width
    result=(w.parsed,w.preview);w.set_document(m.DocumentModel('# Different\n'))
    assert not w.accept_preview(generation,width,result)
    w.doc.move_to(2);before=w.doc.cursor;w.view_point({'row':4,'button':'left','x':0,'y':0})
    assert w.doc.cursor==before


def test_tile_memories_mouse_scroll_and_blur(workspace):
    w=workspace;w.set_document(m.DocumentModel('\n'.join(str(i) for i in range(100))))
    w.frame(120,40);x,y=editor_xy(w,0,0)
    old=w.doc.cursor;w.mouse('scroll_down',x,y);assert w.doc.cursor==old
    w.mouse('down',x,y);assert w.capture
    w.terminal_focus(False);assert not w.capture and not w.terminal_focused


def test_drag_autoscroll(workspace):
    w=workspace;w.set_document(m.DocumentModel('\n'.join('line '+str(i) for i in range(100))))
    w.frame(120,40);x,y=editor_xy(w,0,0);r=w.geometry.contents[m.Tile.EDIT]
    w.mouse('down',x,y);w.mouse('move',x,r.bottom+3)
    before=w.memories[m.Tile.EDIT].scroll
    w.tick_once();assert w.memories[m.Tile.EDIT].scroll>before
    assert w.doc.selection


def test_vim_profile_and_bracketed_paste(workspace):
    w=workspace;w.dispatch('edit.profile');assert w.vim_mode=='normal'
    w.handle_text('i');w.handle_text('X');w.handle_key('escape','input.escape');assert w.doc.text.startswith('X')
    before=w.doc.text;w.handle_text('hello',paste=True);assert w.doc.text!=before
    w.handle_key('c-r','edit.replace');assert not w.overlays


def test_theme_preview_does_not_persist(workspace):
    w=workspace; prior=w.theme.id
    w.choose_theme(); overlay=w.overlays[-1]
    overlay.index=next(i for i,item in enumerate(overlay.items) if item.payload!=prior)
    w.preview_theme(overlay)
    assert w.theme.id!=prior
    assert w.session_data()['theme']==prior
    w.activate_overlay_item(overlay)
    assert w.session_data()['theme']==w.theme.id!=prior


def test_palette_cancels_old_modal_intent_and_preview(workspace):
    w=workspace; prior=w.theme.id; called=[]
    w.choose_theme(); chooser=w.overlays[-1]
    chooser.index=next(i for i,item in enumerate(chooser.items) if item.payload!=prior)
    w.preview_theme(chooser)
    w.confirm('Delete?', 'Old intent',[('Do it',lambda:called.append(True)),('Cancel',None)],default=1)
    w.open_palette()
    assert len(w.overlays)==1 and w.overlays[0].kind=='palette'
    assert w.theme.id==prior and not called


def test_preview_replaces_cached_links(workspace):
    w=workspace
    w.set_document(m.DocumentModel('[Old](https://example.org/old)'));w.parse_sync()
    w.select_link(1);assert w._view_links
    w.set_document(m.DocumentModel('[New](https://example.org/new)'));w.parse_sync()
    assert not w._view_links
    w.select_link(1);assert w._view_links==['https://example.org/new']


def test_stale_contents_and_links_are_held(workspace):
    w=workspace;assert w.contents_rows()
    w.doc.insert('changed')
    assert w.contents_rows()==[]
    w.activate_link('https://example.org/stale')
    assert not w.overlays and 'updating' in w.notice
