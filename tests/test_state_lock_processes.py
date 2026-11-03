"""State-owner probes must never signal a Windows console or steal an unknown lock."""
import ctypes
import json
import os
import subprocess
import sys
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
