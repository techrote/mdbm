"""Cross-process recovery ownership and partial-archive publication regression."""
import hashlib
import os
import stat
import subprocess
import sys

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


def test_primary_cannot_preempt_live_secondary_slot_before_first_snapshot(store,tmp_path):
    follower=m.StateStore(store.directory)
    assert follower.enabled and not follower.owns_lock
    try:
        name=follower.recovery_name
        assert not (store.directory/name).exists()
        with pytest.raises(m.MDBMError,match='another instance has reserved'):
            store.write(name,bundle(tmp_path,['primary preemption']))
        assert not (store.directory/name).exists()
        # The follower's first and later writes must remain available.
        follower.write(name,bundle(tmp_path,['follower first']))
        follower.write(name,bundle(tmp_path,['follower updated']))
        assert follower.read_recovery(name,4096)['documents'][0]['text']=='follower updated'
    finally:
        follower.close()


def test_dead_secondary_lease_does_not_block_stale_slot_reuse(store,tmp_path):
    # PID -1 is invalid/unknown and deliberately considered live. Reap a
    # real child instead, exercising the native dead-owner probe on both
    # POSIX and Windows without weakening conservative ownership checks.
    dead=subprocess.Popen([sys.executable,'-c','pass'],
                          stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
    assert dead.wait(timeout=10)==0
    assert not m._process_alive(dead.pid)
    token='f'*32
    name=f'recovery-{token}.json'
    store._write_json(f'lease-{token}.json',
                      {'pid':dead.pid,'token':token,'primary':False})
    store.write(name,bundle(tmp_path,['reclaimed archive']))
    assert store.read_recovery(name,4096)['documents'][0]['text']=='reclaimed archive'


@pytest.mark.parametrize('unknown_pid',[None,False,-1,0,'not-a-pid',2**31])
def test_unknown_secondary_lease_pid_keeps_reserved_slot(store,tmp_path,unknown_pid):
    # An inaccessible/unverifiable owner is never proof of a dead owner.
    token='e'*32
    name=f'recovery-{token}.json'
    store._write_json(f'lease-{token}.json',
                      {'pid':unknown_pid,'token':token,'primary':False})
    with pytest.raises(m.MDBMError,match='reserved this recovery slot'):
        store.write(name,bundle(tmp_path,['must not preempt unknown owner']))
    assert not (store.directory/name).exists()


def test_unreadable_foreign_lease_keeps_valid_snapshot_protected(store,tmp_path):
    follower=m.StateStore(store.directory)
    assert follower.enabled and not follower.owns_lock
    try:
        name=follower.recovery_name
        follower.write(name,bundle(tmp_path,['follower unsaved']))
        before=(store.directory/name).read_bytes()
        lease=store.directory/f'lease-{follower.token}.json'
        lease.write_bytes(b'{corrupted live lease')
        # The missing/invalid read result is not proof that the process died.
        assert store.read(lease.name,4096) is None
        assert store.snapshot_live(store.recovery_metadata(name,4096))
        with pytest.raises(m.MDBMError,match='another instance'):
            store.write(name,bundle(tmp_path,['primary overwrite']))
        with pytest.raises(m.MDBMError,match='another instance'):
            store.discard_recovery(name)
        assert (store.directory/name).read_bytes()==before
    finally:
        follower.close()


def test_unreadable_foreign_lease_keeps_corrupt_archive_until_owner_closes(store,tmp_path):
    follower=m.StateStore(store.directory)
    assert follower.enabled and not follower.owns_lock
    name=follower.recovery_name
    archive=store.directory/name
    lease=store.directory/f'lease-{follower.token}.json'
    try:
        original=b'{damaged recovery metadata'
        archive.write_bytes(original)
        lease.write_bytes(b'{damaged lease metadata')
        with pytest.raises(m.MDBMError,match='instance is active'):
            store.discard_recovery(name,allow_invalid=True)
        assert archive.read_bytes()==original
    finally:
        follower.close()
    # Once the follower actually releases/removes its lease, the primary may
    # explicitly discard the unreadable archive; never delete it automatically.
    store.discard_recovery(name,allow_invalid=True)
    assert not archive.exists()


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


def test_secondary_cannot_clobber_primary_session_or_owner_metadata(store):
    store.write('session.json',{'schema':'mdbm-session/2.0','documents':[],'marker':'primary'})
    follower=m.StateStore(store.directory)
    assert follower.enabled and not follower.owns_lock
    try:
        session=(store.directory/'session.json').read_bytes()
        with pytest.raises(m.MDBMError,match='Another instance owns session'):
            follower.write('session.json',{'schema':'mdbm-session/2.0','marker':'follower'})
        assert (store.directory/'session.json').read_bytes()==session
        for name in ('lock.json',f'lease-{store.token}.json',f'lease-{follower.token}.json'):
            original=(store.directory/name).read_bytes()
            for actor in (store,follower):
                with pytest.raises(m.MDBMError,match='Ownership metadata is internal'):
                    actor.write(name,{'pid':-1,'token':'invalid'})
                assert (store.directory/name).read_bytes()==original
        assert store.read('lock.json')['token']==store.token
    finally:
        follower.close()


def test_primary_cannot_spoof_follower_lease_to_replace_live_recovery(store,tmp_path):
    follower=m.StateStore(store.directory)
    assert follower.enabled and not follower.owns_lock
    try:
        name=follower.recovery_name
        follower.write(name,bundle(tmp_path,['follower unsaved']))
        original=(store.directory/name).read_bytes()
        lease=f'lease-{follower.token}.json'
        with pytest.raises(m.MDBMError,match='Ownership metadata is internal'):
            store.write(lease,{'pid':-1,'token':follower.token,'primary':False})
        assert store.snapshot_live(store.recovery_metadata(name,4096))
        with pytest.raises(m.MDBMError,match='another instance'):
            store.write(name,bundle(tmp_path,['primary overwrite']))
        assert (store.directory/name).read_bytes()==original
    finally:
        follower.close()


def test_primary_session_publication_still_works(store):
    store.write('session.json',{'schema':'mdbm-session/2.0','documents':[],'marker':'first'})
    store.write('session.json',{'schema':'mdbm-session/2.0','documents':[],'marker':'second'})
    assert store.read('session.json')['marker']=='second'


def test_generic_json_write_cannot_corrupt_shared_recovery_content(store,tmp_path):
    text='unique unsaved recovery text'
    store.write(store.recovery_name,bundle(tmp_path,[text]))
    name='recovery-'+('e'*32)+'.json'
    store.write(name,bundle(tmp_path,[text]))
    blob=next(store.directory.glob('content-*.bin'))
    original=blob.read_bytes()
    follower=m.StateStore(store.directory)
    assert follower.enabled and not follower.owns_lock
    try:
        for actor in (store,follower):
            with pytest.raises(m.MDBMError,match='Recovery content requires guarded publication'):
                actor.write(blob.name,{'invalid':'replacement of shared text'})
            assert blob.read_bytes()==original
        for snapshot in (store.recovery_name,name):
            assert store.read_recovery(snapshot,4096)['documents'][0]['text']==text
    finally:
        follower.close()


def test_disabled_primary_cannot_access_or_modify_recovery_directly(store,tmp_path):
    name=store.recovery_name
    original_text='keep unsaved after persistence failure'
    store.write(name,bundle(tmp_path,[original_text]))
    manifest=(store.directory/name).read_bytes()
    blobs={p.name:p.read_bytes() for p in store.directory.glob('content-*.bin')}
    assert blobs
    # Model a persistence failure after the initial durable write. The normal
    # generic writer already returns without writing, but direct methods must
    # not reacquire recovery.guard or touch durable state.
    store.enabled=False
    try:
        operations=(
            lambda:store.read_recovery(name,4096),
            lambda:store.recovery_metadata(name,4096),
            lambda:store.write_recovery(name,bundle(tmp_path,['clobber old text'])),
            lambda:store.discard_recovery(name),
            lambda:store.discard_recovery(name,allow_invalid=True),
        )
        for operation in operations:
            with pytest.raises(m.MDBMError,match='Persistence unavailable'):
                operation()
        assert (store.directory/name).read_bytes()==manifest
        assert {p.name:p.read_bytes() for p in store.directory.glob('content-*.bin')}==blobs
    finally:
        store.enabled=True
    assert store.read_recovery(name,4096)['documents'][0]['text']==original_text


def test_disabled_secondary_cannot_publish_its_first_snapshot(store,tmp_path):
    follower=m.StateStore(store.directory)
    assert follower.enabled and not follower.owns_lock
    name=follower.recovery_name
    try:
        follower.enabled=False
        with pytest.raises(m.MDBMError,match='Persistence unavailable'):
            follower.write_recovery(name,bundle(tmp_path,['should not publish']))
        assert not (store.directory/name).exists()
        assert not list(store.directory.glob('content-*.bin'))
    finally:
        follower.enabled=True
        follower.close()


@pytest.mark.skipif(not hasattr(os,'mkfifo'),reason='POSIX FIFOs only')
def test_fifo_manifest_cannot_block_recovery_guard(store,tmp_path):
    """A special-file archive must fail closed, not stall all state publishers."""
    name='recovery-'+('7'*32)+'.json'
    fifo=store.directory/name
    os.mkfifo(fifo)
    with pytest.raises(m.MDBMError,match='not a regular file'):
        store.recovery_metadata(name,4096)
    with pytest.raises(m.MDBMError,match='unreadable'):
        store.write(name,bundle(tmp_path,['preserve the foreign path']))
    assert stat.S_ISFIFO(fifo.lstat().st_mode)
    # A failed inspect/write must release the shared transaction guard.
    with m.StateFileLock(store.directory/'recovery.guard'):
        pass


@pytest.mark.skipif(not hasattr(os,'mkfifo'),reason='POSIX FIFOs only')
def test_fifo_content_blob_cannot_block_restore_or_republication(store,tmp_path):
    """Reject unsafe content without replacing the intact manifest."""
    original='unsaved buffer still referenced by manifest'
    name=store.recovery_name
    store.write(name,bundle(tmp_path,[original]))
    manifest=(store.directory/name).read_bytes()
    blob=next(store.directory.glob('content-*.bin'))
    blob.unlink()
    os.mkfifo(blob)
    with pytest.raises(m.MDBMError,match='not a regular file'):
        store.read_recovery(name,4096)
    with pytest.raises(m.MDBMError,match='not a regular file'):
        store.write(name,bundle(tmp_path,[original]))
    assert (store.directory/name).read_bytes()==manifest
    assert stat.S_ISFIFO(blob.lstat().st_mode)
    with m.StateFileLock(store.directory/'recovery.guard'):
        pass


@pytest.mark.skipif(not hasattr(os,'mkfifo'),reason='POSIX FIFOs only')
def test_fifo_secondary_lease_cannot_stall_recovery_owner_checks(store,tmp_path):
    """An unreadable live-owner lease preserves the foreign snapshot without hanging."""
    follower=m.StateStore(store.directory)
    assert follower.enabled and not follower.owns_lock
    name=follower.recovery_name
    archive=store.directory/name
    lease=store.directory/f'lease-{follower.token}.json'
    try:
        follower.write(name,bundle(tmp_path,['unsaved follower buffer']))
        original=archive.read_bytes()
        lease.unlink()
        os.mkfifo(lease)
        assert store.read(lease.name,4096) is None
        assert store.snapshot_live(store.recovery_metadata(name,4096))
        with pytest.raises(m.MDBMError,match='reserved this recovery slot'):
            store.write(name,bundle(tmp_path,['foreign overwrite']))
        with pytest.raises(m.MDBMError,match='another instance'):
            store.discard_recovery(name)
        assert archive.read_bytes()==original
        assert stat.S_ISFIFO(lease.lstat().st_mode)
        with m.StateFileLock(store.directory/'recovery.guard'):
            pass
    finally:
        follower.close()


@pytest.mark.skipif(not hasattr(os,'mkfifo'),reason='POSIX FIFOs only')
def test_fifo_primary_owner_record_cannot_stall_state_startup(tmp_path):
    """A suspect lock.json must not hang initialization while owner.guard is held."""
    directory=tmp_path/'state'
    directory.mkdir()
    lock=directory/'lock.json'
    os.mkfifo(lock)
    store=m.StateStore(directory)
    try:
        assert store.enabled and not store.owns_lock
        assert store.read('lock.json',4096) is None
        assert stat.S_ISFIFO(lock.lstat().st_mode)
        assert (directory/f'lease-{store.token}.json').is_file()
    finally:
        store.close()
    assert stat.S_ISFIFO(lock.lstat().st_mode)
