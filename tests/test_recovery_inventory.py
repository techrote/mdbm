"""Pointer/command inventory routing, deferred archives and restoration guards."""
import json
import os

import pytest

import mdbm as m

from test_recovery_storage import bundle


def config(root):
    return m.load_config(root/'mdbm.conf',raw={'schema':'mdbm-config/1.0',
        'application':{'root':str(root),'state_directory':str(root/'.mdbm')},
        'appearance':{'theme':'minimal','motion':'off'}})


def test_newest_matching_deferred_archive_beyond_old_64_is_offered(tmp_path):
    state=tmp_path/'.mdbm'; state.mkdir()
    source=tmp_path/'0.md'; source.write_text('disk')
    for i in range(80):
        record=bundle(tmp_path if i==79 else tmp_path/'elsewhere',[f'unsaved {i}'])
        path=state/('recovery-'+f'{i:032x}'+'.json')
        path.write_text(json.dumps(record))
        os.utime(path,ns=(10**18+i,10**18+i))
    workspace=m.Workspace(config(tmp_path),source)
    try:
        prompt=workspace.overlays[-1]
        assert prompt.title=='Unsaved recovery found'
        prompt.actions[0][1]()
        assert workspace.doc.text=='unsaved 79'
        assert source.read_text()=='disk'
    finally: workspace.store.close()


def test_recovery_inventory_pages_are_mouse_and_palette_commands(tmp_path):
    workspace=m.Workspace(config(tmp_path),restore=False)
    try:
        for i in range(30):
            workspace.store.write('recovery-'+f'{i:032x}'+'.json',bundle(tmp_path,[str(i)]))
        workspace.dispatch('recovery.inventory')
        overlay=workspace.overlays[-1]
        assert '1–24 of 30' in overlay.title
        assert any(item.command=='recovery.inventory' for item in workspace.menu_items('File'))
        next_page=next(item for item in overlay.items if item.label=='Next page')
        workspace.dispatch(next_page.command,next_page.payload)
        assert '25–30 of 30' in workspace.overlays[-1].title
        item=next(item for item in workspace.overlays[-1].items if item.command=='recovery.inspect')
        workspace.dispatch(item.command,item.payload)
        assert workspace.overlays[-1].title=='Recovery snapshot'
        assert workspace.overlays[-1].actions[-1][0]=='Back'
    finally: workspace.store.close()


@pytest.mark.parametrize('schema',['mdbm-recovery/1.0','mdbm-recovery/2.0'])
def test_legacy_inventory_reports_encoded_json_size_without_hydrating_text(tmp_path,monkeypatch,schema):
    workspace=m.Workspace(config(tmp_path),restore=False)
    try:
        record=bundle(tmp_path,['\0😀é'*800])
        if schema=='mdbm-recovery/1.0':
            record={**record['documents'][0],'schema':schema,'root':str(tmp_path),'timestamp':123}
        name='recovery-'+('c'*32)+'.json'
        encoded=json.dumps(record,ensure_ascii=True).encode('utf-8')
        (workspace.store.directory/name).write_bytes(encoded)
        monkeypatch.setattr(workspace.store,'read_recovery',
            lambda *args,**kwargs:pytest.fail('Inventory must not hydrate legacy text'))
        workspace.show_recovery_inventory()
        row=next(item for item in workspace.overlays[-1].items if item.payload==name)
        assert f'{len(encoded)/1024:.1f} KiB JSON' in row.label
        assert 'KiB text' not in row.label
    finally: workspace.store.close()


@pytest.mark.parametrize('text,label',[('tiny','<0.1 KiB text'),('a'*256,'0.2 KiB text'),('', '0.0 KiB text')])
def test_v3_inventory_distinguishes_source_size_including_tiny_buffers(tmp_path,text,label):
    workspace=m.Workspace(config(tmp_path),restore=False)
    try:
        name='recovery-'+('d'*32)+'.json'
        workspace.store.write(name,bundle(tmp_path,[text]))
        workspace.show_recovery_inventory()
        row=next(item for item in workspace.overlays[-1].items if item.payload==name)
        assert label in row.label and 'KiB JSON' not in row.label
    finally: workspace.store.close()


def test_restore_is_held_while_save_pending_and_archive_retained(tmp_path):
    workspace=m.Workspace(config(tmp_path),restore=False)
    try:
        name='recovery-'+('a'*32)+'.json'
        workspace.store.write(name,bundle(tmp_path,['recovered']))
        metadata=workspace.store.recovery_metadata(name,workspace.files.max_bytes)
        original=workspace.doc
        workspace._pending_saves={'example':'saving.md'}
        workspace._restore_recovery_snapshot(name,metadata,None)
        assert workspace.doc is original
        assert 'finish' in workspace.notice
        assert (workspace.store.directory/name).exists()
    finally: workspace.store.close()


def test_inventory_loading_result_does_not_reopen_cancelled_overlay(tmp_path):
    workspace=m.Workspace(config(tmp_path),restore=False)
    try:
        jobs=[]
        workspace.submit_io=lambda key,work,done:jobs.append((work,done))
        workspace.show_recovery_inventory()
        workspace.close_overlay()
        work,done=jobs.pop()
        done(work(),None)
        assert not workspace.overlays
    finally: workspace.store.close()


def test_recovery_completion_after_closing_cannot_publish_or_consume_archive(tmp_path):
    workspace=m.Workspace(config(tmp_path),restore=False)
    try:
        name='recovery-'+('b'*32)+'.json'
        workspace.store.write(name,bundle(tmp_path,['selected','sibling']))
        metadata=workspace.store.recovery_metadata(name,workspace.files.max_bytes)
        jobs=[]
        workspace.submit_io=lambda key,work,done:jobs.append((work,done))
        workspace._restore_recovery_snapshot(name,metadata,tmp_path/'0.md')
        read,read_done=jobs.pop(0); read_done(read(),None)
        # The selected document is restored, and preservation of its sibling
        # is queued. Closing now must prevent that completion from scheduling
        # a fresh snapshot after the shutdown clear.
        assert workspace.doc.text=='selected' and len(jobs)==1
        sibling_write,sibling_done=jobs.pop(0)
        workspace._closing=True
        sibling_done(sibling_write(),None)
        assert not jobs
        assert (workspace.store.directory/name).exists()
        assert not (workspace.store.directory/workspace.store.recovery_name).exists()
    finally: workspace.store.close()


def test_queued_restore_does_not_replace_buffer_with_newer_archive(tmp_path):
    """Replacing an archive after confirmation must reject the stale Restore."""
    workspace=m.Workspace(config(tmp_path),restore=False)
    try:
        name='recovery-'+('a'*32)+'.json'
        workspace.store.write(name,bundle(tmp_path,['inspected unsaved text']))
        metadata=workspace.store.recovery_metadata(name,workspace.files.max_bytes)
        original=workspace.doc
        pending=[]
        workspace.submit_io=lambda key,work,done: (pending.append((work,done)) or True)
        workspace._restore_recovery_snapshot(name,metadata,None)
        assert len(pending)==1
        workspace.store.write(name,bundle(tmp_path,['newer unsaved text']))
        newer=(workspace.store.directory/name).read_bytes()
        work,done=pending.pop()
        with pytest.raises(m.MDBMError,match='changed since inspection') as failed:
            work()
        done(None,failed.value)
        assert workspace.doc is original
        assert (workspace.store.directory/name).read_bytes()==newer
        assert not pending  # No sibling rewrite or consumed-archive continuation.
        assert workspace.overlays[-1].title=='Recovery operation failed'
    finally:
        workspace.store.close()
