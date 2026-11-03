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


def test_inventory_keyset_cursor_ties_and_preserves_aggregate_counts(tmp_path):
    store=m.StateStore(tmp_path/'state')
    assert store.enabled and store.owns_lock
    try:
        names=[]
        for index in range(29):
            name='recovery-'+f'{index:032x}'+'.json'
            path=store.directory/name
            path.write_bytes(b'{}')
            os.utime(path,ns=(10**18,10**18))
            names.append(name)
        first=store.recovery_inventory()
        last=first['entries'][-1]
        second=store.recovery_inventory(before=(last['mtime_ns'],last['name']))
        observed=[item['name'] for item in first['entries']+second['entries']]
        assert observed==list(reversed(names))
        assert first['total']==second['total']==len(names)
        assert first['bytes']==second['bytes']==2*len(names)
    finally:
        store.close()


def test_deferred_offer_survives_concurrent_newer_archive_removal(tmp_path,monkeypatch):
    state=tmp_path/'.mdbm'
    state.mkdir()
    source=tmp_path/'0.md'
    source.write_text('disk')
    # The sole matching archive begins at old index 24: deleting a newer
    # entry between offset-based pages used to skip it at index 23.
    for index in range(50):
        root=tmp_path if index==25 else tmp_path/'elsewhere'
        record=bundle(root,[f'unsaved {index}'])
        path=state/('recovery-'+f'{index:032x}'+'.json')
        path.write_text(json.dumps(record))
        os.utime(path,ns=(10**18+index,10**18+index))
    workspace=m.Workspace(config(tmp_path),source,restore=False)
    try:
        original=workspace.store.recovery_inventory
        scanned=[]
        def remove_newer_after_first_page(*args,**kwargs):
            page=original(*args,**kwargs)
            scanned.append(kwargs.get('before'))
            if len(scanned)==1:
                (state/('recovery-'+f'{49:032x}'+'.json')).unlink()
            return page
        monkeypatch.setattr(workspace.store,'recovery_inventory',remove_newer_after_first_page)
        workspace._offer_recovery(source)
        assert len(scanned)>=2 and scanned[0] is None and scanned[1] is not None
        assert workspace.overlays[-1].title=='Unsaved recovery found'
        workspace.overlays[-1].actions[0][1]()
        assert workspace.doc.text=='unsaved 25'
        assert source.read_text()=='disk'
    finally:
        workspace.store.close()

def test_compatibility_candidates_survive_concurrent_newer_archive_removal(tmp_path,monkeypatch):
    """A deletion between pages must not skip an older complete legacy bundle."""
    store=m.StateStore(tmp_path/'state')
    assert store.enabled and store.owns_lock
    try:
        for index in range(50):
            name='recovery-'+f'{index:032x}'+'.json'
            path=store.directory/name
            path.write_text(json.dumps(bundle(tmp_path,[f'unsaved {index}'])))
            os.utime(path,ns=(10**18+index,10**18+index))
        original=store.recovery_inventory
        cursors=[]
        def discard_after_first_page(*args,**kwargs):
            page=original(*args,**kwargs)
            cursors.append(kwargs.get('before'))
            if len(cursors)==1:
                (store.directory/('recovery-'+f'{49:032x}'+'.json')).unlink()
            return page
        monkeypatch.setattr(store,'recovery_inventory',discard_after_first_page)
        found=[(name,record['documents'][0]['text'])
               for name,record in store.recovery_candidates(16*1024*1024)]
        expected=[('recovery-'+f'{i:032x}'+'.json',f'unsaved {i}')
                  for i in reversed(range(49))]
        assert found==expected
        assert len(cursors)>=3 and cursors[0] is None and cursors[1] is not None
        assert any('Ignoring recovery-' in msg for msg in store.diagnostics)
    finally:
        store.close()
