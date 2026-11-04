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


@pytest.mark.skipif(os.name!='posix',reason='POSIX symlink semantics')
@pytest.mark.parametrize('archive_state',['reserved','valid','invalid'])
def test_dangling_symlink_lease_is_still_ownership_evidence(store,tmp_path,archive_state):
    # A tampered lease may remain as a symlink whose target does not exist.
    # Path.exists() follows it and reports False; the directory entry still
    # exists and must not be mistaken for a released follower slot.
    follower=m.StateStore(store.directory)
    assert follower.enabled and not follower.owns_lock
    name=follower.recovery_name
    archive=store.directory/name
    lease=store.directory/f'lease-{follower.token}.json'
    try:
        if archive_state=='valid':
            follower.write(name,bundle(tmp_path,['follower unsaved']))
        elif archive_state=='invalid':
            archive.write_bytes(b'{corrupted follower archive')
        before=archive.read_bytes() if archive.exists() else None
        lease.unlink()
        lease.symlink_to('missing-lease-target.json')
        assert lease.is_symlink() and not lease.exists()
        assert store.read(lease.name,4096) is None
        assert store.snapshot_live({'owner':{'token':follower.token}})
        with pytest.raises(m.MDBMError,match='reserved this recovery slot'):
            store.write(name,bundle(tmp_path,['must not claim follower slot']))
        if archive_state=='valid':
            with pytest.raises(m.MDBMError,match='another instance'):
                store.discard_recovery(name)
        elif archive_state=='invalid':
            with pytest.raises(m.MDBMError,match='instance is active'):
                store.discard_recovery(name,allow_invalid=True)
        assert (archive.read_bytes() if archive.exists() else None)==before
        with m.StateFileLock(store.directory/'recovery.guard'):
            pass
    finally:
        follower.close()


def test_inaccessible_lease_metadata_remains_conservatively_live(store,tmp_path,monkeypatch):
    # A stat/permission failure is unknown ownership, not absence.
    token='d'*32
    name=f'recovery-{token}.json'
    lease=store.directory/f'lease-{token}.json'
    actual_lstat=m.Path.lstat
    def inaccessible(path,*args,**kwargs):
        if path==lease:
            raise PermissionError('injected inaccessible lease directory entry')
        return actual_lstat(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(m.Path,'lstat',inaccessible)
        assert store.snapshot_live({'owner':{'token':token}})
        with pytest.raises(m.MDBMError,match='reserved this recovery slot'):
            store.write(name,bundle(tmp_path,['do not seize uncertain lease']))
    assert not (store.directory/name).exists()


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

@pytest.mark.parametrize('owner_mode',['missing','inconsistent'])
@pytest.mark.parametrize('partial',[False,True])
def test_live_follower_slot_cannot_be_discarded_with_damaged_but_parseable_owner(store,tmp_path,owner_mode,partial):
    """The filename lease is authoritative even when the manifest is parseable."""
    follower=m.StateStore(store.directory)
    assert follower.enabled and not follower.owns_lock
    name=follower.recovery_name
    archive=store.directory/name
    try:
        follower.write(name,bundle(tmp_path,['live follower unsaved text']))
        record=store.recovery_metadata(name,4096)
        assert record['owner']['token']==follower.token
        if owner_mode=='missing':
            record.pop('owner')
        else:
            record['owner']={'pid':os.getpid(),'token':'0'*32}
        # Model an archive whose owner metadata was damaged by an external
        # writer without invalidating its schema or content-addressed text.
        store._write_json(name,record)
        assert store.read_recovery(name,4096)['documents'][0]['text']=='live follower unsaved text'
        original=archive.read_bytes()
        assert not store.can_modify_recovery(name,record)
        remaining=bundle(tmp_path,['unwanted partial replacement']) if partial else None
        with pytest.raises(m.MDBMError,match='another instance owns'):
            store.discard_recovery(name,remaining=remaining)
        assert archive.read_bytes()==original
    finally:
        follower.close()
    # Once the live lease really disappears, the primary may explicitly
    # remove the archival slot; no automatic reclamation was performed.
    assert store.can_modify_recovery(name,record)
    store.discard_recovery(name)
    assert not archive.exists()


@pytest.mark.skipif(os.name!='posix' or not hasattr(os,'mkfifo'), reason='POSIX special-file guards only')
@pytest.mark.parametrize('name',['owner.guard','recovery.guard'])
def test_special_file_guards_use_nonblocking_open_and_fail_closed(tmp_path,monkeypatch,name):
    """An injected non-regular guard must not stall before fstat validates it."""
    directory=tmp_path/'state'
    directory.mkdir()
    path=directory/name
    os.mkfifo(path)
    original_open=os.open
    opened=[]

    def observe_open(candidate,flags,*args,**kwargs):
        if os.fspath(candidate)==os.fspath(path):
            opened.append(flags)
        return original_open(candidate,flags,*args,**kwargs)

    monkeypatch.setattr(os,'open',observe_open)
    with pytest.raises(m.MDBMError,match='Invalid state guard'):
        m.StateFileLock(path,blocking=True)
    assert len(opened)==1
    assert opened[0] & os.O_NONBLOCK
    assert stat.S_ISFIFO(path.lstat().st_mode)


@pytest.mark.parametrize('kind',['directory','fifo'])
def test_nonregular_orphan_content_name_does_not_block_publication(store,tmp_path,kind):
    """A content-shaped special file is neither GC-owned nor a reason to lose saves."""
    if kind=='fifo' and not hasattr(os,'mkfifo'):
        pytest.skip('POSIX FIFO support required')
    name=store.recovery_name
    store.write(name,bundle(tmp_path,['first unsaved version']))
    old_manifest=(store.directory/name).read_bytes()
    obstacle=store.directory/('content-'+('e'*64)+'.bin')
    if kind=='directory':
        obstacle.mkdir()
        (obstacle/'sentinel').write_bytes(b'keep non-blob directory intact')
    else:
        os.mkfifo(obstacle)
    orphan=store.directory/('content-'+('f'*64)+'.bin')
    orphan.write_bytes(b'unreferenced regular content')
    # Collection runs on both sides of manifest publication. A non-regular
    # lookalike must not interrupt either pass, and a genuine orphan is free.
    store.write(name,bundle(tmp_path,['second unsaved version']))
    assert (store.directory/name).read_bytes()!=old_manifest
    assert store.read_recovery(name,4096)['documents'][0]['text']=='second unsaved version'
    assert not orphan.exists()
    assert obstacle.lstat()
    if kind=='directory':
        assert (obstacle/'sentinel').read_bytes()==b'keep non-blob directory intact'
    else:
        assert stat.S_ISFIFO(obstacle.lstat().st_mode)
    # Repeated explicit collection must also preserve the foreign path.
    assert store.collect_recovery_garbage()==0
    assert obstacle.lstat()


def test_undeletable_orphan_is_charged_but_does_not_block_recovery(store,tmp_path,monkeypatch):
    """A sharing violation on optional GC cannot suppress a durable snapshot."""
    name=store.recovery_name
    store.write(name,bundle(tmp_path,['first dirty buffer']))
    orphan=store.directory/('content-'+('e'*64)+'.bin')
    orphan.write_bytes(b'undeletable orphan content')
    original_unlink=type(orphan).unlink

    def denied_unlink(path,*args,**kwargs):
        if path==orphan: raise PermissionError('simulated sharing violation')
        return original_unlink(path,*args,**kwargs)

    monkeypatch.setattr(type(orphan),'unlink',denied_unlink)
    store.write(name,bundle(tmp_path,['second dirty buffer']))
    assert store.read_recovery(name,4096)['documents'][0]['text']=='second dirty buffer'
    assert orphan.read_bytes()==b'undeletable orphan content'
    assert sum('orphan cleanup deferred' in message for message in store.diagnostics)==1
    assert store.collect_recovery_garbage()==0
    assert sum('orphan cleanup deferred' in message for message in store.diagnostics)==1
    before=(store.directory/name).read_bytes()
    # The orphan is still included in the accounting even though unlink failed.
    store.storage_limit=store.recovery_inventory()['bytes']+1
    with pytest.raises(m.MDBMError,match='storage limit reached'):
        store.write(name,bundle(tmp_path,['new dirty buffer'+'x'*500]))
    assert (store.directory/name).read_bytes()==before
    assert store.read_recovery(name,4096)['documents'][0]['text']=='second dirty buffer'


def test_unstatable_orphan_still_fails_closed_before_publication(store,tmp_path,monkeypatch):
    """Do not undercount inaccessible files while enforcing the hard quota."""
    name=store.recovery_name
    store.write(name,bundle(tmp_path,['retain unsaved text']))
    previous=(store.directory/name).read_bytes()
    orphan=store.directory/('content-'+('e'*64)+'.bin')
    orphan.write_bytes(b'contents cannot be accounted')
    original_lstat=type(orphan).lstat

    def denied_lstat(path,*args,**kwargs):
        if path==orphan: raise PermissionError('simulated inaccessible metadata')
        return original_lstat(path,*args,**kwargs)

    monkeypatch.setattr(type(orphan),'lstat',denied_lstat)
    with pytest.raises(PermissionError,match='inaccessible metadata'):
        store.write(name,bundle(tmp_path,['attempted overwrite']))
    assert (store.directory/name).read_bytes()==previous
    assert orphan.is_file()

@pytest.mark.parametrize('inaccessible_kind',['blob','archive'])
def test_quota_never_undercounts_unstatable_entries_when_gc_is_suspended(
        store,tmp_path,monkeypatch,inaccessible_kind):
    """An invalid manifest disables GC, not strict publication accounting."""
    name=store.recovery_name
    store.write(name,bundle(tmp_path,['original unsaved work']))
    previous=(store.directory/name).read_bytes()
    corrupt=store.directory/('recovery-'+('a'*32)+'.json')
    corrupt.write_bytes(b'not a JSON recovery manifest')
    if inaccessible_kind=='blob':
        obstacle=store.directory/('content-'+('e'*64)+'.bin')
    else:
        obstacle=store.directory/('recovery-'+('b'*32)+'.json')
    obstacle.write_bytes(b'inaccessible recovery pool bytes')
    original_lstat=type(obstacle).lstat

    def denied_lstat(path,*args,**kwargs):
        if path==obstacle: raise PermissionError('simulated inaccessible metadata')
        return original_lstat(path,*args,**kwargs)

    monkeypatch.setattr(type(obstacle),'lstat',denied_lstat)
    new_text='new work must not replace the previous manifest'
    new_digest=hashlib.sha256(new_text.encode()).hexdigest()
    with pytest.raises(m.MDBMError,match='Recovery storage usage cannot be verified'):
        store.write(name,bundle(tmp_path,[new_text]))
    assert (store.directory/name).read_bytes()==previous
    assert corrupt.read_bytes()==b'not a JSON recovery manifest'
    assert obstacle.read_bytes()==b'inaccessible recovery pool bytes'
    assert not (store.directory/('content-'+new_digest+'.bin')).exists()
    assert store.read_recovery(name,4096)['documents'][0]['text']=='original unsaved work'


@pytest.mark.parametrize('bad_first',[False,True])
@pytest.mark.parametrize('operation',['write','partial_discard'])
def test_conflicting_v3_shared_content_lengths_preserve_snapshot(
        store,tmp_path,bad_first,operation):
    """A duplicated digest cannot describe two different byte streams."""
    name=store.recovery_name
    store.write(name,bundle(tmp_path,['original unsaved work']))
    previous=(store.directory/name).read_bytes()
    candidate=store.read(name)  # Deliberately exercise the v3 input path.
    original=candidate['documents'][0]
    good=dict(original,document_id='a'*32)
    bad=dict(original,document_id='b'*32,bytes=original['bytes']+1)
    candidate['documents']=[bad,good] if bad_first else [good,bad]
    with pytest.raises(m.MDBMError,match='Inconsistent recovery content sizes'):
        if operation=='write':
            store.write(name,candidate)
        else:
            store.discard_recovery(name,remaining=candidate)
    assert (store.directory/name).read_bytes()==previous
    assert [doc['text'] for doc in store.read_recovery(name,4096)['documents']]==[
        'original unsaved work']


def test_v3_shared_content_with_consistent_lengths_still_publishes(store,tmp_path):
    """Multiple documents may legitimately share one content-addressed blob."""
    name=store.recovery_name
    store.write(name,bundle(tmp_path,['same text']))
    candidate=store.read(name)
    candidate['documents'].append(dict(candidate['documents'][0],document_id='b'*32))
    store.write(name,candidate)
    assert [doc['text'] for doc in store.read_recovery(name,4096)['documents']]==[
        'same text','same text']
    assert len(list(store.directory.glob('content-*.bin')))==1

@pytest.mark.parametrize('partial',[False,True])
def test_stale_inspected_archive_cannot_delete_or_rewrite_newer_work(store,tmp_path,partial):
    """An inspected selection cannot silently consume a successor's unsaved text."""
    name='recovery-'+('d'*32)+'.json'
    store.write(name,bundle(tmp_path,['older first','older sibling']))
    inspected=store.recovery_metadata(name,4096)
    remaining=dict(inspected,documents=inspected['documents'][1:]) if partial else None

    # Simulate a cooperating writer publishing a different revision after the
    # recovery confirmation was displayed, but before the queued discard runs.
    store.write(name,bundle(tmp_path,['newer first','newer sibling']))
    newer=(store.directory/name).read_bytes()
    with pytest.raises(m.MDBMError,match='changed since inspection'):
        store.discard_recovery(name,remaining,expected_metadata=inspected)
    assert (store.directory/name).read_bytes()==newer
    assert [doc['text'] for doc in store.read_recovery(name,4096)['documents']]==[
        'newer first','newer sibling']


def test_matching_inspected_archive_still_allows_guarded_partial_discard(store,tmp_path):
    name='recovery-'+('d'*32)+'.json'
    store.write(name,bundle(tmp_path,['chosen','saved sibling']))
    inspected=store.recovery_metadata(name,4096)
    remaining=dict(inspected,documents=inspected['documents'][1:])
    store.discard_recovery(name,remaining,expected_metadata=inspected)
    assert [doc['text'] for doc in store.read_recovery(name,4096)['documents']]==[
        'saved sibling']


def test_unreadable_discard_refuses_archive_repaired_since_inspection(store,tmp_path):
    """The explicit 'unreadable archive' action must not delete a new good save."""
    name='recovery-'+('d'*32)+'.json'
    archive=store.directory/name
    archive.write_bytes(b'{broken unreadable archive')
    with pytest.raises((m.MDBMError,ValueError)):
        store.recovery_metadata(name,4096)
    store.write(store.recovery_name,bundle(tmp_path,['newly recovered document']))
    valid=(store.directory/store.recovery_name).read_bytes()
    # Another writer repaired this slot between inspection and confirmation.
    with m.StateFileLock(store.directory/'recovery.guard',True):
        store._write_bytes(name,valid)
    with pytest.raises(m.MDBMError,match='changed since inspection'):
        store.discard_recovery(name,allow_invalid=True)
    assert archive.read_bytes()==valid
    assert store.read_recovery(name,4096)['documents'][0]['text']=='newly recovered document'


@pytest.mark.parametrize('format_name',['v3','legacy_v2','legacy_v1'])
def test_restore_read_rejects_archive_replaced_after_inspection(store,tmp_path,format_name):
    """A queued Restore must not hydrate the successor of an inspected archive."""
    name='recovery-'+('c'*32)+'.json'

    def publish(text):
        record=bundle(tmp_path,[text])
        if format_name=='v3':
            store.write(name,record)
        else:
            if format_name=='legacy_v1':
                record={**record['documents'][0],'schema':'mdbm-recovery/1.0',
                        'root':str(tmp_path),'timestamp':record['timestamp']}
            with m.StateFileLock(store.directory/'recovery.guard',True):
                store._write_json(name,record)

    publish('original unsaved text')
    inspected=store.recovery_metadata(name,4096)
    assert store.read_recovery(name,4096,expected_metadata=inspected)
    publish('newer unsaved text')
    newer=(store.directory/name).read_bytes()
    with pytest.raises(m.MDBMError,match='changed since inspection'):
        store.read_recovery(name,4096,expected_metadata=inspected)
    assert (store.directory/name).read_bytes()==newer
    restored=store.read_recovery(name,4096)
    assert (restored['text'] if format_name=='legacy_v1' else
            restored['documents'][0]['text'])=='newer unsaved text'


@pytest.mark.parametrize('format_name',['v3','legacy_v2','legacy_v1'])
def test_restore_read_accepts_matching_archive_revision(store,tmp_path,format_name):
    name='recovery-'+('c'*32)+'.json'
    original=bundle(tmp_path,['retained unsaved text'])
    if format_name=='legacy_v1':
        original={**original['documents'][0],'schema':'mdbm-recovery/1.0',
                  'root':str(tmp_path),'timestamp':original['timestamp']}
    if format_name=='v3':
        store.write(name,original)
    else:
        with m.StateFileLock(store.directory/'recovery.guard',True):
            store._write_json(name,original)
    metadata=store.recovery_metadata(name,4096)
    before=(store.directory/name).read_bytes()
    restored=store.read_recovery(name,4096,expected_metadata=metadata)
    assert (restored['text'] if format_name=='legacy_v1' else
            restored['documents'][0]['text'])=='retained unsaved text'
    assert (store.directory/name).read_bytes()==before


@pytest.mark.parametrize('replacement',['same_inode','new_inode'])
def test_unreadable_selection_refuses_changed_unreadable_successor(store,tmp_path,replacement):
    """Even another malformed snapshot is not the archive the user confirmed."""
    name='recovery-'+('d'*32)+'.json'
    archive=store.directory/name
    archive.write_bytes(b'{broken version A')
    inspected,error,revision=store.inspect_recovery_revision(name,4096)
    assert inspected is None and error and revision is not None
    replacement_bytes=b'{broken version B'
    with m.StateFileLock(store.directory/'recovery.guard',True):
        if replacement=='same_inode':
            archive.write_bytes(replacement_bytes)
            after=archive.stat()
            os.utime(archive,ns=(after.st_atime_ns,after.st_mtime_ns+2_000_000_000))
        else:
            pending=store.directory/'pending-invalid-archive'
            pending.write_bytes(replacement_bytes)
            pending.replace(archive)
    assert store._recovery_revision(name)!=revision
    with pytest.raises(m.MDBMError,match='changed since inspection'):
        store.discard_recovery(name,allow_invalid=True,expected_unreadable=revision)
    assert archive.read_bytes()==replacement_bytes


def test_unchanged_unreadable_confirmation_can_still_discard(store):
    name='recovery-'+('d'*32)+'.json'
    archive=store.directory/name
    archive.write_bytes(b'{broken but still the confirmed archive')
    inspected,error,revision=store.inspect_recovery_revision(name,4096)
    assert inspected is None and error and revision is not None
    store.discard_recovery(name,allow_invalid=True,expected_unreadable=revision)
    assert not archive.exists()


@pytest.mark.skipif(not hasattr(os,'mkfifo'),reason='POSIX FIFO support required')
def test_nonregular_unreadable_archive_has_no_discard_identity(store):
    name='recovery-'+('d'*32)+'.json'
    archive=store.directory/name
    os.mkfifo(archive)
    inspected,error,revision=store.inspect_recovery_revision(name,4096)
    assert inspected is None and error and revision is None
    assert stat.S_ISFIFO(archive.lstat().st_mode)
