from pathlib import Path

import mdbm as m


def make_workspace(tmp_path, *, restore=False, persist=False):
    conf=m.load_config(tmp_path/'mdbm.conf',raw={
        'schema':'mdbm-config/1.0',
        'application':{'root':str(tmp_path),'state_directory':str(tmp_path/'.mdbm')},
        'appearance':{'theme':'minimal','seed':17,'motion':'off'},
        'capabilities':{'colour_depth':'truecolour'},
    })
    return m.Workspace(conf,restore=restore,persist=persist)


def loaded(w, path):
    return m.DocumentModel.loaded(w.files.load(path))


def hit_for(w, command, document_id):
    return next(h for h in reversed(w.hits)
                if h.command==command and h.payload==document_id)


def click(w, hit):
    w.mouse('down',hit.rect.x,hit.rect.y)
    w.mouse('up',hit.rect.x,hit.rect.y)


def test_labels_active_dirty_disambiguation_and_three_tabs(tmp_path):
    left=tmp_path/'left'; right=tmp_path/'right'
    left.mkdir(); right.mkdir()
    a=left/'note.md'; b=right/'note.md'
    a.write_text('A'); b.write_text('B')
    w=make_workspace(tmp_path)
    w.set_document(loaded(w,a)); aid=w.active_document_id
    w.open_path(b); bid=w.active_document_id
    w.new_document(); uid=w.active_document_id
    w.doc.insert('dirty')

    labels=dict(w.document_tab_labels())
    assert labels[aid]=='left/note.md'
    assert labels[bid]=='right/note.md'
    assert labels[uid]=='Untitled'
    canvas=w.frame(120,30)
    assert len([h for h in w.hits if h.command=='document.activate'])==3
    active=hit_for(w,'document.activate',uid)
    assert canvas.get(active.rect.x,1).ink==m.HOST_SELECTED
    assert '*' in canvas.plain().splitlines()[1]
    assert w.registry.parity_errors(w.hits)==[]


def test_multiple_untitled_documents_have_stable_readable_labels(tmp_path):
    w=make_workspace(tmp_path)
    w.set_document(m.DocumentModel())
    w.new_document(); w.new_document()
    assert [label for _,label in w.document_tab_labels()]==[
        'Untitled','Untitled 2','Untitled 3']


def test_next_previous_cycle_in_document_order(tmp_path):
    paths=[tmp_path/f'{name}.md' for name in ('a','b','c')]
    for path in paths: path.write_text(path.stem)
    w=make_workspace(tmp_path)
    w.set_document(loaded(w,paths[0]))
    ids=[w.active_document_id]
    for path in paths[1:]:
        w.open_path(path); ids.append(w.active_document_id)
    w.activate_document(ids[0])
    w.dispatch('document.next'); assert w.active_document_id==ids[1]
    w.dispatch('document.next'); assert w.active_document_id==ids[2]
    w.dispatch('document.next'); assert w.active_document_id==ids[0]
    w.dispatch('document.previous'); assert w.active_document_id==ids[2]


def test_mouse_activation_clean_close_and_focus_preservation(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'; c=tmp_path/'c.md'
    for path in (a,b,c): path.write_text(path.stem)
    w=make_workspace(tmp_path)
    w.set_document(loaded(w,a)); aid=w.active_document_id
    w.open_path(b); bid=w.active_document_id
    w.open_path(c); cid=w.active_document_id
    w.set_layout('focus',m.Tile.VIEW)
    w.frame(100,24)

    click(w,hit_for(w,'document.activate',aid))
    assert w.active_document_id==aid
    assert w.layout.focus==m.Tile.VIEW

    w.frame(100,24)
    click(w,hit_for(w,'document.close',bid))
    assert w._state_by_id(bid) is None
    assert w._state_by_id(aid) is not None and w._state_by_id(cid) is not None
    assert w.active_document_id==aid
    assert w.layout.focus==m.Tile.VIEW


def test_mouse_dirty_close_uses_existing_guard(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'
    a.write_text('A'); b.write_text('B')
    w=make_workspace(tmp_path)
    w.set_document(loaded(w,a)); aid=w.active_document_id
    w.doc.insert('dirty')
    w.open_path(b)
    w.frame(90,24)

    click(w,hit_for(w,'document.close',aid))
    assert w._state_by_id(aid) is not None
    assert w.active_document_id==aid
    assert w.overlays and w.overlays[-1].kind=='confirm'
    assert 'Unsaved changes'==w.overlays[-1].title


def test_save_as_and_rename_update_labels_immediately(tmp_path):
    old=tmp_path/'old.md'; old.write_text('old')
    w=make_workspace(tmp_path)
    w.set_document(m.DocumentModel('new text'))
    uid=w.active_document_id
    assert dict(w.document_tab_labels())[uid]=='Untitled'
    saved=tmp_path/'saved.md'
    w.perform_save(saved,None)
    assert dict(w.document_tab_labels())[uid]=='saved.md'

    w.set_document(loaded(w,old)); oid=w.active_document_id
    rows=w.tree_rows()
    w.memories[m.Tile.FILESYSTEM].row=next(i for i,row in enumerate(rows) if row[0]==old)
    w.rename_selected()
    overlay=w.overlays[-1]
    assert overlay.accept is not None
    overlay.accept('renamed.md')
    assert w._state_by_id(oid).doc.path==tmp_path/'renamed.md'
    assert dict(w.document_tab_labels())[oid]=='renamed.md'


def test_session_restores_tab_order_and_active_document(tmp_path):
    paths=[tmp_path/f'{name}.md' for name in ('one','two','three')]
    for path in paths: path.write_text(path.stem)
    w=make_workspace(tmp_path,persist=True)
    w.set_document(loaded(w,paths[0])); ids=[w.active_document_id]
    for path in paths[1:]:
        w.open_path(path); ids.append(w.active_document_id)
    w.activate_document(ids[1]); w.persist_session()
    assert w.store is not None; w.store.close()

    restored=make_workspace(tmp_path,restore=True,persist=True)
    assert [label for _,label in restored.document_tab_labels()]==['one.md','two.md','three.md']
    assert restored.active_document_id==ids[1]
    canvas=restored.frame(100,24)
    active=hit_for(restored,'document.activate',ids[1])
    assert canvas.get(active.rect.x,1).ink==m.HOST_SELECTED
    assert restored.store is not None; restored.store.close()


def test_active_tab_visible_under_narrow_overflow_and_compact_geometry(tmp_path):
    paths=[]
    for i in range(7):
        folder=tmp_path/f'directory-{i}'; folder.mkdir()
        path=folder/f'long-document-name-{i}.md'; path.write_text(str(i)); paths.append(path)
    w=make_workspace(tmp_path)
    w.set_document(loaded(w,paths[0])); ids=[w.active_document_id]
    for path in paths[1:]:
        w.open_path(path); ids.append(w.active_document_id)
    target=ids[3]; w.activate_document(target)

    canvas=w.frame(24,8)
    assert w.geometry is not None and w.geometry.compact
    active=hit_for(w,'document.activate',target)
    assert active.rect.y==1 and active.rect.right<=24
    row=canvas.plain().splitlines()[1]
    assert '<' in row or '>' in row
    assert all(rect.y>=2 for rect in w.geometry.tiles.values())
    assert w.registry.parity_errors(w.hits)==[]


def test_document_commands_are_palette_visible_with_portable_fallback(tmp_path):
    w=make_workspace(tmp_path)
    for cid in ('document.next','document.previous','document.close'):
        command=w.registry.commands[cid]
        assert command.visible
        assert command.keyboard_path.startswith('Ctrl+P')
        assert command.keys==()
    assert not w.registry.commands['file.close'].visible
