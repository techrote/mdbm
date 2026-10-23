"""MDBM-003 conservative terminal path ingress and canonical lifecycle regressions."""
from pathlib import Path

import pytest
import mdbm as m


def workspace(tmp_path):
    conf=m.load_config(tmp_path/'mdbm.conf',raw={
        'schema':'mdbm-config/1.0',
        'application':{'root':str(tmp_path),'state_directory':str(tmp_path/'.mdbm')},
        'appearance':{'theme':'minimal','seed':17,'motion':'off'},
        'capabilities':{'colour_depth':'truecolour'},
    })
    return m.Workspace(conf,restore=False,persist=False)


def start(w, path):
    w.set_document(m.DocumentModel.loaded(w.files.load(path)))


def paste_from_filesystem(w, payload):
    w.set_layout('focus',m.Tile.FILESYSTEM)
    w.handle_text(payload,paste=True)


@pytest.mark.parametrize(('payload','expected'),[
    ('/home/user/notes.md',['/home/user/notes.md']),
    (r'C:\notes\example.md',[r'C:\notes\example.md']),
    ('relative.md',['relative.md']),
    ('"README"',['README']),
    ('"./folder/notes with spaces.md"',['./folder/notes with spaces.md']),
    ("'/tmp/with spaces.md'",['/tmp/with spaces.md']),
    ('"one.md" "two with spaces.md"',['one.md','two with spaces.md']),
    (r'/tmp/my\ file.md',['/tmp/my file.md']),
    (r'notes\ with\ spaces.md',['notes with spaces.md']),
    (r'C:\My Documents\notes.md',[r'C:\My Documents\notes.md']),
    ('a.md\nb.md',['a.md','b.md']),
    ('"a.md"\n"b.md"',['a.md','b.md']),
    ('a.md\r\nb.md',['a.md','b.md']),
])
def test_parser_recognizes_supported_path_forms(payload,expected):
    assert m.parse_terminal_paths(payload)==expected


@pytest.mark.parametrize('payload',[
    '', 'hello', 'just some pasted prose', 'a.md\n\nb.md',
    'echo $(touch /tmp/bad)', '$(id)', r'$HOME/secret.md',
    r'%USERPROFILE%\secret.md', '~/file.md', 'a.md | sh',
    'a.md && b.md', 'a.md > output', 'a*.md', 'a?.md', 'a;rm.md',
    'a.md # comment', 'a.md\nrm -rf .', 'https://example.org/a.md',
    'file:///tmp/a.md', 'ftp://example.org/a.md',
    'mailto:user@example.org', 'smb://server/share.md', 'C:relative.md',
    '"a.md" b.md', '"a.md""b.md"', '"unclosed.md',
    '/tmp/a.md\x1b[0m', '/tmp/a.md\x00', '/tmp/a.md\tb.md',
    '/tmp/a.md\ud800', '"bad\npath.md"', '--help',
])
def test_parser_rejects_ambiguous_control_and_command_payloads(payload):
    assert m.parse_terminal_paths(payload) is None


def test_parser_rejects_backtick_command_substitution():
    assert m.parse_terminal_paths(chr(96)+'id'+chr(96)) is None


def test_parser_rejects_bounded_payloads():
    assert m.parse_terminal_paths('a'*16385+'.md') is None
    assert m.parse_terminal_paths(' '.join('"x.md"' for _ in range(m.MAX_OPEN_DOCUMENTS+1))) is None


def test_workspace_paste_opens_local_file(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'
    a.write_text('A'); b.write_text('B')
    w=workspace(tmp_path); start(w,a); original=w.active_document_id
    paste_from_filesystem(w,str(b))
    assert len(w.documents)==2
    assert w.active_document_id!=original and w.doc.path==b
    assert w.doc.text=='B'
    assert w.notice=='Opened 1 file'


def test_multiple_quoted_paths_preserve_order_and_active_identity(tmp_path):
    a=tmp_path/'initial.md'; a.write_text('A')
    b=tmp_path/'one space.md'; b.write_text('B')
    c=tmp_path/'two space.md'; c.write_text('C')
    w=workspace(tmp_path); start(w,a)
    paste_from_filesystem(w,f'"{b}" "{c}"')
    assert [state.doc.path for state in w.documents]==[a,b,c]
    assert w.doc.path==c and w.active_document_id==w.documents[-1].id
    assert w.notice=='Opened 2 files'


def test_duplicate_drop_activates_original_document_without_duplication(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'
    a.write_text('A'); b.write_text('B')
    w=workspace(tmp_path); start(w,a); a_id=w.active_document_id
    w.open_path(b)
    paste_from_filesystem(w,str(a))
    assert len(w.documents)==2 and w.active_document_id==a_id
    assert w.notice=='Opened 0 files · 1 already open'


def test_multi_drop_partial_success_still_activates_final_success(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'; c=tmp_path/'c.md'
    a.write_text('A'); b.write_text('B'); c.write_text('C')
    missing=tmp_path/'missing.md'
    w=workspace(tmp_path); start(w,a)
    paste_from_filesystem(w,f'"{b}" "{missing}" "{c}"')
    assert len(w.documents)==3 and w.doc.path==c
    assert w.notice_error and 'Opened 2 files · 1 rejected' in w.notice
    assert 'missing.md' in w.notice and 'Dropped file does not exist.' in w.notice
    assert any('Drop rejected:' in item for item in w.diagnostics)


def test_multi_drop_duplicate_and_valid_keeps_last_success_active(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'; c=tmp_path/'c.md'
    for item in (a,b,c): item.write_text(item.name)
    w=workspace(tmp_path); start(w,a)
    paste_from_filesystem(w,f'"{b}" "{b}" "{c}"')
    assert len(w.documents)==3 and w.doc.path==c
    assert w.notice=='Opened 2 files · 1 already open'


@pytest.mark.parametrize(('kind','reason'),[
    ('outside','escapes the workspace root'),
    ('missing','Dropped file does not exist.'),
    ('directory','Only existing regular files may be dropped.'),
    ('state','recovery/state paths are protected'),
])
def test_rejected_targets_do_not_mutate_documents_or_root(tmp_path,kind,reason):
    a=tmp_path/'a.md'; a.write_text('A')
    w=workspace(tmp_path); start(w,a)
    if kind=='outside':
        path=tmp_path.parent/'b.md'; path.write_text('B')
    elif kind=='missing':
        path=tmp_path/'missing.md'
    elif kind=='directory':
        path=tmp_path/'subdir'; path.mkdir()
    else:
        path=tmp_path/'.mdbm'/'secret.md'; path.parent.mkdir(); path.write_text('secret')
    before=w.active_document_id
    paste_from_filesystem(w,str(path))
    assert len(w.documents)==1 and w.active_document_id==before
    assert w.files.root==tmp_path.resolve() and w.doc.text=='A'
    assert w.notice_error and reason in w.notice


def test_rejected_symlink_does_not_open(tmp_path):
    a=tmp_path/'a.md'; a.write_text('A')
    link=tmp_path/'linked.md'
    try: link.symlink_to(a)
    except (OSError,NotImplementedError): pytest.skip('symlinks unavailable')
    w=workspace(tmp_path); start(w,a)
    paste_from_filesystem(w,str(link))
    assert len(w.documents)==1
    assert 'Symbolic links and junctions' in w.notice


def test_invalid_utf8_file_rejected_without_document_changes(tmp_path):
    a=tmp_path/'a.md'; a.write_text('A')
    bad=tmp_path/'bad.md'; bad.write_bytes(b'\xff\xfe')
    w=workspace(tmp_path); start(w,a)
    paste_from_filesystem(w,str(bad))
    assert len(w.documents)==1 and w.doc.text=='A'
    assert w.notice_error and 'UTF-8' in w.notice


def test_text_paste_into_edit_remains_literal(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'
    a.write_text('A'); b.write_text('B')
    w=workspace(tmp_path); start(w,a)
    assert w.layout.focus==m.Tile.EDIT
    w.handle_text(str(b),paste=True)
    assert len(w.documents)==1 and w.doc.path==a
    assert str(b) in w.doc.text and w.doc.dirty


def test_overlay_input_paste_remains_literal(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'
    a.write_text('A'); b.write_text('B')
    w=workspace(tmp_path); start(w,a)
    w.set_layout('focus',m.Tile.FILESYSTEM)
    w.request_text('Search','',lambda v:None)
    w.handle_text(str(b),paste=True)
    assert w.overlays[-1].editor.text==str(b)
    assert w.doc.path==a and len(w.documents)==1


def test_menu_overlay_disables_path_ingress(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'
    a.write_text('A'); b.write_text('B')
    w=workspace(tmp_path); start(w,a)
    w.set_layout('focus',m.Tile.FILESYSTEM); w.open_menu('File')
    w.handle_text(str(b),paste=True)
    assert len(w.documents)==1 and w.doc.path==a
    assert w.overlays and w.overlays[-1].kind=='menu'


def test_vim_insert_mode_pastes_text_but_normal_mode_opens(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'; c=tmp_path/'c.md'
    for item in (a,b,c): item.write_text(item.name)
    w=workspace(tmp_path); start(w,a)
    w.toggle_profile()
    assert w.layout.focus==m.Tile.EDIT and w.vim_mode=='normal'
    w.handle_text(str(b),paste=True)
    assert len(w.documents)==2 and w.doc.path==b
    w.vim_mode='insert'
    w.handle_text(str(c),paste=True)
    assert len(w.documents)==2 and w.doc.path==b
    assert str(c) in w.doc.text


def test_ambiguous_non_editor_paste_does_not_open(tmp_path):
    a=tmp_path/'a.md'; a.write_text('A')
    w=workspace(tmp_path); start(w,a)
    paste_from_filesystem(w,'text that is not a file path')
    assert len(w.documents)==1 and w.doc.text=='A'
    assert not w.notice_error


def test_meta_and_url_payloads_never_open_a_valid_member(tmp_path):
    a=tmp_path/'a.md'; b=tmp_path/'b.md'
    a.write_text('A'); b.write_text('B')
    w=workspace(tmp_path); start(w,a)
    for payload in (f'"{b}" "$(touch foo)"',f'"{b}" https://example.com'):
        paste_from_filesystem(w,payload)
        assert len(w.documents)==1 and w.doc.path==a


def test_drop_directory_change_race_cannot_replace_workspace(tmp_path,monkeypatch):
    a=tmp_path/'a.md'; a.write_text('A')
    directory=tmp_path/'changed.md'; directory.mkdir()
    w=workspace(tmp_path); start(w,a)
    real_is_file=Path.is_file
    # Simulate a file turning into a directory after ingress checks the type.
    def claimed_file(path):
        return True if path==directory else real_is_file(path)
    monkeypatch.setattr(Path,'is_file',claimed_file)
    paste_from_filesystem(w,str(directory))
    assert w.files.root==tmp_path.resolve()
    assert len(w.documents)==1 and w.doc.path==a
    assert w.notice_error and 'Only existing regular files' in w.notice
