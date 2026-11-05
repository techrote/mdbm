"""Disk jobs preserve source snapshots, ordering, ownership and guarded exits."""
import threading
import time

import pytest

import mdbm as m


def settle(w, timeout=10):
    worker=w.io_worker
    deadline=time.monotonic()+timeout
    while worker.busy():
        with worker.condition:
            assert worker.condition.wait_for(
                lambda:worker.results or (not worker.running and not worker.pending),
                max(0,deadline-time.monotonic())), 'disk worker did not complete'
        w._poll_io()
        assert time.monotonic()<deadline


@pytest.fixture
def disk_workspace(tmp_path):
    config=m.load_config(tmp_path/'mdbm.conf',raw={
        'schema':'mdbm-config/1.0',
        'application':{'root':str(tmp_path),'state_directory':str(tmp_path/'.mdbm')},
        'appearance':{'theme':'minimal','motion':'off'},
    })
    path=tmp_path/'one.md'; path.write_text('disk',encoding='utf-8')
    w=m.Workspace(config,path,restore=False)
    w.io_worker=m.SerialIOWorker()
    yield w
    w.io_worker.close()
    w.store.close()


def test_worker_has_bounded_ordered_queue_and_moves_replaced_snapshot_to_tail():
    worker=m.SerialIOWorker(capacity=3)
    started=threading.Event(); release=threading.Event(); writes=[]; results=[]
    def blocked():
        started.set(); assert release.wait(5); writes.append('first')
    complete=lambda value,error:results.append((value,error))
    try:
        assert worker.request('first',blocked,complete)
        assert started.wait(5)
        assert worker.request('snapshot',lambda:writes.append('obsolete'),complete)
        assert worker.request('transaction',lambda:writes.append('transaction'),complete)
        assert not worker.request('overflow',lambda:None,complete)
        assert worker.request('snapshot',lambda:writes.append('latest'),complete,replace_pending=True)
    finally:
        release.set(); worker.close()
    assert writes==['first','transaction','latest']
    while item:=worker.poll(): item[1](item[2],item[3])
    assert len(results)==3 and all(error is None for _,error in results)
    assert not worker.busy() and not worker.request('closed',lambda:None,complete)


def test_recovery_model_construction_and_hashing_stay_on_disk_worker(disk_workspace,monkeypatch):
    from test_recovery_storage import bundle
    w=disk_workspace
    record=bundle(w.files.root,['# Recovered\n'+'line\n'*2000])
    record['documents'][0]['path']=str(w.doc.path)
    name='recovery-'+('e'*32)+'.json'; w.store.write(name,record)
    metadata=w.store.recovery_metadata(name,w.files.max_bytes)
    index_threads=[]; hash_threads=[]
    original_index=m.DocumentModel._reindex; original_hash=m.DocumentModel._hash
    def reindex(doc):
        index_threads.append(threading.get_ident()); original_index(doc)
    def digest(doc):
        hash_threads.append(threading.get_ident())
        assert threading.get_ident()==w.io_worker.thread.ident, 'Recovery rehashed a full document on the UI thread'
        return original_hash(doc)
    monkeypatch.setattr(m.DocumentModel,'_reindex',reindex)
    monkeypatch.setattr(m.DocumentModel,'_hash',digest)
    w._restore_recovery_snapshot(name,metadata,None)
    settle(w)
    assert index_threads==[w.io_worker.thread.ident]
    assert hash_threads==[w.io_worker.thread.ident]
    assert w.doc.text==record['documents'][0]['text'] and w.doc.dirty
    assert w.doc is w._active_state().doc
    assert not (w.store.directory/name).exists()


def test_edits_during_recovery_preparation_are_not_overwritten(disk_workspace,monkeypatch):
    from test_recovery_storage import bundle
    w=disk_workspace; original=w._document_from_recovery
    record=bundle(w.files.root,['recovered'])
    record['documents'][0]['path']=str(w.doc.path)
    name='recovery-'+('f'*32)+'.json'; w.store.write(name,record)
    metadata=w.store.recovery_metadata(name,w.files.max_bytes)
    entered=threading.Event(); release=threading.Event()
    def blocked(*args):
        entered.set(); assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(w,'_document_from_recovery',blocked)
    try:
        w._restore_recovery_snapshot(name,metadata,None)
        assert entered.wait(5)
        current=w.doc; w.doc.insert('typed while recovery loads ')
    finally: release.set()
    settle(w)
    assert w.doc is current and w.doc.text=='typed while recovery loads disk'
    assert w.overlays[-1].title=='Recovery rejected'
    assert (w.store.directory/name).exists()


def test_save_does_not_block_editing_or_mark_newer_text_clean(disk_workspace,monkeypatch):
    w=disk_workspace; original=w.files.save
    entered=threading.Event(); release=threading.Event(); threads=[]; after=[]
    def blocked(*args):
        threads.append(threading.get_ident()); entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(w.files,'save',blocked)
    w.doc.insert('first '); snapshot=w.doc.text
    try:
        w.save(after=lambda:after.append('closed'))
        assert entered.wait(5)
        w.handle_text('newer '); w.frame(80,24)
        assert w.doc.text!=snapshot and w.doc.dirty
        assert not after and w.doc.path.read_text()=='disk'
        w.close_document(); assert w._pending_saves and len(w.documents)==1
    finally: release.set()
    settle(w)
    assert threads==[w.io_worker.thread.ident]
    assert w.doc.path.read_text()==snapshot and w.doc.dirty and not after
    assert 'newer edits remain unsaved' in w.notice
    assert w.doc.fingerprint==w.files.fingerprint(w.doc.path)
    w.save(); settle(w)
    assert w.doc.path.read_text()==w.doc.text and not w.doc.dirty


def test_save_completion_keeps_its_document_when_user_switches(disk_workspace,monkeypatch):
    w=disk_workspace; state=w._active_state(); original=w.files.save
    entered=threading.Event(); release=threading.Event()
    def blocked(*args):
        entered.set(); assert release.wait(5); return original(*args)
    monkeypatch.setattr(w.files,'save',blocked)
    state.doc.insert('saved ')
    try:
        w.save(); assert entered.wait(5)
        w.new_document(); current=w.doc; w.handle_text('other unsaved')
    finally: release.set()
    settle(w)
    assert w.doc is current and current.text=='other unsaved' and current.dirty
    assert not state.doc.dirty and state.doc.path.read_text()==state.doc.text


def test_pending_destination_is_reserved_for_save_as(disk_workspace,monkeypatch):
    w=disk_workspace; target=w.files.root/'new.md'; original=w.files.save
    entered=threading.Event(); release=threading.Event()
    def blocked(*args):
        entered.set(); assert release.wait(5); return original(*args)
    monkeypatch.setattr(w.files,'save',blocked)
    try:
        w.perform_save(target,None); assert entered.wait(5)
        assert w._save_in_progress(w.doc.path)  # Save As also reserves its original source.
        w.new_document()
        with pytest.raises(m.MDBMError,match='pending file operation'):
            w.perform_save(target,None)
        with pytest.raises(m.MDBMError,match='still being saved'):
            w.open_path(target)
    finally: release.set()
    settle(w)
    assert target.read_text()=='disk' and w.doc.path is None


def test_async_conflict_requires_explicit_current_fingerprint(disk_workspace):
    w=disk_workspace; path=w.doc.path
    w.doc.insert('ours '); path.write_text('external',encoding='utf-8')
    w.save(); settle(w)
    assert path.read_text()=='external' and w.doc.dirty
    assert w.overlays[-1].title=='File conflict'
    # An intervening edit on disk after confirmation is displayed conflicts
    # again; accepting a stale confirmation never weakens SafeFiles guards.
    path.write_text('external again',encoding='utf-8')
    w.overlay_action({'row':0}); settle(w)
    assert path.read_text()=='external again' and w.overlays[-1].title=='File conflict'
    w.overlay_action({'row':0}); settle(w)
    assert path.read_text()==w.doc.text and not w.doc.dirty


def test_recovery_coalesces_and_shutdown_clear_is_last(disk_workspace,monkeypatch):
    w=disk_workspace; original=w.store.write; original_clear=w.store.clear_recovery
    entered=threading.Event(); release=threading.Event(); writes=[]
    def blocked(name,record):
        if name==w.store.recovery_name:
            text=record['documents'][0]['text']; writes.append(text)
            if not entered.is_set():
                entered.set(); assert release.wait(5)
        else: writes.append(name)
        return original(name,record)
    def clear():
        writes.append('clear'); original_clear()
    monkeypatch.setattr(w.store,'write',blocked)
    monkeypatch.setattr(w.store,'clear_recovery',clear)
    try:
        w.doc.insert('first '); first=w.doc.text
        w.persist_recovery(); assert entered.wait(5)
        w.doc.insert('obsolete '); obsolete=w.doc.text; w.persist_recovery()
        w.doc.insert('last '); last=w.doc.text; w.persist_recovery()
        w._exit()
        assert not w.exit_requested and w._closing
        w.handle_text('forbidden'); assert w.doc.text==last
    finally: release.set()
    settle(w)
    assert w.clean_exit and w.exit_requested
    assert first in writes and last in writes and obsolete not in writes
    assert writes[-1]=='clear' and w.store.read(w.store.recovery_name) is None
    assert w.store.owns_lock  # Release follows the drained shutdown, in run().


def test_failed_background_recovery_is_retryable_and_keeps_previous(disk_workspace,monkeypatch):
    w=disk_workspace; w.doc.insert('old '); w.persist_recovery(); settle(w)
    old=w.store.read(w.store.recovery_name); original=w.store.write
    def failed(*args): raise m.MDBMError('disk full')
    monkeypatch.setattr(w.store,'write',failed)
    w.doc.insert('new '); w.persist_recovery(); settle(w)
    assert w.notice_error and 'disk full' in w.notice
    assert w.store.read(w.store.recovery_name)==old
    monkeypatch.setattr(w.store,'write',original)
    w.persist_recovery(); settle(w)
    assert w.store.read(w.store.recovery_name)!=old


def test_recovery_signature_does_not_json_encode_source_on_event_thread(disk_workspace,monkeypatch):
    w=disk_workspace; w.doc.insert('unique source sentinel ')
    original=m.json.dumps; controller=threading.get_ident(); inspected=[]
    def encode(value,*args,**kwargs):
        if threading.get_ident()==controller and isinstance(value,dict) and 'documents' in value:
            inspected.append(value)
            assert all('text' not in item for item in value['documents'])
        return original(value,*args,**kwargs)
    monkeypatch.setattr(m.json,'dumps',encode)
    w.persist_recovery(); settle(w)
    assert inspected and w.store.read(w.store.recovery_name) is not None


def test_open_constructs_document_off_thread_and_drops_old_root_result(disk_workspace,monkeypatch):
    w=disk_workspace; path=w.files.root/'other.md'; path.write_text('# Heading\n',encoding='utf-8')
    newroot=w.files.root/'nested'; newroot.mkdir()
    original=w.files.load; entered=threading.Event(); release=threading.Event(); threads=[]
    def blocked(*args):
        threads.append(threading.get_ident()); entered.set()
        assert release.wait(5); return original(*args)
    monkeypatch.setattr(w.files,'load',blocked)
    try:
        w.open_path(path); assert entered.wait(5)
        assert len(w.documents)==1
        w.change_root(newroot)
    finally: release.set()
    settle(w)
    assert threads==[w.io_worker.thread.ident]
    assert w.files.root==newroot and len(w.documents)==1 and w.doc.path is None
    assert not w._pending_opens


def test_late_open_does_not_steal_a_newer_document_or_edit(disk_workspace,monkeypatch):
    w=disk_workspace; path=w.files.root/'other.md'; path.write_text('opened',encoding='utf-8')
    original=w.files.load; entered=threading.Event(); release=threading.Event()
    def blocked(*args):
        entered.set(); assert release.wait(5); return original(*args)
    monkeypatch.setattr(w.files,'load',blocked)
    try:
        w.open_path(path); assert entered.wait(5)
        w.new_document(); current=w.doc; w.handle_text('keep writing')
    finally: release.set()
    settle(w)
    assert w.doc is current and w.doc.text=='keep writing'
    assert w._state_by_path(path).doc.text=='opened'
    assert 'Opened in background' in w.notice


@pytest.mark.parametrize('later_intent',['none','reselect','appearance'])
def test_repeated_open_keeps_intent_but_later_user_intent_cancels_activation(disk_workspace,monkeypatch,later_intent):
    w=disk_workspace; path=w.files.root/'other.md'; path.write_text('opened',encoding='utf-8')
    original=w.files.load; entered=threading.Event(); release=threading.Event(); current=w.doc
    def blocked(*args):
        entered.set(); assert release.wait(5); return original(*args)
    monkeypatch.setattr(w.files,'load',blocked)
    try:
        w.open_path(path); assert entered.wait(5)
        w.open_path(path)
        if later_intent=='reselect': w.activate_document(w.active_document_id)
        elif later_intent=='appearance': w.choose_appearance()
    finally: release.set()
    settle(w)
    assert len(w.documents)==2
    if later_intent!='none': assert w.doc is current
    else: assert w.doc.path==path
    if later_intent=='appearance': assert w.overlays[-1].kind=='settings'
