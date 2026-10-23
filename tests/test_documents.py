import json
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


def test_multi_document_open_activate_and_deduplicate(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'; c=tmp_path/'c.md'
    a.write_text('a\n'); b.write_text('b\n'); c.write_text('c\n')
    w=make_workspace(tmp_path)
    w.set_document(loaded(w,a))
    first=w.active_document_id
    w.doc.move_to(len(w.doc.text)); w.doc.insert('unsaved-a')
    w.memories[m.Tile.EDIT].scroll=7
    cursor=w.doc.cursor

    w.open_path(b); second=w.active_document_id
    w.doc.move_to(len(w.doc.text)); w.doc.insert('unsaved-b')
    w.open_path(c)
    assert len(w.documents)==3

    w.activate_document(first)
    assert w.doc.path==a and w.doc.text.endswith('unsaved-a')
    assert w.doc.cursor==cursor
    assert w.memories[m.Tile.EDIT].scroll==7

    count=len(w.documents)
    w.open_path(a)
    assert w.active_document_id==first and len(w.documents)==count
    w.activate_document(second)
    assert w.doc.path==b and w.doc.text.endswith('unsaved-b')


def test_save_is_scoped_to_active_document(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'
    a.write_text('A'); b.write_text('B')
    w=make_workspace(tmp_path)
    w.set_document(loaded(w,a)); aid=w.active_document_id
    w.open_path(b); bid=w.active_document_id
    w.doc.move_to(1); w.doc.insert('2'); w.save()
    assert b.read_text()=='B2' and a.read_text()=='A'

    w.activate_document(aid); w.doc.move_to(1); w.doc.insert('1'); w.save()
    assert a.read_text()=='A1' and b.read_text()=='B2'
    assert w._state_by_id(bid).doc.text=='B2'


def test_dirty_close_guards_only_target_document(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'
    a.write_text('A'); b.write_text('B')
    w=make_workspace(tmp_path)
    w.set_document(loaded(w,a)); aid=w.active_document_id
    w.doc.insert('dirty')
    w.open_path(b); bid=w.active_document_id

    w.close_document(aid)
    assert w.active_document_id==aid
    assert w.overlays and w.overlays[-1].kind=='confirm'
    overlay=w.overlays[-1]
    row=next(i for i,(label,_) in enumerate(overlay.actions) if label=='Discard')
    w.overlay_action({'row':row})
    assert w._state_by_id(aid) is None
    assert w._state_by_id(bid) is not None and w.doc.path==b


def test_quit_walks_every_dirty_document(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'
    a.write_text('A'); b.write_text('B')
    w=make_workspace(tmp_path)
    w.set_document(loaded(w,a)); w.doc.insert('dirty-a')
    w.open_path(b); w.doc.insert('dirty-b')

    w.request_quit()
    assert w.overlays and not w.exit_requested
    first=w.overlays[-1]
    row=next(i for i,(label,_) in enumerate(first.actions) if label=='Discard')
    w.overlay_action({'row':row})
    assert w.overlays and not w.exit_requested

    second=w.overlays[-1]
    row=next(i for i,(label,_) in enumerate(second.actions) if label=='Discard')
    w.overlay_action({'row':row})
    assert w.exit_requested


def test_session_restores_document_set_active_document_and_view_state(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'; c=tmp_path/'c.md'
    for path,text in ((a,'a\n'),(b,'b\n'),(c,'c\n')): path.write_text(text)
    w=make_workspace(tmp_path,persist=True)
    w.set_document(loaded(w,a))
    w.open_path(b); bid=w.active_document_id
    w.memories[m.Tile.EDIT].scroll=5
    w.doc.cursor=1
    w.open_path(c)
    w.activate_document(bid)
    w.persist_session()
    assert w.store is not None
    w.store.close()

    restored=make_workspace(tmp_path,restore=True,persist=True)
    assert len(restored.documents)==3
    assert restored.active_document_id==bid
    assert restored.doc.path==b and restored.doc.cursor==1
    assert restored.memories[m.Tile.EDIT].scroll==5
    assert restored.store is not None
    restored.store.close()


def test_recovery_bundle_keeps_document_identity_and_fingerprints(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'
    a.write_text('A'); b.write_text('B')
    w=make_workspace(tmp_path,persist=True)
    w.set_document(loaded(w,a)); w.doc.move_to(1); w.doc.insert(' dirty-a')
    w.open_path(b); w.doc.move_to(1); w.doc.insert(' dirty-b')
    w.persist_recovery()
    assert w.store is not None
    record=w.store.read(w.store.recovery_name)
    assert record['schema']=='mdbm-recovery/2.0'
    assert len(record['documents'])==2
    w.store.close()

    a.write_text('external-a')
    restored=make_workspace(tmp_path)
    restored.restore_recovery_bundle(record)
    sa=restored._state_by_path(a); sb=restored._state_by_path(b)
    assert sa is not None and sb is not None
    assert sa.doc.text=='A dirty-a' and sb.doc.text=='B dirty-b'
    assert sa.doc.dirty and sb.doc.dirty
    try:
        restored.files.save(a,sa.doc.text,sa.doc.fingerprint)
    except m.ConflictError:
        pass
    else:
        raise AssertionError('recovered fingerprint must retain external-change protection')


def test_legacy_single_document_session_remains_readable(tmp_path):
    note=tmp_path/'legacy.md'; note.write_text('legacy')
    state=tmp_path/'.mdbm'; state.mkdir()
    (state/'session.json').write_text(json.dumps({
        'schema':'mdbm-session/1.0','root':str(tmp_path),
        'active_file':str(note),'cursor':3,'anchor':1,'theme':'minimal',
    }))
    w=make_workspace(tmp_path,restore=True,persist=True)
    assert w.doc.path==note and w.doc.cursor==3 and w.doc.anchor==1
    assert len(w.documents)==1
    assert w.store is not None
    w.store.close()
