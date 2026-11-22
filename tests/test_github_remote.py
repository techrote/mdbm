"""Deterministic, network-free GitHub Markdown integration and trust-boundary tests."""
import base64
import copy
import urllib.parse

import pytest
import mdbm as m


def file_data(text):
    raw=text.encode("utf-8")
    return {"type":"file","encoding":"base64",
            "content":base64.b64encode(raw).decode("ascii"),"size":len(raw)}


class FakeStore:
    enabled=True
    owns_lock=True
    def __init__(self): self.records={}
    def read(self,name,max_bytes=0): return copy.deepcopy(self.records.get(name))
    def write(self,name,record): self.records[name]=copy.deepcopy(record)


def make_workspace(tmp_path, *, restore=False, persist=False):
    config=m.load_config(tmp_path/"mdbm.conf",raw={
        "schema":"mdbm-config/1.0",
        "application":{"root":str(tmp_path),"state_directory":str(tmp_path/".mdbm")},
        "appearance":{"theme":"minimal","seed":17,"motion":"off"},
        "capabilities":{"colour_depth":"truecolour"},
    })
    return m.Workspace(config,restore=restore,persist=persist)


@pytest.fixture
def github_workspace(tmp_path,monkeypatch):
    w=make_workspace(tmp_path)
    calls=[]
    def get(url,limit):
        calls.append(url)
        parsed=urllib.parse.urlsplit(url)
        assert parsed.hostname=="api.github.com"
        assert parsed.path.startswith("/repos/example/project")
        if parsed.path=="/repos/example/project":
            return {"default_branch":"main"}
        if parsed.path=="/repos/example/project/contents":
            return [{"type":"dir","path":"docs"},{"type":"file","path":"README.md"},
                    {"type":"file","path":"diagram.png"},{"type":"symlink","path":"bad.md"}]
        if parsed.path=="/repos/example/project/contents/docs":
            return [{"type":"file","path":"docs/guide.md"}]
        if parsed.path=="/repos/example/project/contents/README.md":
            return file_data("# Readme\n\n[Guide](docs/guide.md)\n\n[Home](#readme)\n")
        if parsed.path=="/repos/example/project/contents/docs/guide.md":
            return file_data("# Guide\n\n[Home](../README.md)\n\n[Root](/)\n")
        raise AssertionError("unexpected GitHub API request: "+url)
    monkeypatch.setattr(w.github,"_request_json",get)
    return w,calls


def test_open_github_url_in_readonly_tab_and_local_stays_editable(github_workspace,tmp_path):
    w,calls=github_workspace
    w.open_target("https://github.com/example/project/blob/main/README.md")
    assert w.doc.readonly
    assert w._active_state().github.path=="README.md"
    assert w.doc.path is None
    assert "Guide" in w.doc.text
    assert "READ ONLY" in w.notice
    w.frame(100,30)
    w.parse_sync(w.preview_width)
    assert "Guide" in w.frame(100,30).plain()
    assert calls
    with pytest.raises(m.MDBMError,match="read-only"): w.doc.insert("overwrite")
    w.clipboard="BAD"
    w.dispatch("edit.paste")
    assert "BAD" not in w.doc.text
    with pytest.raises(m.MDBMError,match="read-only"): w.save()
    with pytest.raises(m.MDBMError,match="cannot be saved"): w.perform_save(tmp_path/"danger.md",None)
    w.new_document()
    w.doc.insert("ordinary writable text")
    assert w.doc.dirty
    assert not w.doc.readonly


def test_remote_relative_links_anchor_tabs_and_external_confirmation(github_workspace):
    w,_=github_workspace
    w.open_target("https://github.com/example/project/blob/main/README.md")
    first=w.active_document_id
    w.frame(100,30);w.parse_sync(w.preview_width)
    w.activate_link("docs/guide.md")
    assert w.doc.readonly
    assert w._active_state().github.path=="docs/guide.md"
    second=w.active_document_id
    assert second!=first
    w.frame(100,30);w.parse_sync(w.preview_width)
    w.activate_link("../README.md")
    assert w.active_document_id==first
    assert len(w.documents)==3  # Welcome plus two remote documents
    w.activate_link("#readme")
    assert w._active_state().github.path=="README.md"
    w.activate_link("https://example.org/help")
    assert w.overlays and w.overlays[-1].kind=="confirm"
    assert "external" in w.overlays[-1].title.lower()
    w.activate_document(second)
    assert w._active_state().github.path=="docs/guide.md"


def test_repository_tree_directory_expand_select_and_no_mutation(github_workspace,tmp_path):
    w,_=github_workspace
    local=tmp_path/"README.md"
    local.write_text("SAFE",encoding="utf-8")
    w.open_target("https://github.com/example/project")
    assert w._active_state().github.directory
    assert "[README.md](README.md)" in w.doc.text
    rows=w.tree_rows()
    assert [(p.name,d,isdir) for p,d,isdir,_ in rows]==[
        ("docs",0,True),("README.md",0,False)]
    w.memories[m.Tile.FILESYSTEM].row=0
    w.tree_activate()
    rows=w.tree_rows()
    assert [p.name for p,_,_,_ in rows]==["docs","guide.md","README.md"]
    with pytest.raises(m.MDBMError,match="read-only"): w.rename_selected()
    with pytest.raises(m.MDBMError,match="read-only"): w.trash_selected()
    w.memories[m.Tile.FILESYSTEM].row=1
    w.tree_activate()
    assert w._active_state().github.path=="docs/guide.md"
    assert local.read_text()=="SAFE"


def test_refresh_updates_document_and_bypasses_cached_network(github_workspace,monkeypatch):
    w,calls=github_workspace
    w.open_target("https://github.com/example/project/blob/main/README.md")
    initial_id=w.active_document_id
    initial_text=w.doc.text
    first_count=sum("/contents/README.md" in c for c in calls)
    assert first_count==1
    w.open_target("https://github.com/example/project/blob/main/README.md")
    assert sum("/contents/README.md" in c for c in calls)==first_count
    old=w.github._request_json
    def changed(url,limit):
        if "/contents/README.md" in url: return file_data("# Changed\n")
        return old(url,limit)
    monkeypatch.setattr(w.github,"_request_json",changed)
    w.dispatch("navigate.refresh")
    assert w.doc.text=="# Changed\n"
    assert w.doc.readonly
    assert w.active_document_id==initial_id
    assert initial_text!=w.doc.text
    assert sum("/contents/README.md" in c for c in calls)==first_count


def test_url_paste_and_local_document_interop(github_workspace,tmp_path):
    w,_=github_workspace
    assert w.open_pasted_paths("https://github.com/example/project/blob/main/README.md")
    assert w._active_state().github
    local=tmp_path/"working.md"
    local.write_text("# Local",encoding="utf-8")
    w.open_target(local)
    assert w.doc.path==local and w._active_state().github is None
    w.doc.insert("Editable")
    assert w.doc.dirty
    w.open_target("https://github.com/example/project/blob/main/README.md")
    assert w.doc.readonly and w._active_state().github
    with pytest.raises(m.PathError):
        w.open_target("https://evil.example/file.md")


def test_remote_session_restores_from_disk_cache_offline(tmp_path,monkeypatch):
    w=make_workspace(tmp_path,persist=True)
    def get(url,limit):
        if "/contents/README.md" in url: return file_data("# Cached page\n")
        return [{"type":"file","path":"README.md"}]
    monkeypatch.setattr(w.github,"_request_json",get)
    w.open_target("https://github.com/example/project/blob/main/README.md")
    ident=w.active_document_id
    w.persist_session()
    assert w.store is not None
    w.store.close()
    restored=make_workspace(tmp_path,restore=True,persist=True)
    assert restored.active_document_id==ident
    assert restored.doc.text=="# Cached page\n"
    assert restored.doc.readonly
    assert restored._active_state().github.path=="README.md"
    restored.frame(100,30);restored.parse_sync(restored.preview_width)
    monkeypatch.setattr(restored.github,"_request_json",lambda *a: (_ for _ in ()).throw(m.GitHubError("offline")))
    restored.refresh_github()
    assert restored.doc.text=="# Cached page\n"
    assert "stale cache" in restored.notice
    assert restored.store is not None
    restored.store.close()


def test_backend_cache_refresh_failure_and_size(monkeypatch):
    store=FakeStore(); client=m.GitHubClient(store,max_bytes=512)
    loc=m.GitHubLocation("example","project","main","README.md",False)
    n=[0]
    def fake(url,limit):
        n[0]+=1
        return file_data("# "+str(n[0]))
    monkeypatch.setattr(client,"_request_json",fake)
    assert client.document(loc)==("# 1","network")
    assert client.document(loc)==("# 1","cache")
    assert client.document(loc,refresh=True)==("# 2","network")
    offline=m.GitHubClient(store,max_bytes=512)
    monkeypatch.setattr(offline,"_request_json",lambda *a: (_ for _ in ()).throw(m.GitHubError("offline")))
    assert offline.document(loc,refresh=True)==("# 2","stale cache")
    monkeypatch.setattr(client,"_request_json",lambda *a: file_data("x"*513))
    assert client.document(loc,refresh=True)==("# 2","stale cache")
    fresh=m.GitHubClient(max_bytes=512)
    monkeypatch.setattr(fresh,"_request_json",lambda *a: file_data("x"*513))
    with pytest.raises(m.GitHubError,match="size limit"): fresh.document(loc,refresh=True)


@pytest.mark.parametrize("url",[
    "http://github.com/o/r/blob/main/README.md",
    "https://user:pass@github.com/o/r/blob/main/README.md",
    "https://github.com/o/r/blob/main/../README.md",
    "https://github.com/o/r/blob/main/%2fetc.md",
    "https://github.com/o/r/blob/main/%0aREADME.md",
    "https://github.com/o/r/blob/main/script.py",
    "https://github.com/o/r/blob/main/README.md?token=secret",
])
def test_reject_invalid_github_urls(url):
    with pytest.raises(m.GitHubError): m.parse_github_url(url)


@pytest.mark.parametrize("link",[
    "../../escape.md","%2e%2e/%2e%2e/escape.md","image.png",
    "docs/%2fetc.md","//other.example/malicious.md","file.md?token=x",
])
def test_reject_unsafe_remote_links(link):
    loc=m.GitHubLocation("example","project","main","docs/index.md",False)
    with pytest.raises(m.GitHubError): m.resolve_github_link(loc,link)


def test_github_fragment_and_directory_link_resolution():
    loc=m.GitHubLocation("example","project","main","docs/README.md",False)
    assert m.resolve_github_link(loc,"#start").fragment=="start"
    assert m.resolve_github_link(loc,"../README.md").path=="README.md"
    assert m.resolve_github_link(loc,"/README.md").path=="README.md"
    assert m.resolve_github_link(loc,"/docs/").directory
    assert m.parse_github_url("https://raw.githubusercontent.com/example/project/main/README.md").path=="README.md"
    assert m.parse_github_url("https://github.com/example/project/tree/main/docs").directory
