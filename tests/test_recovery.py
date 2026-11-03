import copy
from pathlib import Path

import pytest

import mdbm as m


def config(root):
    return m.load_config(root/'mdbm.conf', raw={
        'schema':'mdbm-config/1.0',
        'application':{'root':str(root),'state_directory':str(root/'.mdbm')},
        'appearance':{'theme':'minimal','motion':'off'},
    })


def dirty_snapshot(root, *, session=True):
    a=root/'a.md'; b=root/'b.md'
    a.write_text('disk-a',encoding='utf-8'); b.write_text('disk-b',encoding='utf-8')
    w=m.Workspace(config(root),a,restore=False)
    w.doc.move_to(len(w.doc.text)); w.doc.insert(' recovered-a')
    w.open_path(b); w.doc.move_to(len(w.doc.text)); w.doc.insert(' recovered-b')
    w.persist_recovery()
    if session: w.persist_session()
    record=w.recovery_data()
    w.store.close()
    return a,b,record


def choose(w, label):
    overlay=w.overlays[-1]
    index=next(i for i,(text,_) in enumerate(overlay.actions) if text==label)
    w.overlay_action({'row':index})


def saved_texts(w):
    return [item['text'] for _,record in w.store.recovery_candidates(w.files.max_bytes)
            for item in (record['documents'] if record['schema']=='mdbm-recovery/2.0' else [record])]


def test_startup_recovery_rebinds_active_session_document(tmp_path):
    a,b,record=dirty_snapshot(tmp_path)
    w=m.Workspace(config(tmp_path))
    try:
        assert w.doc.path==b and w.doc.text=='disk-b'
        choose(w,'Restore')
        assert w.doc is w._active_state().doc
        assert w.doc.text=='disk-b recovered-b' and w.doc.dirty
        assert w._state_by_path(a).doc.text=='disk-a recovered-a'
        persisted=w.store.read(w.store.recovery_name)
        assert {d['text'] for d in persisted['documents']}=={'disk-a recovered-a','disk-b recovered-b'}
        assert a.read_text()=='disk-a' and b.read_text()=='disk-b'
    finally:
        w.store.close()


def test_explicit_file_recovery_preserves_unselected_siblings(tmp_path):
    a,b,record=dirty_snapshot(tmp_path)
    w=m.Workspace(config(tmp_path),a)
    try:
        assert '1 other document(s) remain saved for later' in w.overlays[-1].message
        choose(w,'Restore')
        assert w.doc.path==a and w.doc.text=='disk-a recovered-a'
        assert w._state_by_path(b) is None
        assert sorted(saved_texts(w))==['disk-a recovered-a','disk-b recovered-b']
        # Discarding the selected document on exit must not discard its sibling.
        w._exit()
        assert saved_texts(w)==['disk-b recovered-b']
    finally:
        w.store.close()
    other=m.Workspace(config(tmp_path),b)
    try:
        choose(other,'Restore')
        assert other.doc.text=='disk-b recovered-b' and other.doc.dirty
        assert b.read_text()=='disk-b'
    finally:
        other.store.close()


def test_explicit_file_discard_preserves_unselected_siblings(tmp_path):
    a,b,record=dirty_snapshot(tmp_path)
    w=m.Workspace(config(tmp_path),a)
    try:
        choose(w,'Discard selected')
        assert saved_texts(w)==['disk-b recovered-b']
        assert w.doc.path==a and w.doc.text=='disk-a' and not w.doc.dirty
        w._exit()
        assert saved_texts(w)==['disk-b recovered-b']
    finally:
        w.store.close()


@pytest.mark.parametrize('restore',[False,True])
def test_deferred_snapshot_survives_new_autosnapshot_and_clean_exit(tmp_path,restore):
    a,b,record=dirty_snapshot(tmp_path)
    w=m.Workspace(config(tmp_path),a,restore=restore)
    try:
        if restore: choose(w,'Later')
        w.doc.insert('new session '); w.persist_recovery()
        assert set(saved_texts(w))=={'disk-a recovered-a','disk-b recovered-b','new session disk-a'}
        w._exit()
        assert set(saved_texts(w))=={'disk-a recovered-a','disk-b recovered-b'}
    finally:
        w.store.close()


def test_corrupt_later_record_does_not_partially_restore(tmp_path):
    a,b,record=dirty_snapshot(tmp_path)
    w=m.Workspace(config(tmp_path),a,restore=False,persist=False)
    record=copy.deepcopy(record)
    record['documents'][1]['digest']='0'*64
    before=w.session_data(); original=w.doc
    with pytest.raises(m.MDBMError,match='integrity'):
        w.restore_recovery_bundle(record)
    assert w.session_data()==before and w.doc is original
    assert w.doc is w._active_state().doc and w.doc.text=='disk-a'
    assert len(w.documents)==1


def test_recovery_does_not_overwrite_current_dirty_document(tmp_path):
    a,b,record=dirty_snapshot(tmp_path)
    w=m.Workspace(config(tmp_path),a,restore=False,persist=False)
    w.doc.insert('current unsaved ')
    with pytest.raises(m.MDBMError,match='current unsaved'):
        w.restore_recovery_bundle(record)
    assert w.doc.text=='current unsaved disk-a' and len(w.documents)==1


def test_recovered_identity_collision_gets_a_distinct_live_id(tmp_path):
    a,b,record=dirty_snapshot(tmp_path)
    w=m.Workspace(config(tmp_path),a,restore=False,persist=False)
    original_id=w.active_document_id
    b_record=copy.deepcopy(record['documents'][1]); b_record['document_id']=original_id
    record=dict(record,documents=[b_record],active_document=original_id)
    w.restore_recovery_bundle(record)
    assert w.doc.path==b and w.doc.text=='disk-b recovered-b'
    assert w.active_document_id!=original_id
    assert len({state.id for state in w.documents})==2
    w.activate_document(original_id)
    assert w.doc.path==a and w.doc.text=='disk-a'


def test_recovery_capacity_failure_leaves_document_set_unchanged(tmp_path,monkeypatch):
    a,b,record=dirty_snapshot(tmp_path)
    w=m.Workspace(config(tmp_path),a,restore=False,persist=False)
    monkeypatch.setattr(m,'MAX_OPEN_DOCUMENTS',2)
    w.new_document()
    before=w.session_data(); original=w.doc
    with pytest.raises(m.MDBMError,match='open-document limit'):
        w.restore_recovery_bundle(record)
    assert w.session_data()==before and w.doc is original
    assert w._state_by_path(a).doc.text=='disk-a'


def test_non_owner_restoration_does_not_consume_owner_snapshot(tmp_path):
    a=tmp_path/'a.md'; a.write_text('disk-a',encoding='utf-8')
    owner=m.Workspace(config(tmp_path),a,restore=False)
    owner.doc.insert('live owner '); owner.persist_recovery()
    original=(tmp_path/'.mdbm'/'recovery.json').read_bytes()
    other=m.Workspace(config(tmp_path),a)
    try:
        assert not other.store.owns_lock
        choose(other,'Restore')
        assert other.doc.text=='live owner disk-a'
        assert (tmp_path/'.mdbm'/'recovery.json').read_bytes()==original
    finally:
        other.store.close(); owner.store.close()


def test_legacy_recovery_survives_defer_and_later_restore(tmp_path):
    a=tmp_path/'legacy.md'; a.write_text('disk',encoding='utf-8')
    w=m.Workspace(config(tmp_path),a,restore=False)
    w.doc.insert('legacy unsaved ')
    w.store.recovery(w.doc,w.files.root); w.store.close()
    deferred=m.Workspace(config(tmp_path),a)
    choose(deferred,'Later'); deferred._exit(); deferred.store.close()
    restored=m.Workspace(config(tmp_path),a)
    try:
        choose(restored,'Restore')
        assert restored.doc.text=='legacy unsaved disk'
        assert restored.doc.dirty and a.read_text()=='disk'
    finally:
        restored.store.close()


def test_unchanged_dirty_snapshot_is_not_rewritten(tmp_path,monkeypatch):
    a=tmp_path/'a.md'; a.write_text('disk',encoding='utf-8')
    w=m.Workspace(config(tmp_path),a,restore=False)
    w.doc.insert('unsaved ')
    publications=[]
    write=w.store.write
    def record_write(name,value):
        publications.append(copy.deepcopy(value))
        write(name,value)
    monkeypatch.setattr(w.store,'write',record_write)
    timestamps=iter(range(100,110))
    monkeypatch.setattr(m.time,'time',lambda:next(timestamps))
    try:
        w.persist_recovery()
        first_bytes=(tmp_path/'.mdbm'/'recovery.json').read_bytes()
        assert len(publications)==1 and publications[-1]['timestamp']==100

        w.persist_recovery()
        assert len(publications)==1
        assert (tmp_path/'.mdbm'/'recovery.json').read_bytes()==first_bytes

        w.doc.move_to(1); w.persist_recovery()
        assert len(publications)==2 and publications[-1]['timestamp']==102
        assert publications[-1]['documents'][0]['cursor']==1

        w.memories[m.Tile.EDIT].scroll=2; w.persist_recovery()
        assert len(publications)==3
        assert publications[-1]['documents'][0]['memories']['edit']['scroll']==2

        w.doc.insert('changed '); w.persist_recovery()
        assert len(publications)==4
        assert publications[-1]['documents'][0]['text']==w.doc.text
        w.persist_recovery()
        assert len(publications)==4

        w.doc.mark_saved(); w.persist_recovery()
        assert not (tmp_path/'.mdbm'/'recovery.json').exists()
    finally:
        w.store.close()
