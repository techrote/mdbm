"""State-owner probes must never signal a Windows console or steal an unknown lock."""
import ctypes
import concurrent.futures
import gc
import json
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

import mdbm as m


def test_second_store_in_same_process_never_signals(tmp_path, monkeypatch):
    def signal_forbidden(*args):
        pytest.fail('Checking the current process must not send a signal')

    monkeypatch.setattr(m.os, 'kill', signal_forbidden)
    first = m.StateStore(tmp_path / 'state')
    second = m.StateStore(tmp_path / 'state')
    try:
        assert first.owns_lock and not second.owns_lock
        assert second.recovery_name != first.recovery_name
        assert first.read('lock.json')['token'] == first.token
    finally:
        second.close()
        first.close()


@pytest.mark.parametrize('alive', [False, True])
def test_windows_dispatch_never_calls_os_kill(monkeypatch, alive):
    def signal_forbidden(*args):
        pytest.fail('Windows liveness must not use os.kill')

    monkeypatch.setattr(m, 'os', SimpleNamespace(name='nt', getpid=lambda: 1,
                                               kill=signal_forbidden))
    probes = []
    monkeypatch.setattr(m, '_windows_process_alive',
                        lambda pid: probes.append(pid) or alive)
    assert m._process_alive(321) is alive
    assert probes == [321]


class NativeCall:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


@pytest.mark.parametrize('handle, wait_result, error, alive', [
    (9876543210, 258, 0, True),        # Live process / WAIT_TIMEOUT.
    (9876543210, 0, 0, False),         # Exited process / WAIT_OBJECT_0.
    (9876543210, 0xffffffff, 5, True), # Wait failed: never steal the lock.
    (0, None, 87, False),             # No process with the requested PID.
    (0, None, 5, True),               # Access denied: possibly still live.
    (0, None, 0, True),               # Unknown failure: possibly still live.
])
def test_windows_probe_conservative_results_and_handle_cleanup(
        monkeypatch, handle, wait_result, error, alive):
    api = SimpleNamespace(OpenProcess=NativeCall(handle),
                          WaitForSingleObject=NativeCall(wait_result),
                          CloseHandle=NativeCall(True))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: api, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: error, raising=False)
    assert m._windows_process_alive(321) is alive
    assert api.OpenProcess.calls == [(0x00100000, False, 321)]
    assert api.WaitForSingleObject.calls == ([(handle, 0)] if handle else [])
    assert api.CloseHandle.calls == ([(handle,)] if handle else [])


def test_unavailable_native_probe_keeps_owner(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('native query unavailable')

    monkeypatch.setattr(ctypes, 'WinDLL', unavailable, raising=False)
    assert m._windows_process_alive(321)


@pytest.mark.parametrize('pid', [None, True, '321', -1, 0, 2**31])
def test_invalid_owner_never_gets_probed_or_reclaimed(tmp_path, monkeypatch, pid):
    def signal_forbidden(*args):
        pytest.fail('Invalid owner PID must not be probed')

    monkeypatch.setattr(m.os, 'kill', signal_forbidden)
    state = tmp_path / 'state'
    state.mkdir()
    lock = state / 'lock.json'
    lock.write_text(json.dumps({'pid': pid, 'token': 'unknown'}))
    store = m.StateStore(state)
    try:
        assert not store.owns_lock
        assert json.loads(lock.read_text())['token'] == 'unknown'
    finally:
        store.close()


def test_native_live_child_lock_is_preserved_and_dead_child_lock_is_reclaimed(tmp_path):
    # Runs on Windows CI as well as POSIX. A real different PID exercises the
    # native HANDLE signatures, which same-process and mocked tests cannot.
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    state = tmp_path / 'state'
    state.mkdir()
    lock = state / 'lock.json'
    lock.write_text(json.dumps({'pid': child.pid, 'token': 'live-child'}))
    follower = reclaimed = None
    try:
        follower = m.StateStore(state)
        assert not follower.owns_lock
        assert json.loads(lock.read_text())['token'] == 'live-child'
        assert child.poll() is None
        child.terminate()
        child.wait(timeout=5)
        reclaimed = m.StateStore(state)
        assert reclaimed.owns_lock
        assert json.loads(lock.read_text())['token'] == reclaimed.token
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        if follower is not None:
            follower.close()
        if reclaimed is not None:
            reclaimed.close()


def test_simultaneous_stale_reclaimers_have_one_owner_and_crash_releases_guard(tmp_path):
    """Real processes hold their leases until every contender reports a result."""
    dead=subprocess.Popen([sys.executable,'-c','pass'])
    dead.wait(timeout=10)
    state=tmp_path/'state'; state.mkdir()
    (state/'lock.json').write_text(json.dumps({'pid':dead.pid,'token':'stale'}))
    code=textwrap.dedent('''
        import json,sys,time
        from pathlib import Path
        import mdbm as m
        original=m._process_alive
        def slow_probe(pid):
            alive=original(pid)
            if not alive: time.sleep(.10)
            return alive
        m._process_alive=slow_probe
        print('ready',flush=True)
        sys.stdin.readline()
        store=m.StateStore(Path(sys.argv[1]))
        print(json.dumps({'owner':store.owns_lock,'token':store.token}),flush=True)
        sys.stdin.readline()
        store.close()
    ''')
    children=[]; replacement=None
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=4)
    try:
        children=[subprocess.Popen([sys.executable,'-c',code,str(state)],
                  cwd=str(m.APP_DIR),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE,text=True) for _ in range(4)]
        ready=[pool.submit(child.stdout.readline) for child in children]
        assert [future.result(timeout=15).strip() for future in ready]==['ready']*4
        for child in children: child.stdin.write('go\n'); child.stdin.flush()
        result=[pool.submit(child.stdout.readline) for child in children]
        records=[json.loads(future.result(timeout=15)) for future in result]
        owners=[i for i,record in enumerate(records) if record['owner']]
        assert len(owners)==1
        winner=children[owners[0]]
        assert json.loads((state/'lock.json').read_text())['token']==records[owners[0]]['token']
        guard_stat=(state/'owner.guard').stat()
        winner.kill(); winner.wait(timeout=10)
        replacement=m.StateStore(state)
        assert replacement.owns_lock
        assert json.loads((state/'lock.json').read_text())['token']==replacement.token
        assert (state/'owner.guard').stat().st_ino==guard_stat.st_ino
    finally:
        if replacement: replacement.close()
        for child in children:
            if child.poll() is None:
                child.stdin.write('close\n'); child.stdin.flush()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
            for stream in (child.stdin,child.stdout,child.stderr):
                if stream: stream.close()
        pool.shutdown(wait=True,cancel_futures=True)


@pytest.mark.skipif(os.name!='posix',reason='Creating symlinks requires native Windows privileges')
def test_symlinked_owner_guard_disables_persistence_without_touching_target(tmp_path):
    state=tmp_path/'state'; state.mkdir()
    target=tmp_path/'target'; target.write_bytes(b'untouched')
    (state/'owner.guard').symlink_to(target)
    store=m.StateStore(state)
    try:
        assert not store.enabled and not store.owns_lock
        assert target.read_bytes()==b'untouched'
    finally: store.close()


def test_modern_unheld_guard_is_reclaimed_even_if_recorded_pid_was_reused(tmp_path):
    state=tmp_path/'state'; state.mkdir()
    (state/'lock.json').write_text(json.dumps({'pid':os.getpid(),'token':'f'*32,
                                              'protocol':'kernel-guard/1.0'}))
    store=m.StateStore(state)
    try:
        assert store.owns_lock
        assert store.read('lock.json')['token']==store.token
    finally: store.close()


def test_abandoned_store_releases_its_kernel_owner_and_lease(tmp_path):
    state=tmp_path/'state'
    original=m.StateStore(state); token=original.token
    assert original.owns_lock
    del original; gc.collect()
    replacement=m.StateStore(state)
    try:
        assert replacement.owns_lock
        assert not (state/('lease-'+token+'.json')).exists()
    finally: replacement.close()
