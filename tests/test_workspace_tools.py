"""User-facing multi-document workflows and their cancellation boundaries."""
import pytest

import mdbm as m


def make_workspace(tmp_path, names=('one.md', 'two.md', 'three.md')):
    conf=m.load_config(tmp_path/'mdbm.conf',raw={
        'schema':'mdbm-config/1.0',
        'application':{'root':str(tmp_path),'state_directory':str(tmp_path/'.mdbm')},
        'appearance':{'theme':'minimal','motion':'off'},
    })
    w=m.Workspace(conf,restore=False,persist=False)
    for index,name in enumerate(names):
        path=tmp_path/name; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('original '+name+'\n',encoding='utf-8')
        if index==0: w.set_document(m.DocumentModel.loaded(w.files.load(path)))
        else: w.open_path(path)
    return w


def dirty_all(w):
    for state in w.documents:
        state.doc.move_to(0); state.doc.insert('changed ')


def accept_text(w, text):
    assert w.overlays[-1].editor is not None
    w.handle_key('c-a','edit.select_all'); w.handle_text(text,paste=True)
    w.handle_key('enter','input.enter')


def test_document_picker_filters_paths_and_preserves_cancelled_state(tmp_path):
    w=make_workspace(tmp_path,('left/note.md','right/note.md'))
    before=w.active_document_id
    w.doc.move_to(4,True); selection=w.doc.selection
    w.documents[0].doc.insert('unsaved ')
    w.set_layout('focus',m.Tile.VIEW)
    w.handle_key('c-b','document.switch')
    w.handle_text('LEFT note',paste=True)
    picker=w.overlays[-1]
    assert len(picker.items)==1 and picker.items[0].label.startswith('* ')
    assert 'left' in picker.items[0].label
    w.handle_key('escape','input.escape')
    assert w.active_document_id==before and w.doc.selection==selection
    assert w.layout.focus==m.Tile.VIEW

    w.choose_document(); w.handle_text('left')
    w.frame(80,24)
    hit=next(h for h in w.hits if h.command=='overlay.row')
    w.mouse('down',hit.rect.x,hit.rect.y); w.mouse('up',hit.rect.x,hit.rect.y)
    assert w.active_document_id==w.documents[0].id and not w.overlays
    assert w.layout.focus==m.Tile.VIEW and w.doc.dirty


def test_document_picker_empty_filter_and_paste_do_not_open_paths(tmp_path):
    w=make_workspace(tmp_path); before=w.active_document_id
    unopened=tmp_path/'unopened.md'; unopened.write_text('do not open')
    w.choose_document(); w.handle_text(str(unopened),paste=True)
    assert len(w.overlays[-1].items)==1 and not w.overlays[-1].items[0].enabled
    w.overlay_accept()
    assert w.overlays and w.active_document_id==before and len(w.documents)==3
    w.handle_key('c-a','edit.select_all'); w.handle_key('backspace','edit.backspace')
    assert len(w.overlays[-1].items)==3


def test_oversized_picker_input_is_rejected_without_losing_overlay(tmp_path):
    w=make_workspace(tmp_path); w.choose_document(); picker=w.overlays[-1]
    w.handle_text('x'*257,paste=True)
    assert w.notice_error and picker.editor.text=='' and w.overlays[-1] is picker
    w.clipboard='x'*257; w.handle_key('c-v','edit.paste')
    assert picker.editor.text=='' and len(picker.items)==3
    w.escape(); w.doc.max_chars=len(w.doc.text)
    before=w.doc.text; w.handle_text('x',paste=True)
    assert w.doc.text==before and w.notice_error


def test_keyboard_dialog_error_is_reported_without_crashing(tmp_path):
    w=make_workspace(tmp_path); w.dispatch('file.open')
    accept_text(w,'missing.md')
    assert w.notice_error and len(w.documents)==3


def test_new_commands_are_available_through_palette_and_menus(tmp_path):
    w=make_workspace(tmp_path)
    assert w.registry.commands['document.switch'].keys==('c-b',)
    for cid in ('document.switch','document.search','file.save_all'):
        assert w.registry.commands[cid].visible
        assert any(item.command==cid for name in ('File','Navigate') for item in w.menu_items(name))
    w.choose_document(); w.frame(30,10)
    assert w.registry.parity_errors(w.hits)==[]
    assert all(h.rect.right<=30 and h.rect.bottom<=10 for h in w.hits)


def test_search_reads_unsaved_buffers_and_reveals_exact_source(tmp_path):
    w=make_workspace(tmp_path)
    first=w.documents[0]; second=w.documents[1]
    first.doc.replace_range(0,len(first.doc.text),'# First\n界 literal [x] here\n')
    second.doc.replace_range(0,len(second.doc.text),'a literal [x]\n')
    w.search_documents('[x]')
    picker=w.overlays[-1]
    assert len(picker.items)==2
    assert picker.items[0].label.startswith('one.md:2:11')
    assert picker.items[1].label.startswith('two.md:1:11')
    assert '[x]' not in (tmp_path/'one.md').read_text()
    w.overlay_accept()
    assert w.active_document_id==first.id and w.doc.selected_text()=='[x]'
    assert w.layout.focus==m.Tile.EDIT and w.search_term=='[x]'
    assert not w.overlays and first.doc.dirty and second.doc.dirty


@pytest.mark.parametrize('change',['edit','replace','close'])
def test_search_results_refuse_stale_buffer_identity(tmp_path,change):
    w=make_workspace(tmp_path)
    target=w.documents[0]; original=w.active_document_id
    w.search_documents('original'); result=w.overlays[-1].items[0]
    if change=='edit': target.doc.insert('prefix ')
    elif change=='replace': target.doc=m.DocumentModel(target.doc.text,target.doc.path)
    else: w._drop_document_states([target.id])
    w.dispatch(result.command,result.payload)
    assert w.active_document_id==original
    assert w.notice_error and 'Search results changed' in w.notice


def test_search_is_literal_case_sensitive_and_has_bounded_results(tmp_path):
    w=make_workspace(tmp_path,('one.md',))
    w.doc.replace_range(0,len(w.doc.text),'a.* A.* '+'x '*250)
    w.search_documents('a.*')
    assert len(w.overlays[-1].items)==1
    w.escape(); w.search_documents('x')
    assert len(w.overlays[-1].items)==m.MAX_DOCUMENT_MATCHES
    assert '200+' in w.overlays[-1].title
    assert 'First 200 matches' in w.overlays[-1].message
    w.escape(); w.search_documents('not present')
    assert not w.overlays[-1].items[0].enabled
    w.escape(); w.search_documents('')
    assert not w.overlays


def test_search_bounds_snippets_and_sanitizes_source_controls(tmp_path):
    w=make_workspace(tmp_path,('one.md',))
    w.doc.replace_range(0,len(w.doc.text),'z'*100_000+'needle\x1b]52;attack\x07'+'z'*100_000)
    w.search_documents('needle')
    label=w.overlays[-1].items[0].label
    assert len(label)<180 and not any(m.unsafe_codepoint(c) for c in label)


def test_save_all_publishes_dirty_buffers_and_restores_active_document(tmp_path):
    w=make_workspace(tmp_path); dirty_all(w)
    original=w.documents[1].id; w.activate_document(original)
    w.doc.move_to(5,True); selected=w.doc.selection
    w.set_layout('focus',m.Tile.VIEW)
    w.dispatch('file.save_all')
    assert not any(state.doc.dirty for state in w.documents)
    assert all(state.doc.path.read_text()==state.doc.text for state in w.documents)
    assert w.active_document_id==original and w.doc.selection==selected
    assert w.layout.focus==m.Tile.VIEW and not w.overlays
    assert 'Saved all 3' in w.notice
    w.dispatch('file.save_all'); assert 'already saved' in w.notice


def test_save_all_stops_at_cancelled_conflict_without_rollback(tmp_path):
    w=make_workspace(tmp_path); dirty_all(w)
    changed=tmp_path/'two.md'; changed.write_text('external change',encoding='utf-8')
    last=(tmp_path/'three.md').read_text()
    w.dispatch('file.save_all')
    assert not w.documents[0].doc.dirty and w.documents[1].doc.dirty
    assert w.overlays[-1].title=='File conflict'
    w.overlay_accept()  # Default Cancel.
    assert not w.overlays
    assert changed.read_text()=='external change'
    assert (tmp_path/'three.md').read_text()==last and w.documents[2].doc.dirty
    assert w.active_document_id==w.documents[1].id


def test_save_all_continues_only_after_explicit_conflict_resolution(tmp_path):
    w=make_workspace(tmp_path); dirty_all(w)
    (tmp_path/'two.md').write_text('external change')
    original=w.active_document_id
    w.save_all(); assert w.overlays[-1].title=='File conflict'
    w.overlay_action({'row':0})  # Explicit Overwrite.
    assert not any(state.doc.dirty for state in w.documents)
    assert w.active_document_id==original and not w.overlays


def test_save_all_untitled_cancel_and_save_as_resume(tmp_path):
    w=make_workspace(tmp_path,('one.md',))
    w.new_document(); w.doc.insert('new unsaved text'); untitled=w.active_document_id
    later=tmp_path/'later.md'; later.write_text('later'); w.open_path(later); w.doc.insert('changed ')
    original=w.active_document_id
    w.save_all(); assert w.overlays[-1].title=='Save As'
    w.escape()
    assert w._state_by_id(untitled).doc.dirty and later.read_text()=='later'
    w.activate_document(original); w.save_all(); accept_text(w,'created.md')
    assert (tmp_path/'created.md').read_text()=='new unsaved text'
    assert later.read_text()=='changed later' and w.active_document_id==original
    assert not w.overlays


def test_save_all_refuses_open_save_as_destination_and_stops(tmp_path):
    w=make_workspace(tmp_path,('one.md',)); w.new_document(); w.doc.insert('draft')
    later=tmp_path/'later.md'; later.write_text('later'); w.open_path(later); w.doc.insert('changed ')
    w.dispatch('file.save_all')
    overlay=w.overlays[-1]; overlay.editor.replace_range(0,len(overlay.editor.text),'one.md')
    # Real event dispatch reports the guarded refusal instead of bypassing it.
    w.dispatch('overlay.accept')
    assert w.notice_error and 'already open' in w.notice
    assert (tmp_path/'one.md').read_text()=='original one.md\n'
    assert later.read_text()=='later'


def test_save_all_file_error_stops_remaining_documents(tmp_path,monkeypatch):
    w=make_workspace(tmp_path); dirty_all(w)
    save=w.files.save
    def fail_second(path,*args,**kwargs):
        if path.name=='two.md': raise m.PathError('read-only destination')
        return save(path,*args,**kwargs)
    monkeypatch.setattr(w.files,'save',fail_second)
    w.dispatch('file.save_all')
    assert w.notice_error and 'read-only' in w.notice
    assert not w.documents[0].doc.dirty and all(s.doc.dirty for s in w.documents[1:])
    assert (tmp_path/'three.md').read_text()=='original three.md\n'
