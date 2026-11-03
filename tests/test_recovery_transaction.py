"""Cross-process recovery ownership and partial-archive publication regression."""
import hashlib

import pytest

import mdbm as m

from test_recovery_storage import bundle


@pytest.fixture
def store(tmp_path):
    primary=m.StateStore(tmp_path/'state')
    assert primary.enabled and primary.owns_lock
    try:
        yield primary
    finally:
        primary.close()


def test_secondary_cannot_replace_live_primary_recovery(store,tmp_path):
    store.write(store.recovery_name,bundle(tmp_path,['primary unsaved']))
    original=(store.directory/store.recovery_name).read_bytes()
    secondary=m.StateStore(store.directory)
    assert secondary.enabled and not secondary.owns_lock
    try:
        with pytest.raises(m.MDBMError,match='another instance'):
            secondary.write(store.recovery_name,bundle(tmp_path,['secondary overwrite']))
        assert (store.directory/store.recovery_name).read_bytes()==original
        assert store.read_recovery(store.recovery_name,4096)['documents'][0]['text']=='primary unsaved'
    finally:
        secondary.close()


def test_secondary_can_update_its_own_snapshot(store,tmp_path):
    secondary=m.StateStore(store.directory)
    try:
        assert secondary.enabled and not secondary.owns_lock
        for value in ('first dirty','second dirty'):
            secondary.write(secondary.recovery_name,bundle(tmp_path,[value]))
        assert secondary.read_recovery(secondary.recovery_name,4096)['documents'][0]['text']=='second dirty'
    finally:
        secondary.close()


def test_unreadable_existing_archive_is_not_silently_overwritten(store,tmp_path):
    name='recovery-'+('a'*32)+'.json'
    corrupt=b'{invalid metadata - preserve for inspection'
    (store.directory/name).write_bytes(corrupt)
    with pytest.raises(m.MDBMError,match='unreadable'):
        store.write(name,bundle(tmp_path,['new']))
    assert (store.directory/name).read_bytes()==corrupt


def test_partial_discard_keeps_guard_held_during_manifest_publication(store,tmp_path,monkeypatch):
    name='recovery-'+('b'*32)+'.json'
    store.write(name,bundle(tmp_path,['retain me','discard me']))
    original=store.read_recovery(name,4096)
    actual_publish=store._publish_recovery_locked
    called=[]
    def checked_publish(*args,**kwargs):
        # A second open file description must not acquire the kernel guard
        # before the partial replacement commits (POSIX and Windows).
        with pytest.raises(OSError):
            with m.StateFileLock(store.directory/'recovery.guard'):
                pass
        called.append(True)
        return actual_publish(*args,**kwargs)
    monkeypatch.setattr(store,'_publish_recovery_locked',checked_publish)
    store.discard_recovery(name,dict(original,documents=original['documents'][:1]))
    assert called==[True]
    result=store.read_recovery(name,4096)
    assert [d['text'] for d in result['documents']]==['retain me']


def test_failed_partial_publication_preserves_manifest_and_source(store,tmp_path,monkeypatch):
    name='recovery-'+('c'*32)+'.json'
    store.write(name,bundle(tmp_path,['retained','to drop']))
    previous=(store.directory/name).read_bytes()
    record=store.read_recovery(name,4096)
    real_write=store._write_bytes
    def fail_manifest(file_name,encoded):
        if file_name==name: raise m.MDBMError('Injected manifest failure')
        return real_write(file_name,encoded)
    monkeypatch.setattr(store,'_write_bytes',fail_manifest)
    with pytest.raises(m.MDBMError,match='Injected manifest failure'):
        store.discard_recovery(name,dict(record,documents=record['documents'][:1]))
    assert (store.directory/name).read_bytes()==previous
    assert [d['text'] for d in store.read_recovery(name,4096)['documents']]==['retained','to drop']


@pytest.mark.parametrize('invalid',[
    None,
    [],
    {'schema':'mdbm-recovery/4.0','documents':[]},
    {'schema':'mdbm-session/2.0','documents':[]},
    {'documents':[]},
])
def test_unsupported_recovery_write_cannot_replace_existing_snapshot(store,tmp_path,invalid):
    # The generic state writer previously bypassed recovery.guard and owner
    # validation for any unrecognized schema (including non-mapping input).
    store.write(store.recovery_name,bundle(tmp_path,['keep unsaved']))
    original=(store.directory/store.recovery_name).read_bytes()
    with pytest.raises(m.MDBMError,match='Unsupported recovery format'):
        store.write(store.recovery_name,invalid)
    assert (store.directory/store.recovery_name).read_bytes()==original
    assert store.read_recovery(store.recovery_name,4096)['documents'][0]['text']=='keep unsaved'


def test_unsupported_generic_write_cannot_clobber_another_live_owner(store,tmp_path):
    store.write(store.recovery_name,bundle(tmp_path,['primary unsaved']))
    original=(store.directory/store.recovery_name).read_bytes()
    secondary=m.StateStore(store.directory)
    assert secondary.enabled and not secondary.owns_lock
    try:
        with pytest.raises(m.MDBMError,match='Unsupported recovery format'):
            secondary.write(store.recovery_name,{'schema':'unrecognized','documents':[]})
        assert (store.directory/store.recovery_name).read_bytes()==original
        assert store.read_recovery(store.recovery_name,4096)['documents'][0]['text']=='primary unsaved'
    finally:
        secondary.close()


def test_invalid_recovery_write_creates_no_slot_and_regular_state_still_writes(store):
    name='recovery-'+('d'*32)+'.json'
    with pytest.raises(m.MDBMError,match='Unsupported recovery format'):
        store.write(name,{'schema':'unknown'})
    assert not (store.directory/name).exists()
    store.write('session.json',{'schema':'mdbm-session/2.0','documents':[]})
    assert store.read('session.json')['schema']=='mdbm-session/2.0'
