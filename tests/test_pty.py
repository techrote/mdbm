"""Real POSIX PTY integration; Windows/macOS native terminal checks remain manual."""
import io, os, sys, time
from pathlib import Path
import pytest
import mdbm as m

pytestmark=pytest.mark.skipif(os.name!='posix',reason='POSIX PTY suite; use the native terminal checklist on Windows')


class TerminalLog(io.StringIO):
    """Keep raw teardown evidence and reconstruct the visible VT screen."""
    def __init__(self, columns=100, lines=30):
        super().__init__()
        pyte=pytest.importorskip('pyte')
        self.screen=pyte.Screen(columns,lines)
        self.stream=pyte.Stream(self.screen)

    def write(self, text):
        self.stream.feed(text)
        return super().write(text)


def expect_screen(child, text, *, present=True):
    """Wait on displayed cells, not contiguous bytes in an incremental diff."""
    import pexpect
    deadline=time.monotonic()+child.timeout
    while True:
        visible='\n'.join(child.logfile_read.screen.display)
        if (text in visible)==present:
            return
        remaining=deadline-time.monotonic()
        if remaining<=0:
            pytest.fail(f'Terminal text {text!r} present={present} was not observed.\n{visible}')
        try:
            child.read_nonblocking(65536,timeout=remaining)
        except (pexpect.TIMEOUT,pexpect.EOF) as exc:
            visible='\n'.join(child.logfile_read.screen.display)
            pytest.fail(f'{type(exc).__name__} waiting for terminal text {text!r} '
                        f'present={present}.\n{visible}')


def resize_terminal(child, lines, columns):
    child.logfile_read.screen.resize(lines=lines,columns=columns)
    child.setwinsize(lines,columns)


def test_screen_observation_preserves_cells_skipped_by_cursor_diff():
    log=TerminalLog(40,3)
    log.write('\x1b[1;11HM\x1b[H')
    log.write('F9_OPENED_\x1b[')
    log.write('CARKER')
    assert 'F9_OPENED_MARKER' in log.screen.display[0]
    assert 'F9_OPENED_MARKER' not in log.getvalue()


def launch(tmp_path, content='alpha beta\n'):
    pexpect=pytest.importorskip('pexpect')
    target=tmp_path/'note.md';target.write_text(content,encoding='utf-8')
    conf=tmp_path/'mdbm.conf';conf.write_text('schema="mdbm-config/1.0"\n[application]\nroot="."\nstate_directory=".mdbm"\n[appearance]\ntheme="minimal"\nmotion="off"\n[session]\nrecovery_seconds=1\n')
    child=pexpect.spawn(sys.executable,[str(m.APP_DIR/'mdbm.py'),'--config',str(conf),'--no-restore',str(target)],
        env={**os.environ,'TERM':'xterm-256color'},encoding='utf-8',timeout=8,dimensions=(30,100))
    log=TerminalLog();child.logfile_read=log
    expect_screen(child,'= note.md 1:1 EDIT')
    return child,target,log


def finish(child):
    import pexpect
    child.sendcontrol('q');child.expect(pexpect.EOF);child.close()
    assert child.exitstatus==0


def test_pty_edit_save_exit_restores_modes(tmp_path):
    child,target,log=launch(tmp_path)
    try:
        child.send('\x1b[200~inserted\x1b[201~');time.sleep(.08);child.sendcontrol('s')
        expect_screen(child,'Saved');finish(child)
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
        child.send('Z');time.sleep(.08);child.sendcontrol('s');expect_screen(child,'Saved');finish(child)
        assert target.read_text()=='alpha Zbeta\n'
    finally:
        if child.isalive():child.close(force=True)


def test_pty_dirty_exit_cancel_then_discard(tmp_path):
    import pexpect
    child,target,log=launch(tmp_path)
    try:
        child.send('dirty');child.sendcontrol('q');expect_screen(child,'Unsaved')
        child.send('\r');expect_screen(child,'Unsaved',present=False);assert child.isalive()
        child.sendcontrol('q');expect_screen(child,'Unsaved');child.send('d')
        child.expect(pexpect.EOF);child.close();assert child.exitstatus==0
        assert target.read_text()=='alpha beta\n'
    finally:
        if child.isalive():child.close(force=True)


def test_pty_terminal_resize_and_focus_reports_do_not_edit(tmp_path):
    child,target,log=launch(tmp_path)
    try:
        child.send('\x1b[O');time.sleep(.03);child.send('\x1b[I')
        resize_terminal(child,12,40);time.sleep(.08);resize_terminal(child,40,120);time.sleep(.08)
        child.sendcontrol('s');expect_screen(child,'Saved');finish(child)
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
        expect_screen(child,'drop target')
        # Ingress now preserves Filesystem focus; explicitly focus Edit to type.
        child.send('\x1b3'); time.sleep(.1)
        child.send('X'); time.sleep(.08); child.sendcontrol('s')
        expect_screen(child,'Saved'); finish(child)
        assert dropped.read_text()=='Xdrop target\n'
        assert target.read_text()=='alpha beta\n'
    finally:
        if child.isalive(): child.close(force=True)


def test_pty_bracketed_path_paste_in_edit_remains_text(tmp_path):
    child,target,log=launch(tmp_path)
    dropped=tmp_path/'another.md'; dropped.write_text('unchanged\n')
    try:
        child.send('\x1b[200~'+str(dropped)+'\x1b[201~')
        time.sleep(.08); child.sendcontrol('s'); expect_screen(child,'Saved')
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
        expect_screen(child,'FIRST_DROP_MARKER')
        child.send('\x1b[200~'+str(second)+'\x1b[201~')
        expect_screen(child,'SECOND_DROP_MARKER')
        child.send('\x1b3'); time.sleep(.10)  # Edit only after both opens.
        child.send('Z'); time.sleep(.08); child.sendcontrol('s')
        expect_screen(child,'Saved'); finish(child)
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
        expect_screen(child,'File-drop capture armed')
        child.send('\x1b[200~'+str(dropped)+'\x1b[201~')
        expect_screen(child,'F9_OPENED_MARKER')
        # One-shot reset: ordinary subsequent Edit paste is still text.
        child.send('\x1b[200~'+str(other)+'\x1b[201~')
        time.sleep(.08); child.sendcontrol('s'); expect_screen(child,'Saved')
        finish(child)
        assert dropped.read_text()==str(other)+'F9_OPENED_MARKER\n'
        assert other.read_text()=='NO_OPEN_MARKER\n'
        assert original.read_text()=='alpha beta\n'
    finally:
        if child.isalive(): child.close(force=True)


def test_pty_document_picker_search_and_save_all(tmp_path):
    child,original,log=launch(tmp_path)
    second=tmp_path/'second.md'; second.write_text('SECOND_SOURCE\n',encoding='utf-8')
    try:
        child.send('\x1b[200~FIRST_EDIT \x1b[201~')
        child.sendcontrol('o'); expect_screen(child,'Open file')
        child.send('\x1b[200~'+str(second)+'\x1b[201~\r')
        expect_screen(child,'SECOND_SOURCE')
        child.send('\x1b[200~SECOND_EDIT \x1b[201~')

        child.sendcontrol('b'); expect_screen(child,'Switch document')
        child.send('note.md\r')
        expect_screen(child,'Switch document',present=False)
        expect_screen(child,'FIRST_EDIT')

        child.sendcontrol('p'); child.send('Save all changed documents')
        expect_screen(child,'File / Save all changed documents')
        child.send('\r'); expect_screen(child,'Saved all 2 changed documents')
        assert original.read_text()=='FIRST_EDIT alpha beta\n'
        assert second.read_text()=='SECOND_EDIT SECOND_SOURCE\n'

        child.sendcontrol('p'); child.send('Search open documents')
        expect_screen(child,'Navigate / Search open documents')
        child.send('\r'); expect_screen(child,'Case-sensitive literal text in all open buffers')
        child.send('SECOND_EDIT\r'); expect_screen(child,'second.md:1:1')
        child.send('\r'); expect_screen(child,'Found in second.md')
        finish(child)
    finally:
        if child.isalive(): child.close(force=True)
