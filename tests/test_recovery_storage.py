"""Recovery publication budgets, lazy inventory and live-owner boundaries."""
import copy
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

import mdbm as m


def bundle(root, texts, stamp=123):
    docs=[]
    for i,text in enumerate(texts):
        docs.append({'document_id':f'{i+1:032x}','path':str(root/f'{i}.md'),
                     'text':text,'digest':hashlib.sha256(text.encode()).hexdigest(),
                     'baseline':hashlib.sha256(b'disk').hexdigest(),'fingerprint':None,
                     'cursor':0,'anchor':None,'newline':'\n','bom':False})
    return {'schema':'mdbm-recovery/2.0','root':str(root),'documents':docs,
            'active_document':docs[0]['document_id'],'timestamp':stamp}


@pytest.fixture
def store(tmp_path):
    store=m.StateStore(tmp_path/'state')
    yield store
    store.close()


def test_full_32_document_utf8_bundle_round_trips_without_json_expansion(store,tmp_path):
    maximum=32768; store.max_recovery_bytes=maximum
    # Each document is within its UTF-8 budget, but aggregate JSON with escaped
    # controls/emoji exceeds the old reader's eight-document envelope.
    texts=[('\0\\"é😀'+str(i)+'\n')*1100 for i in range(32)]
    assert all(len(text.encode())<=maximum for text in texts)
    record=bundle(tmp_path,texts)
    assert len(json.dumps(record).encode())>8*maximum+4096
    store.write(store.recovery_name,record)
    raw=store.read(store.recovery_name)
    assert raw['schema']=='mdbm-recovery/3.0'
    assert all('text' not in item for item in raw['documents'])
    assert len((store.directory/store.recovery_name).read_bytes())<32768
    restored=store.read_recovery(store.recovery_name,maximum)
    assert [item['text'] for item in restored['documents']]==texts
    assert sum(p.stat().st_size for p in store.directory.glob('content-*.bin'))==sum(len(text.encode()) for text in texts)


def test_streaming_legacy_bundle_exceeds_old_aggregate_limit_and_metadata_skips_text(store,tmp_path):
    maximum=4096
    record=bundle(tmp_path,['\0é😀'*400+str(i) for i in range(32)])
    encoded=json.dumps(record,ensure_ascii=True).encode()
    assert len(encoded)>8*maximum+4096
    (store.directory/'recovery.json').write_bytes(encoded)
    metadata=store.recovery_metadata('recovery.json',maximum)
    assert all(item['text'] is None for item in metadata['documents'])
    assert store.read_recovery('recovery.json',maximum)==record


def test_legacy_single_document_with_schema_after_text_is_read(store,tmp_path):
    record=bundle(tmp_path,['legacy\n\0é'])
    legacy={**record['documents'][0],'root':str(tmp_path),'timestamp':2,'schema':'mdbm-recovery/1.0'}
    (store.directory/'recovery.json').write_text(json.dumps(legacy))
    assert store.read_recovery('recovery.json',4096)==legacy
    assert store.recovery_metadata('recovery.json',4096)['text'] is None


def test_file_limit_and_pool_limit_fail_before_replacing_previous_snapshot(store,tmp_path):
    store.max_recovery_bytes=2048
    first=bundle(tmp_path,['old unsaved text'])
    store.write(store.recovery_name,first)
    original=(store.directory/store.recovery_name).read_bytes()
    with pytest.raises(m.MDBMError,match='source exceeds'):
        store.write(store.recovery_name,bundle(tmp_path,['é'*1025]))
    assert (store.directory/store.recovery_name).read_bytes()==original
    usage=store.recovery_inventory()['bytes']
    store.storage_limit=usage+100
    with pytest.raises(m.MDBMError,match='storage limit reached'):
        store.write(store.recovery_name,bundle(tmp_path,['new'*500]))
    assert (store.directory/store.recovery_name).read_bytes()==original
    assert store.read_recovery(store.recovery_name,2048)['documents'][0]['text']=='old unsaved text'


def test_metadata_budget_is_identical_for_write_and_read(store,tmp_path,monkeypatch):
    store.write(store.recovery_name,bundle(tmp_path,['old']))
    original=(store.directory/store.recovery_name).read_bytes()
    monkeypatch.setattr(m,'RECOVERY_MANIFEST_BYTES',512)
    record=bundle(tmp_path,['new']); record['documents'][0]['memories']={'large':'x'*1024}
    with pytest.raises(m.MDBMError,match='metadata exceeds'):
        store.write(store.recovery_name,record)
    assert (store.directory/store.recovery_name).read_bytes()==original
    with pytest.raises(m.MDBMError,match='manifest exceeds|field exceeds'):
        store.read_recovery(store.recovery_name,4096)


def test_shared_content_survives_archive_discard_and_orphans_are_collected(store,tmp_path):
    first='recovery-'+uuid.uuid4().hex+'.json'; second='recovery-'+uuid.uuid4().hex+'.json'
    store.write(first,bundle(tmp_path,['shared','first only']))
    store.write(second,bundle(tmp_path,['shared','second only']))
    assert len(list(store.directory.glob('content-*.bin')))==3
    store.discard_recovery(first)
    assert len(list(store.directory.glob('content-*.bin')))==2
    assert [d['text'] for d in store.read_recovery(second,4096)['documents']]==['shared','second only']
    store.discard_recovery(second)
    assert not list(store.directory.glob('content-*.bin'))


def test_corrupt_content_is_rejected_without_mutating_snapshot(store,tmp_path):
    store.write(store.recovery_name,bundle(tmp_path,['dirty']))
    manifest=(store.directory/store.recovery_name).read_bytes()
    blob=next(store.directory.glob('content-*.bin')); blob.write_bytes(b'WRONG')
    with pytest.raises(m.MDBMError,match='integrity'):
        store.read_recovery(store.recovery_name,4096)
    assert (store.directory/store.recovery_name).read_bytes()==manifest
    store.write(store.recovery_name,bundle(tmp_path,['dirty']))
    assert store.read_recovery(store.recovery_name,4096)['documents'][0]['text']=='dirty'


def test_unreadable_archive_prevents_automatic_orphan_deletion(store,tmp_path):
    orphan=store.directory/('content-'+hashlib.sha256(b'orphan').hexdigest()+'.bin')
    orphan.write_bytes(b'orphan')
    bad='recovery-'+uuid.uuid4().hex+'.json'
    (store.directory/bad).write_text('{broken')
    assert store.collect_recovery_garbage()==0 and orphan.exists()
    store.discard_recovery(bad,allow_invalid=True)
    assert not orphan.exists()


def test_inventory_pages_every_archive_newest_first_without_loading_payloads(store,tmp_path,monkeypatch):
    names=[]
    for i in range(90):
        name='recovery-'+f'{i:032x}'+'.json'; names.append(name)
        path=store.directory/name
        path.write_text(json.dumps(bundle(tmp_path,[str(i)])))
        os.utime(path,ns=(10**18+i,10**18+i))
    monkeypatch.setattr(store,'read_recovery',lambda *a,**k:pytest.fail('Inventory must not load payloads'))
    found=[]
    for offset in (0,24,48,72):
        page=store.recovery_inventory(offset)
        assert page['total']==90 and len(page['entries'])<=24
        found.extend(item['name'] for item in page['entries'])
    assert found==list(reversed(names))


def test_primary_cannot_discard_live_follower_snapshot(store,tmp_path):
    follower=m.StateStore(store.directory)
    assert store.owns_lock and not follower.owns_lock
    name=follower.recovery_name
    try:
        follower.write(name,bundle(tmp_path,['follower unsaved']))
        record=store.recovery_metadata(name,4096)
        assert store.snapshot_live(record)
        with pytest.raises(m.MDBMError,match='another instance'):
            store.discard_recovery(name)
        assert (store.directory/name).exists()
    finally: follower.close()
    assert not store.snapshot_live(record)
    store.discard_recovery(name)
    assert not (store.directory/name).exists()


def test_missing_blob_and_unsafe_reference_are_rejected(store,tmp_path):
    store.write(store.recovery_name,bundle(tmp_path,['dirty']))
    manifest=store.read(store.recovery_name)
    manifest['documents'][0]['content']='../outside'
    (store.directory/store.recovery_name).write_text(json.dumps(manifest))
    with pytest.raises(m.MDBMError,match='content reference'):
        store.read_recovery(store.recovery_name,4096)


def test_recovery_actions_cannot_address_session_or_lock_files(store):
    original=(store.directory/'lock.json').read_bytes()
    with pytest.raises(m.MDBMError,match='snapshot name'):
        store.discard_recovery('lock.json',allow_invalid=True)
    assert (store.directory/'lock.json').read_bytes()==original


def test_oversized_legacy_utf8_source_is_rejected_by_reader(store,tmp_path):
    record=bundle(tmp_path,['é'*100])
    (store.directory/'recovery.json').write_text(json.dumps(record))
    with pytest.raises(m.MDBMError,match='size limit'):
        store.read_recovery('recovery.json',100)


def test_legacy_metadata_budget_is_aggregate_not_per_field(store,tmp_path,monkeypatch):
    record=bundle(tmp_path,['small source'])
    record['documents'][0].update({f'extra{i}':'x'*800 for i in range(5)})
    (store.directory/'recovery.json').write_text(json.dumps(record))
    monkeypatch.setattr(m,'RECOVERY_MANIFEST_BYTES',2048)
    with pytest.raises(m.MDBMError,match='metadata exceeds its aggregate'):
        store.recovery_metadata('recovery.json',4096)


def test_legacy_object_key_count_is_bounded_before_large_dictionary_allocation(store,tmp_path):
    record=bundle(tmp_path,['small source'])
    record.update({f'extra{i}':0 for i in range(70)})
    (store.directory/'recovery.json').write_text(json.dumps(record))
    with pytest.raises(m.MDBMError,match='too many fields'):
        store.recovery_metadata('recovery.json',4096)

