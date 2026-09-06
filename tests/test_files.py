import asyncio, codecs, dataclasses, hashlib, json, os, sys, time
from pathlib import Path
import pytest
import mdbm as m


def test_atomic_save_and_conflict(tmp_path):
    f=m.SafeFiles(tmp_path);p=tmp_path/'one.md'
    fp=f.save(p,'one\n',None)
    with pytest.raises(m.ConflictError):f.save(p,'unexpected',None)
    p.write_text('external')
    with pytest.raises(m.ConflictError):f.save(p,'two',fp)
    assert p.read_text()=='external' and not list(tmp_path.glob('.mdbm-save-*'))
    current=f.fingerprint(p);f.save(p,'approved',current);assert p.read_text()=='approved'


def test_conflict_during_save(tmp_path,monkeypatch):
    f=m.SafeFiles(tmp_path);p=tmp_path/'one.md';p.write_text('initial');fp=f.fingerprint(p)
    original=f.fingerprint;calls=0
    def race(path):
        nonlocal calls
        calls+=1
        if calls==2:p.write_text('raced')
        return original(path)
    monkeypatch.setattr(f,'fingerprint',race)
    with pytest.raises(m.ConflictError):f.save(p,'must not publish',fp)
    assert p.read_text()=='raced' and not list(tmp_path.glob('.mdbm-save-*'))


@pytest.mark.parametrize('raw',[b'a\r\nb\r\n',codecs.BOM_UTF8+b'a\nb\n',b'a\rb\r'])
def test_newline_bom_roundtrip(tmp_path,raw):
    p=tmp_path/'text.md';p.write_bytes(raw);f=m.SafeFiles(tmp_path);loaded=f.load(p)
    f.save(p,loaded.text,loaded.fingerprint,loaded.newline,loaded.bom)
    assert p.read_bytes()==raw


@pytest.mark.parametrize('path',['../escape.md','.mdbm/recovery.json','bad\x1bname.md'])
def test_root_protection(tmp_path,path):
    f=m.SafeFiles(tmp_path)
    with pytest.raises(m.PathError):f.resolve(path)


def test_symlink_protection(tmp_path):
    real=tmp_path/'real';real.mkdir();(real/'one.md').write_text('private')
    link=tmp_path/'link'
    try:link.symlink_to(real,target_is_directory=True)
    except OSError:pytest.skip('Symlink creation not permitted by this OS')
    f=m.SafeFiles(tmp_path)
    with pytest.raises(m.PathError):f.load(link/'one.md')
    with pytest.raises(m.PathError):f.save(link/'two.md','x',None)


def test_exact_move_and_no_overwrite(tmp_path):
    f=m.SafeFiles(tmp_path);src=tmp_path/'src.md';dst=tmp_path/'dst.md';raw=b'x\r\ny\n';src.write_bytes(raw)
    f.move(src,dst);assert not src.exists() and dst.read_bytes()==raw
    src.write_text('other')
    with pytest.raises(m.ConflictError):f.move(src,dst)
    assert src.read_text()=='other' and dst.read_bytes()==raw


def test_trash_failure_never_deletes(tmp_path,monkeypatch):
    p=tmp_path/'keep.md';p.write_text('keep')
    def reject(_):raise OSError('trash unavailable')
    monkeypatch.setattr(m,'send2trash',reject)
    with pytest.raises(m.PathError):m.SafeFiles(tmp_path).trash(p)
    assert p.read_text()=='keep'


@pytest.mark.parametrize('url',['javascript:alert(1)','data:text/html,x','file:///etc/passwd','https://user:pass@example.org','../escape.md','x%1b.md','//evil/path','mailto:a@b?body=%0d%0aattack'])
def test_unsafe_links(tmp_path,url):
    with pytest.raises(m.PathError):m.classify_link(url,m.SafeFiles(tmp_path),None)


def test_safe_links(tmp_path):
    f=m.SafeFiles(tmp_path)
    assert m.classify_link('#one',f,None).kind=='heading'
    assert m.classify_link('https://example.org/a',f,None).kind=='external'
    assert m.classify_link('local.md#one',f,None).kind=='local'


def test_recovery_fingerprint_integrity(workspace,tmp_path):
    w=workspace;p=tmp_path/'note.md';p.write_text('base')
    doc=m.DocumentModel.loaded(w.files.load(p));doc.insert('unsaved ')
    store=m.StateStore(tmp_path/'.mdbm');store.recovery(doc,w.files.root)
    record=store.read(store.recovery_name)
    p.write_text('externally changed');w.restore_recovery(record)
    assert w.doc.text=='unsaved base' and w.doc.dirty
    with pytest.raises(m.ConflictError):w.files.save(p,w.doc.text,w.doc.fingerprint)
    record['text']='tampered'
    with pytest.raises(m.MDBMError):w.restore_recovery(record)
    assert p.read_text()=='externally changed';store.close()


def test_state_lock_and_corrupt_read(tmp_path):
    a=m.StateStore(tmp_path/'.mdbm');b=m.StateStore(tmp_path/'.mdbm')
    assert a.owns_lock and not b.owns_lock and a.recovery_name!=b.recovery_name
    (tmp_path/'.mdbm'/'bad.json').write_text('{not json')
    assert a.read('bad.json') is None and a.diagnostics
    b.close();assert (tmp_path/'.mdbm'/'lock.json').exists()
    a.close();assert not (tmp_path/'.mdbm'/'lock.json').exists()


def test_script_arguments_and_allowlist(tmp_path):
    (tmp_path/'good.py').write_text('print("ok")');(tmp_path/'mdbm.py').write_text('')
    cfg=m.load_config(raw={'schema':'mdbm-config/1.0','scripts':{'allowlist':['good.py','mdbm.py','../x.py','none.py']}})
    allowed,diags=m.allowed_scripts(cfg,tmp_path)
    assert [p.name for p in allowed]==['good.py'] and len(diags)==3
    assert m.parse_script_args('["a b", "; echo not-a-shell"]')==['a b','; echo not-a-shell']
    for args in ['[1]','"one string"','{"a":"b"}','["\\u0000"]']:
        with pytest.raises(m.MDBMError):m.parse_script_args(args)


def test_script_output_sanitized_and_bounded(tmp_path):
    p=tmp_path/'echo.py';p.write_text('import sys\nprint(sys.argv[1])\nprint("\\x1b]52;c;evil\\x07")\nprint("x"*50000)\n')
    runner=m.ScriptRunner(4096)
    asyncio.run(runner.run(p,['; shell?'],[p]))
    assert runner.returncode==0 and runner.truncated and len(runner.output.encode())<=4096
    assert '\x1b' not in runner.output and '; shell?' in runner.output


def test_script_cancellation(tmp_path):
    p=tmp_path/'sleep.py';p.write_text('import time\nprint("ready",flush=True)\ntime.sleep(30)\n')
    async def scenario():
        r=m.ScriptRunner();task=asyncio.create_task(r.run(p,[],[p]))
        for _ in range(100):
            if 'ready' in r.output:break
            await asyncio.sleep(.01)
        await r.cancel();await asyncio.wait_for(task,3)
        assert r.cancelled and not r.running and r.returncode is not None
    asyncio.run(scenario())


@pytest.mark.parametrize('external',[False,True])
def test_rename_preserves_conflict_baseline(workspace,tmp_path,external):
    w=workspace; src=tmp_path/'original.md';src.write_text('loaded')
    w.set_document(m.DocumentModel.loaded(w.files.load(src)))
    w.tree_cache=[(src,0,False,False)]
    if external: src.write_text('external')
    w.rename_selected();w.overlays[-1].accept('renamed.md')
    assert w.doc.path==tmp_path/'renamed.md'
    if external:
        with pytest.raises(m.ConflictError):w.files.save(w.doc.path,'must not overwrite',w.doc.fingerprint)
        assert w.doc.path.read_text()=='external'
    else:
        w.files.save(w.doc.path,'saved after rename',w.doc.fingerprint)
        assert w.doc.path.read_text()=='saved after rename'


def test_change_root_preserves_custom_state_protection(workspace,tmp_path):
    w=workspace;new=tmp_path/'child';new.mkdir()
    state=new/'private-state';state.mkdir()
    w.config.data['application']['state_directory']=str(state)
    w.change_root(new)
    with pytest.raises(m.PathError):w.files.resolve(state/'session.json')
