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


def test_pty_bracketed_path_paste_opens_document(tmp_path):
    child,target,log=launch(tmp_path)
    dropped=tmp_path/'dropped.md'; dropped.write_text('drop target\n')
    try:
        # Alt+1 focuses Filesystem: ordinary Edit insertion must be exited first.
        child.send('\x1b1'); time.sleep(.12)
        child.send('\x1b[200~'+str(dropped)+'\x1b[201~')
        child.expect('drop target')
        # Ingress now preserves Filesystem focus; explicitly focus Edit to type.
        child.send('\x1b3'); time.sleep(.1)
        child.send('X'); time.sleep(.08); child.sendcontrol('s')
        child.expect('Saved'); finish(child)
        assert dropped.read_text()=='Xdrop target\n'
        assert target.read_text()=='alpha beta\n'
    finally:
        if child.isalive(): child.close(force=True)


def test_pty_bracketed_path_paste_in_edit_remains_text(tmp_path):
    child,target,log=launch(tmp_path)
    dropped=tmp_path/'another.md'; dropped.write_text('unchanged\n')
    try:
        child.send('\x1b[200~'+str(dropped)+'\x1b[201~')
        time.sleep(.08); child.sendcontrol('s'); child.expect('Saved')
        finish(child)
        assert target.read_text()==str(dropped)+'alpha beta\n'
        assert dropped.read_text()=='unchanged\n'
    finally:
        if child.isalive(): child.close(force=True)



def test_pty_consecutive_file_drops_without_refocusing(tmp_path):
    child,original,log=launch(tmp_path)
    first=tmp_path/'first.md'; second=tmp_path/'second.md'
    first.write_text('FIRST_DROP_MARKER\n')
    second.write_text('SECOND_DROP_MARKER\n')
    try:
        child.send('\x1b1'); time.sleep(.12)  # Focus Filesystem once.
        child.send('\x1b[200~'+str(first)+'\x1b[201~')
        child.expect('FIRST_DROP_MARKER')
        child.send('\x1b[200~'+str(second)+'\x1b[201~')
        child.expect('SECOND_DROP_MARKER')
        child.send('\x1b3'); time.sleep(.10)  # Edit only after both opens.
        child.send('Z'); time.sleep(.08); child.sendcontrol('s')
        child.expect('Saved'); finish(child)
        assert original.read_text()=='alpha beta\n'
        assert first.read_text()=='FIRST_DROP_MARKER\n'
        assert second.read_text()=='ZSECOND_DROP_MARKER\n'
    finally:
        if child.isalive(): child.close(force=True)


def test_pty_f9_explicit_edit_drop_once(tmp_path):
    child,original,log=launch(tmp_path)
    dropped=tmp_path/'opened.md'; dropped.write_text('F9_OPENED_MARKER\n')
    other=tmp_path/'not-opened.md'; other.write_text('NO_OPEN_MARKER\n')
    try:
        child.send('\x1b[20~')  # xterm/VT F9.
        child.expect('File-drop capture armed')
        child.send('\x1b[200~'+str(dropped)+'\x1b[201~')
        child.expect('F9_OPENED_MARKER')
        # One-shot reset: ordinary subsequent Edit paste is still text.
        child.send('\x1b[200~'+str(other)+'\x1b[201~')
        time.sleep(.08); child.sendcontrol('s'); child.expect('Saved')
        finish(child)
        assert dropped.read_text()==str(other)+'F9_OPENED_MARKER\n'
        assert other.read_text()=='NO_OPEN_MARKER\n'
        assert original.read_text()=='alpha beta\n'
    finally:
        if child.isalive(): child.close(force=True)
