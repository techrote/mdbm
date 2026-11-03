"""Workspace and appearance transactions preserve work and committed state."""
import copy
import dataclasses

import pytest

import mdbm as m


def select_item(w, command):
    overlay=w.overlays[-1]
    overlay.index=next(i for i,item in enumerate(overlay.items) if item.command==command)
    w.handle_key('enter','input.enter')


def select_preset(w, key, commit=False):
    overlay=w.overlays[-1]
    overlay.index=next(i for i,item in enumerate(overlay.items) if item.payload==key)
    w.preview_workspace(overlay)
    if commit: w.handle_key('enter','input.enter')


def test_workspace_preview_preserves_models_memories_and_committed_layout(workspace):
    w=workspace
    w.doc.move_to(7,True)
    w.memories[m.Tile.CONTENTS].filter='Two'
    w.contents_collapsed.add('one')
    before=w.session_data()
    doc=w.doc
    memories={tile:dataclasses.asdict(memory) for tile,memory in w.memories.items()}
    selection=w.doc.selection
    w.choose_workspace(); select_preset(w,'reading')
    w.frame(120,40)
    assert w.layout.visible()==(m.Tile.CONTENTS,m.Tile.VIEW)
    assert w.session_data()['layout']==before['layout']
    assert w.doc is doc and w.doc.selection==selection
    assert {t:dataclasses.asdict(v) for t,v in w.memories.items()}==memories
    w.escape()
    assert w.session_data()['layout']==before['layout']
    assert w._workspace_preview is None and not w.overlays
    assert w.contents_collapsed=={'one'}


def test_workspace_click_previews_then_commits_without_click_through(workspace):
    w=workspace; original=w.layout
    w.choose_workspace(); w.frame(120,40)
    overlay=w.overlays[-1]
    index=next(i for i,item in enumerate(overlay.items) if item.payload=='writing')
    hit=next(h for h in w.hits if h.command=='overlay.row' and h.payload['row']==index)
    w.mouse('down',hit.rect.x,hit.rect.y); w.mouse('up',hit.rect.x,hit.rect.y)
    assert w.overlays[-1] is overlay and w.layout.visible()==(m.Tile.EDIT,)
    assert w.session_data()['layout']==w._layout_record(original)
    w.mouse('down',hit.rect.x,hit.rect.y); w.mouse('up',hit.rect.x,hit.rect.y)
    assert not w.overlays and w.layout.visible()==(m.Tile.EDIT,)
    assert w.session_data()['layout']==w._layout_record(w.layout)
    w.choose_workspace(); select_preset(w,'reading'); w.frame(120,40)
    generation=w.doc.generation
    w.mouse('down',0,39); w.mouse('up',0,39)
    assert not w.overlays and w.layout.visible()==(m.Tile.EDIT,)
    assert w.doc.generation==generation


def test_emergency_restore_and_palette_cancel_workspace_preview(workspace):
    w=workspace; original=w.layout
    w.choose_workspace(); select_preset(w,'reading')
    w.handle_key('c-p','command.palette')
    assert w.layout==original and w.overlays[-1].kind=='palette'
    w.escape(); w.choose_workspace(); select_preset(w,'writing')
    w.handle_key('c-t','tile.restore')
    assert w.layout==m.reduce_layout(original,'restore')
    assert not w.overlays and w._workspace_preview is None


@pytest.mark.parametrize('tile',list(m.Tile))
def test_maximize_restore_preserves_layout_session_and_memories(workspace,tile):
    w=workspace; w.set_layout('focus',tile)
    original=w.layout; memories=copy.deepcopy(w.memories)
    w.toggle_maximize(tile)
    assert w.layout.visible()==(tile,)
    assert w.session_data()['layout']==w._layout_record(original)
    w.toggle_maximize()
    assert w.layout==original and w.memories==memories
    other=m.Tile.VIEW if tile==m.Tile.EDIT else m.Tile.EDIT
    w.toggle_maximize(tile); w.set_layout('focus',other)
    assert w._maximized_layout is None
    assert w.layout==m.reduce_layout(original,'focus',other)


def test_clicking_maximized_tile_keeps_its_geometry_and_focus(workspace):
    w=workspace; w.toggle_maximize(m.Tile.EDIT); original=w.layout
    w.frame(80,24)
    hit=next(h for h in w.hits if h.command=='edit.point')
    w.mouse('down',hit.rect.x+4,hit.rect.y); w.mouse('up',hit.rect.x+4,hit.rect.y)
    assert w.layout==original and w._maximized_layout is not None
    assert w.layout.visible()==(m.Tile.EDIT,)


def test_cancel_workspace_preview_restores_maximized_state(workspace):
    w=workspace; original=w.layout
    w.toggle_maximize(m.Tile.VIEW); maximized=w.layout
    w.choose_workspace(); select_preset(w,'review'); w.escape()
    assert w.layout==maximized and w._maximized_layout==original
    assert w.session_data()['layout']==w._layout_record(original)


def test_custom_workspaces_are_bounded_validated_and_replace_is_confirmed(workspace):
    w=workspace; original=w.layout
    w.save_workspace('My writing desk')
    w.apply_workspace('reading'); w.save_workspace('My writing desk')
    assert w.overlays[-1].kind=='confirm'
    assert w.custom_workspaces['My writing desk']==original
    w.escape()
    for index in range(7): w.save_workspace('Desk '+str(index))
    with pytest.raises(m.MDBMError,match='Eight custom'): w.save_workspace('Ninth')
    for name in ('','x'*49,'bad\x1bname','bad\nname'):
        with pytest.raises(m.MDBMError): w.save_workspace(name)
    record=w.session_data()
    w.custom_workspaces={}; w._restore_layout(record)
    assert len(w.custom_workspaces)==8
    w.apply_workspace('saved:My writing desk'); assert w.layout==original
    w.remove_workspace('My writing desk')
    assert w.overlays[-1].index==1
    w.overlay_action({'row':0})
    assert 'My writing desk' not in w.custom_workspaces


def test_invalid_saved_workspaces_fail_independently(workspace):
    w=workspace; good=w._layout_record(w.layout)
    w._restore_layout({'workspace_presets':{
        'Valid':good,'Infinite':{**good,'left_ratio':float('inf')},
        'Boolean ratio':{**good,'left_ratio':True},'Extra':{**good,'script':'execute'},
        'Invalid tile':{**good,'left_tile':'edit'},'bad\x07name':good}})
    assert list(w.custom_workspaces)==['Valid']


def test_appearance_preview_serializes_only_committed_preferences(workspace):
    w=workspace; before=w._appearance_preferences(); text=w.doc.text
    w.choose_appearance()
    for command in ('theme.high_contrast','theme.reduced_motion','theme.motion',
                    'theme.ascii','theme.colour_depth','theme.spacing','theme.immersive'):
        select_item(w,command)
    assert w._appearance_preferences()!=before
    assert w.session_data()['appearance_preferences']==before
    assert w.doc.text==text and w.overlays[-1].kind=='settings'
    w.handle_key('escape','input.escape')
    assert w._appearance_preferences()==before and w._appearance_preview is None


def test_appearance_apply_and_palette_cancel_use_same_commands(workspace):
    w=workspace
    w.choose_appearance(); select_item(w,'theme.high_contrast')
    select_item(w,'theme.appearance_apply')
    assert not w.overlays and w.config.get('accessibility','high_contrast')
    committed=w.session_data()['appearance_preferences']
    w.choose_appearance(); select_item(w,'theme.ascii')
    w.handle_key('c-p','command.palette')
    assert w._appearance_preferences()==committed
    assert w.overlays[-1].kind=='palette' and w._appearance_preview is None


def test_appearance_roundtrip_and_invalid_fields_are_independent(workspace,tmp_path):
    w=workspace
    w._restore_appearance({'motion':'off','high_contrast':True,'ascii_only':True,
        'colour_depth':'mono','outer':[2,2,2,2],'reduced_motion':True,'immersive':True})
    record=w.session_data()['appearance_preferences']
    conf=m.load_config(tmp_path/'config',raw={'schema':'mdbm-config/1.0'})
    restored=m.Workspace(conf,restore=False,persist=False)
    restored._restore_appearance(record)
    assert restored._appearance_preferences()==record
    assert restored.config.motion=='off'  # Accessibility must never re-enable motion.
    restored._restore_appearance({'motion':'unsafe','high_contrast':'false','outer':[-1,0,0,0],
        'colour_depth':None,'ascii_only':False,'unrecognized':'ignored'})
    assert not restored.config.ascii
    assert restored.config.motion=='off' and restored.config.depth=='mono'
    assert restored.config.get('artspace','outer')==[2,2,2,2]
    assert restored.config.get('accessibility','high_contrast')


def test_committed_preferences_and_presets_restore_without_rewriting_config(tmp_path):
    path=tmp_path/'mdbm.conf'
    source='schema = "mdbm-config/1.0"\n[application]\nroot = "."\nstate_directory = ".mdbm"\n'
    path.write_text(source,encoding='utf-8')
    w=m.Workspace(m.load_config(path),restore=False)
    try:
        w.choose_appearance(); select_item(w,'theme.high_contrast')
        w.persist_session()
        assert not w.store.read('session.json')['appearance_preferences']['high_contrast']
        select_item(w,'theme.appearance_apply')
        w.apply_workspace('reading'); w.save_workspace('Reading desk')
    finally: w.store.close()
    restored=m.Workspace(m.load_config(path))
    try:
        assert restored.config.get('accessibility','high_contrast')
        assert restored.layout.visible()==(m.Tile.CONTENTS,m.Tile.VIEW)
        assert restored.custom_workspaces['Reading desk']==restored.layout
        assert path.read_text(encoding='utf-8')==source
    finally: restored.store.close()


def test_new_commands_have_menu_palette_and_keyboard_routes(workspace):
    w=workspace
    commands=('workspace.choose','workspace.save','workspace.remove','tile.maximize',
              'theme.appearance','theme.reduced_motion','theme.colour_depth','theme.spacing')
    menus=[item.command for name in ('Tile','Theme') for item in w.menu_items(name)]
    for command in commands:
        assert command in menus
        assert w.registry.commands[command].visible and w.registry.commands[command].keyboard_path
    assert w.registry.commands['tile.maximize'].keys==('escape m',)
    w.tile_context(m.Tile.VIEW)
    assert any(item.command=='tile.maximize' and item.payload=='view' for item in w.overlays[-1].items)


@pytest.mark.parametrize('kind',['workspace','appearance'])
@pytest.mark.parametrize('size',[(20,6),(40,12),(80,24),(120,40),(200,60)])
def test_new_overlays_have_bounded_full_row_hits(workspace,kind,size):
    w=workspace
    if kind=='workspace': w.choose_workspace()
    else: w.choose_appearance()
    w.frame(*size)
    assert not w.registry.parity_errors(w.hits)
    assert all(0<=h.rect.x<h.rect.right<=size[0] and 0<=h.rect.y<h.rect.bottom<=size[1] for h in w.hits)
    rows=[h for h in w.hits if h.command=='overlay.row']
    assert rows
    assert all(h.rect.w==w.overlays[-1].rect.w-2 for h in rows)
    w.handle_key('pagedown','navigate.pagedown'); w.frame(*size)
    w.handle_key('escape','input.escape'); assert not w.overlays


@pytest.mark.parametrize('size',[(40,12),(80,24),(120,40),(200,60)])
@pytest.mark.parametrize('profile',['truecolour','ansi256','ansi16','monochrome','ascii','reduced','high-contrast'])
def test_quiet_study_capabilities_and_protected_controls(tmp_path,size,profile):
    from render_support import scene
    w,canvas=scene(tmp_path,'quiet-study',profile,*size)
    assert w.theme.id=='quiet-study' and not w.catalog.diagnostics
    assert not w.theme.animations
    assert canvas.get(1,0).ink==m.HOST_FOCUS
    assert not w.registry.parity_errors(w.hits)
    assert canvas.operations<=canvas.budget
    if profile=='ascii': assert all(all(ord(c)<128 for c in cell.glyph) for cell in canvas.cells)
    w.clock=1000; repeated=w.frame(*size)
    assert repeated.digest()==canvas.digest()
    assert w.last_damage==0
