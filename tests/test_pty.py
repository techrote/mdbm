"""Real POSIX PTY integration; Windows/macOS native terminal checks remain manual."""
import io, os, sys, time
from pathlib import Path
import pytest
import mdbm as m

pytestmark=pytest.mark.skipif(os.name!='posix',reason='POSIX PTY suite; use the native terminal checklist on Windows')


def launch(tmp_path, content='alpha beta\n'):
    pexpect=pytest.importorskip('pexpect')
    target=tmp_path/'note.md';target.write_text(content,encoding='utf-8')
    conf=tmp_path/'mdbm.conf';conf.write_text('schema="mdbm-config/1.0"\n[application]\nroot="."\nstate_directory=".mdbm"\n[appearance]\ntheme="minimal"\nmotion="off"\n[session]\nrecovery_seconds=1\n')
    child=pexpect.spawn(sys.executable,[str(m.APP_DIR/'mdbm.py'),'--config',str(conf),'--no-restore',str(target)],
        env={**os.environ,'TERM':'xterm-256color'},encoding='utf-8',timeout=8,dimensions=(30,100))
    log=io.StringIO();child.logfile_read=log
    child.expect('MDBM');time.sleep(.18)
    return child,target,log


def finish(child):
    import pexpect
    child.sendcontrol('q');child.expect(pexpect.EOF);child.close()
    assert child.exitstatus==0


def test_pty_edit_save_exit_restores_modes(tmp_path):
    child,target,log=launch(tmp_path)
    try:
        child.send('\x1b[200~inserted\x1b[201~');time.sleep(.08);child.sendcontrol('s')
        child.expect('Saved');finish(child)
        assert target.read_text()=='insertedalpha beta\n'
        out=log.getvalue()
        assert '\x1b[?1049l' in out and '\x1b[?1006l' in out and '\x1b[?2004l' in out and '\x1b[?1004l' in out
        assert not (tmp_path/'.mdbm'/'lock.json').exists()
    finally:
        if child.isalive():child.close(force=True)


def test_pty_mouse_click_focus_and_insertion(tmp_path):
    child,target,log=launch(tmp_path)
    try:
        cfg=m.load_config(tmp_path/'mdbm.conf');w=m.Workspace(cfg,target,persist=False,restore=False)
        w.frame(100,30);r=w.geometry.contents[m.Tile.EDIT]
        gutter=min(len(str(w.doc.line_count))+2,max(0,r.w//3))
        x,y=r.x+gutter+6,r.y
        # SGR coordinates are one-based. No preceding keyboard focus command.
        child.send(f'\x1b[<0;{x+1};{y+1}M\x1b[<0;{x+1};{y+1}m')
        child.send('Z');time.sleep(.08);child.sendcontrol('s');child.expect('Saved');finish(child)
        assert target.read_text()=='alpha Zbeta\n'
    finally:
        if child.isalive():child.close(force=True)


def test_pty_dirty_exit_cancel_then_discard(tmp_path):
    import pexpect
    child,target,log=launch(tmp_path)
    try:
        child.send('dirty');child.sendcontrol('q');child.expect('Unsaved')
        child.send('\r');time.sleep(.08);assert child.isalive()
        child.sendcontrol('q');child.expect('Unsaved');child.send('d')
        child.expect(pexpect.EOF);child.close();assert child.exitstatus==0
        assert target.read_text()=='alpha beta\n'
    finally:
        if child.isalive():child.close(force=True)


def test_pty_terminal_resize_and_focus_reports_do_not_edit(tmp_path):
    child,target,log=launch(tmp_path)
    try:
        child.send('\x1b[O');time.sleep(.03);child.send('\x1b[I')
        child.setwinsize(12,40);time.sleep(.08);child.setwinsize(40,120);time.sleep(.08)
        child.sendcontrol('s');child.expect('Saved');finish(child)
        assert target.read_text()=='alpha beta\n'
    finally:
        if child.isalive():child.close(force=True)
