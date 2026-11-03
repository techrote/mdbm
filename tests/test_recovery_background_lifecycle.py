"""Cross-check archive continuations against the ordered shutdown write."""
import collections
import hashlib

import pytest

import mdbm as m


class ControlledDiskQueue:
    """Keep real disk transactions, but deliver completions at explicit barriers."""
    def __init__(self):
        self.pending=collections.deque()
        self.results=collections.deque()

    def request(self,key,work,complete,replace_pending=False):
        self.pending.append((key,work,complete))
        return True

    def poll(self):
        return self.results.popleft() if self.results else None

    def run_next(self):
        key,work,complete=self.pending.popleft()
        try: value,error=work(),None
        except Exception as exc: value,error=None,exc
        self.results.append((key,complete,value,error))


def make_workspace(tmp_path):
    config=m.load_config(tmp_path/'mdbm.conf',raw={'schema':'mdbm-config/1.0',
        'application':{'root':str(tmp_path),'state_directory':str(tmp_path/'.mdbm')},
        'appearance':{'theme':'minimal','motion':'off'}})
    first=tmp_path/'first.md'; first.write_text('disk first',encoding='utf-8')
    second=tmp_path/'second.md'; second.write_text('disk second',encoding='utf-8')
    return m.Workspace(config,first,restore=False),first,second


def drain(w,queue):
    for _ in range(20):
        if not queue.pending and not queue.results: return
        if queue.pending: queue.run_next()
        w._poll_io()
    raise AssertionError('Recovery continuation failed to settle')


def archive_for_current_document(w):
    text='recovered source'
    item={'document_id':w.active_document_id,'path':str(w.doc.path),'text':text,
        'digest':hashlib.sha256(text.encode()).hexdigest(),'baseline':w.doc.digest,
        'fingerprint':None,'cursor':0,'anchor':None,'newline':'\n','bom':False}
    name='recovery-'+('b'*32)+'.json'
    w.store.write(name,{'schema':'mdbm-recovery/2.0','root':str(w.files.root),
        'documents':[item],'active_document':w.active_document_id,'timestamp':1})
    return name,w.store.recovery_metadata(name,w.files.max_bytes)


def test_sibling_preservation_completion_cannot_publish_after_shutdown_clear(tmp_path,monkeypatch):
    w,first,second=make_workspace(tmp_path)
    queue=ControlledDiskQueue(); w.io_worker=queue
    archive='recovery-'+('a'*32)+'.json'
    records=[]
    for index,path in enumerate((first,second),1):
        text='recovered '+path.name
        records.append({'document_id':f'{index:032x}','path':str(path),'text':text,
            'digest':hashlib.sha256(text.encode()).hexdigest(),
            'baseline':hashlib.sha256(path.read_bytes()).hexdigest(),
            'fingerprint':None,'cursor':0,'anchor':None,'newline':'\n','bom':False})
    record={'schema':'mdbm-recovery/2.0','root':str(tmp_path),
        'documents':records,'active_document':records[0]['document_id'],'timestamp':1}
    try:
        w.store.write(archive,record)
        metadata=w.store.recovery_metadata(archive,w.files.max_bytes)
        writes=[]; write=w.store.write; clear=w.store.clear_recovery
        def tracked_write(name,value):
            writes.append(name); return write(name,value)
        def tracked_clear():
            writes.append('clear'); return clear()
        monkeypatch.setattr(w.store,'write',tracked_write)
        monkeypatch.setattr(w.store,'clear_recovery',tracked_clear)
        w._restore_recovery_snapshot(archive,metadata,first)
        queue.run_next(); w._poll_io()  # Read is accepted; sibling publication waits.
        assert w.doc.text=='recovered first.md' and len(queue.pending)==1
        w._exit()  # User has completed the quit/discard guard.
        assert w._closing and len(queue.pending)==2
        drain(w,queue)
        assert w.clean_exit and w.exit_requested
        assert writes[-1]=='clear'
        assert not (w.store.directory/w.store.recovery_name).exists()
        assert (w.store.directory/archive).exists()
        assert first.read_text(encoding='utf-8')=='disk first'
        assert second.read_text(encoding='utf-8')=='disk second'
        assert not w.overlays and w.store.owns_lock
    finally:
        w.io_worker=None; w.store.close()


def test_pending_recovery_error_does_not_reopen_dialog_during_exit(tmp_path,monkeypatch):
    w,_,_=make_workspace(tmp_path)
    queue=ControlledDiskQueue(); w.io_worker=queue
    def fail_read(*args,**kwargs): raise m.MDBMError('Inventory read failed')
    monkeypatch.setattr(w.store,'recovery_inventory',fail_read)
    try:
        w.show_recovery_inventory()
        w.request_quit()
        assert w._closing and not w.overlays
        drain(w,queue)
        assert w.clean_exit and w.exit_requested and not w.overlays
    finally:
        w.io_worker=None; w.store.close()


@pytest.mark.parametrize('operation',['inspect','restore'])
@pytest.mark.parametrize('cancel',['escape','outside','palette','new_dialog'])
def test_cancelled_recovery_read_cannot_reopen_ui_or_replace_buffer(tmp_path,operation,cancel):
    w,_,_=make_workspace(tmp_path)
    queue=ControlledDiskQueue(); w.io_worker=queue
    try:
        name,metadata=archive_for_current_document(w)
        original=w.doc; text=original.text
        if operation=='inspect': w.inspect_recovery(name)
        else: w._restore_recovery_snapshot(name,metadata,None)
        loading=w.overlays[-1]
        assert len(queue.pending)==1 and loading.kind=='picker'
        expected=[]
        if cancel=='escape': w.escape()
        elif cancel=='outside':
            w.frame(80,24); w.mouse('down',0,23); w.mouse('up',0,23)
        elif cancel=='palette':
            w.open_palette(); expected=[w.overlays[-1]]
        else:
            w.choose_workspace(); expected=[w.overlays[-1]]
        drain(w,queue)
        assert w.overlays==expected and loading not in w.overlays
        assert w.doc is original and w.doc.text==text
        assert (w.store.directory/name).exists()
        assert not (w.store.directory/w.store.recovery_name).exists()
    finally:
        w.io_worker=None; w.store.close()


def test_owned_recovery_read_closes_loading_and_publishes_normally(tmp_path):
    w,_,_=make_workspace(tmp_path)
    queue=ControlledDiskQueue(); w.io_worker=queue
    try:
        name,metadata=archive_for_current_document(w)
        w.inspect_recovery(name); drain(w,queue)
        assert w.overlays[-1].title=='Recovery snapshot'
        w.overlay_action({'row':0})
        assert w.overlays[-1].title=='Loading recovery snapshot'
        drain(w,queue)
        assert w.doc.text=='recovered source' and w.doc.dirty
        assert not w.overlays and not (w.store.directory/name).exists()
        assert w.store.read_recovery(w.store.recovery_name,w.files.max_bytes)['documents'][0]['text']=='recovered source'
    finally:
        w.io_worker=None; w.store.close()


def test_cancelled_inventory_failure_is_quiet_and_rejected_submission_closes_loading(tmp_path,monkeypatch):
    w,_,_=make_workspace(tmp_path)
    queue=ControlledDiskQueue(); w.io_worker=queue
    def failed(*args,**kwargs): raise m.MDBMError('Read failed after cancellation')
    try:
        monkeypatch.setattr(w.store,'recovery_inventory',failed)
        w.show_recovery_inventory(); w.escape(); drain(w,queue)
        assert not w.overlays
        monkeypatch.setattr(w,'submit_io',lambda *args,**kwargs:False)
        w.inspect_recovery('recovery-'+('b'*32)+'.json')
        assert not w.overlays
    finally:
        w.io_worker=None; w.store.close()
