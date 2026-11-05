"""Bounded work, immutable snapshots and deferred navigation regressions."""
import threading
import time

import pytest

import mdbm as m


def completion(worker):
    with worker.condition:
        assert worker.condition.wait_for(lambda: worker.result is not None, timeout=5)
    result = worker.poll()
    assert result is not None
    return result


def test_printable_ascii_width_and_wrapping_avoid_per_glyph_work(monkeypatch):
    monkeypatch.setattr(m, 'display_units', lambda *args, **kwargs: pytest.fail('ASCII slow path'))
    assert m.width_of('Simple printable text!') == 22
    spans = (m.Span('abcdef', 'code'), m.Span(' gh', 'link', 'https://example.org'))
    rows = m.wrap_spans(spans, 4, preserve=True)
    assert [''.join(span.text for span in row) for row in rows] == ['abcd', 'ef g', 'h']
    assert rows[1][-1].link == 'https://example.org'


@pytest.mark.parametrize('text,ascii_only,width', [
    ('界\u0301', False, 2), ('界\u0301', True, 1), ('\t', False, 4), ('\x1b', False, 1),
])
def test_non_ascii_and_controls_keep_cell_aware_width(text, ascii_only, width):
    assert m.width_of(text, ascii_only) == width


@pytest.mark.parametrize('preserve', [False, True])
def test_bounded_long_unicode_paragraph_does_not_precalculate_entire_width(monkeypatch, preserve):
    original = m.display_units
    visited = 0

    def counted(*args, **kwargs):
        nonlocal visited
        for unit in original(*args, **kwargs):
            visited += 1
            yield unit

    monkeypatch.setattr(m, 'display_units', counted)
    rows = m.wrap_spans((m.Span('界' * 100_000),), 20, preserve=preserve, max_rows=2)
    assert len(rows) == 2 and visited <= 32
    assert [''.join(span.text for span in row) for row in rows] == ['界' * 10] * 2


def test_width_and_ascii_reflow_reuse_parsed_source():
    worker = m.PreviewWorker()
    text = '# Heading\n\n界 text ' * 20
    try:
        worker.request('doc', text, 7, 40, False)
        first = completion(worker)
        worker.request('doc', text, 7, 60, False)
        second = completion(worker)
        worker.request('doc', text, 7, 60, True)
        third = completion(worker)
        assert first[3][0] is second[3][0] is third[3][0]
        assert worker.parse_count == 1 and worker.layout_count == 3
        assert second[3][1].width == 60 and third[3][1].ascii_only
    finally:
        worker.close()


def test_ast_cache_is_bounded_by_documents_and_source_characters(monkeypatch):
    monkeypatch.setattr(m, 'MAX_PARSE_CACHE_DOCUMENTS', 2)
    monkeypatch.setattr(m, 'MAX_PARSE_CACHE_CHARS', 30)
    worker = m.PreviewWorker()
    try:
        for index in range(3):
            worker.request(str(index), '# Small', 0, 20, False)
            completion(worker)
        assert list(worker.parse_cache) == ['1', '2']
        worker.request('large', 'x' * 25, 0, 20, False)
        completion(worker)
        assert list(worker.parse_cache) == ['large'] and worker.cache_chars == 25
        worker.request('oversize', 'x' * 31, 0, 20, False)
        completion(worker)
        assert 'oversize' not in worker.parse_cache and worker.cache_chars <= 30
    finally:
        worker.close()


def test_cached_parse_does_not_cross_source_or_generation_identity():
    worker = m.PreviewWorker()
    try:
        worker.request('doc', '# First', 0, 20, False)
        completion(worker)
        worker.request('doc', '# Replacement', 0, 20, False)
        second = completion(worker)
        worker.request('doc', '# Replacement', 1, 20, False)
        third = completion(worker)
        assert worker.parse_count == 3
        assert second[3][0].headings[0].title == 'Replacement'
        assert third[3][0].generation == 1
    finally:
        worker.close()


def test_controller_ast_hint_reflows_documents_above_worker_cache_budget(monkeypatch):
    monkeypatch.setattr(m, 'MAX_PARSE_CACHE_CHARS', 1)
    text = '# Source\n\nbody'
    parsed = m.MarkdownEngine().parse(text, 5)
    worker = m.PreviewWorker()
    try:
        worker.request('doc', text, 5, 18, False, parsed=parsed)
        result = completion(worker)
        assert result[3][0] is parsed and worker.parse_count == 0
        assert not worker.parse_cache
    finally:
        worker.close()


def test_resize_during_parse_finishes_parse_once_then_lays_out_latest_width(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    parse = m.MarkdownEngine.parse

    def held_parse(self, text, generation=0, cancelled=None):
        entered.set()
        assert release.wait(5)
        return parse(self, text, generation, cancelled)

    monkeypatch.setattr(m.MarkdownEngine, 'parse', held_parse)
    worker = m.PreviewWorker()
    text = '# Stable source'
    try:
        worker.request('doc', text, 4, 20, False)
        assert entered.wait(5)
        for width in (30, 40, 50):
            worker.request('doc', text, 4, width, False)
        release.set()
        result = completion(worker)
        assert result[2] == 50 and worker.parse_count == 1
        assert worker.layout_count == 1
    finally:
        release.set()
        worker.close()


def test_superseded_layout_cancels_before_latest_source_is_published(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    layout = m.make_preview_layout

    def held_layout(parsed, width, ascii_only=False, cancelled=None):
        if parsed.generation == 1:
            entered.set()
            assert release.wait(5)
        return layout(parsed, width, ascii_only, cancelled)

    monkeypatch.setattr(m, 'make_preview_layout', held_layout)
    worker = m.PreviewWorker()
    try:
        worker.request('doc', '# Old', 1, 20, False)
        assert entered.wait(5)
        worker.request('doc', '# Current', 2, 20, False)
        release.set()
        result = completion(worker)
        assert result[1] == 2 and result[3][0].headings[0].title == 'Current'
    finally:
        release.set()
        worker.close()


def snapshot(text, document_id='doc'):
    doc = m.DocumentModel(text)
    return m.DocumentSearchSnapshot(document_id, doc, doc.generation, doc.text,
                                    doc._line_starts, 'note.md')


@pytest.mark.parametrize('needle,text', [
    ('needle', 'xxxxxxneedle\nneedle'),
    ('aaaa', 'a' * 37),
    ('\nsecond\n', 'first\nsecond\nthird\nsecond\n'),
    ('界界', 'prefix界界界end界界'),
    ('x' * 40, 'x' * 90),
])
def test_chunked_search_matches_normal_nonoverlapping_find(monkeypatch, needle, text):
    monkeypatch.setattr(m, 'SEARCH_CHUNK_CHARS', 7)
    expected = []
    position = 0
    while (start := text.find(needle, position)) >= 0:
        expected.append(start)
        position = start + len(needle)
    items, truncated = m.search_document_snapshots([snapshot(text)], needle, False)
    assert [item.payload[3] for item in items] == expected
    assert not truncated


def test_search_snapshot_retains_original_text_and_line_index_after_edit():
    frozen = snapshot('first\nneedle\nlast')
    frozen.document.replace_range(0, len(frozen.document.text), 'new document')
    items, _ = m.search_document_snapshots([frozen], 'needle', False)
    assert items[0].label.startswith('note.md:2:1')
    assert items[0].payload[2] == 0 and frozen.document.generation == 1


def test_large_no_match_search_checks_cancellation_at_chunk_boundaries(monkeypatch):
    monkeypatch.setattr(m, 'SEARCH_CHUNK_CHARS', 32)
    count = 0

    def cancelled():
        nonlocal count
        count += 1
        return count == 3

    with pytest.raises(m.CancelledWork):
        m.search_document_snapshots([snapshot('x' * 100_000)], 'absent', False, cancelled)
    assert count == 3


def test_search_worker_replaces_inflight_work_and_closes_without_late_result(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    search = m.search_document_snapshots

    def held_search(snapshots, needle, ascii_only, cancelled=None):
        if needle == 'old':
            entered.set()
            assert release.wait(5)
        return search(snapshots, needle, ascii_only, cancelled)

    monkeypatch.setattr(m, 'search_document_snapshots', held_search)
    worker = m.DocumentSearchWorker()
    source = [snapshot('old and current')]
    try:
        worker.request('old-request', source, 'old', False)
        assert entered.wait(5)
        worker.request('current-request', source, 'current', False)
        release.set()
        request, result = completion(worker)
        assert request == 'current-request' and result[0][0].payload[-1] == 'current'
        worker.close()
        worker.request('closed', source, 'old', False)
        assert worker.pending is None and worker.poll() is None
    finally:
        release.set()
        worker.close()


class ControlledSearch:
    def __init__(self):
        self.jobs = []
        self.result = None
        self.cancelled = []

    def request(self, *job):
        self.jobs.append(job)

    def cancel(self, request_id):
        self.cancelled.append(request_id)

    def complete(self, job):
        request_id, sources, needle, ascii_only = job
        self.result = (request_id, m.search_document_snapshots(sources, needle, ascii_only))

    def poll(self):
        result, self.result = self.result, None
        return result


def test_search_controller_returns_before_scan_and_escape_cancels(workspace):
    w = workspace
    w.search_worker = ControlledSearch()
    w.search_documents('alpha')
    overlay = w.overlays[-1]
    assert 'searching' in overlay.title and not overlay.items[0].enabled
    job = w.search_worker.jobs[-1]
    w.escape()
    assert w.search_worker.cancelled == [job[0]]
    w.search_worker.complete(job)
    w.tick_once()
    assert not w.overlays and w._document_search is None


def test_search_controller_publishes_only_current_request(workspace):
    w = workspace
    w.search_worker = ControlledSearch()
    w.search_documents('alpha')
    old = w.search_worker.jobs[-1]
    w.search_documents('beta')
    current = w.search_worker.jobs[-1]
    overlay = w.overlays[-1]
    w.search_worker.complete(old)
    w.tick_once()
    assert 'searching' in overlay.title
    w.search_worker.complete(current)
    w.tick_once()
    assert overlay.items[0].payload[-1] == 'beta'
    w.overlay_accept()
    assert w.doc.selected_text() == 'beta'


@pytest.mark.parametrize('change', ['edit', 'replace', 'close', 'root'])
def test_search_controller_rejects_changed_snapshot_owner(workspace, tmp_path, change):
    w = workspace
    w.search_worker = ControlledSearch()
    w.search_documents('alpha')
    overlay = w.overlays[-1]
    job = w.search_worker.jobs[-1]
    if change == 'edit':
        w.doc.insert('prefix ')
    elif change == 'replace':
        w.set_document(m.DocumentModel(w.doc.text))
    elif change == 'close':
        w._drop_document_states([w.active_document_id])
    else:
        other = tmp_path / 'other'
        other.mkdir()
        w.change_root(other)
    w.search_worker.complete(job)
    w.tick_once()
    assert not overlay.items[0].enabled
    assert w._document_search is None


class ControlledPreview:
    def __init__(self):
        self.job = None
        self.result = None
        self.parsed = None

    def request(self, *job, parsed=None):
        self.job = job
        self.parsed = parsed

    def complete(self):
        ident, text, generation, width, ascii_only = self.job
        parsed = self.parsed or m.MarkdownEngine().parse(text, generation)
        self.result = (ident, generation, width,
                       (parsed, m.make_preview_layout(parsed, width, ascii_only)))

    def poll(self):
        result, self.result = self.result, None
        return result


@pytest.mark.parametrize('setting', ['colour', 'motion', 'contrast', 'reduced', 'immersive'])
def test_paint_only_appearance_changes_do_not_enqueue_reflow(workspace, setting):
    w = workspace
    w.worker = ControlledPreview()
    completed = w.preview
    w.choose_appearance()
    if setting == 'colour':
        w.cycle_colour_depth()
    elif setting == 'motion':
        w.cycle_motion()
    elif setting == 'contrast':
        w.toggle_accessibility('high_contrast')
    elif setting == 'reduced':
        w.toggle_accessibility('reduced_motion')
    else:
        w.toggle_appearance('immersive')
    w.frame(120, 40)
    w.tick_once(time.monotonic() + 1)
    assert w.worker.job is None and w.preview is completed
    w.escape()
    w.tick_once(time.monotonic() + 1)
    assert w.worker.job is None and w.preview is completed


def test_cancelling_pending_ascii_preview_resubmits_restored_policy(workspace):
    w = workspace
    w.worker = ControlledPreview()
    parsed = w.parsed
    w.choose_appearance()
    w.toggle_accessibility('ascii_only')
    w.tick_once(time.monotonic() + 1)
    assert w.worker.job[-1] is True and not w.preview.ascii_only
    # The old completion is already in the slot when Escape restores the
    # original policy. Its generation/width alone cannot make it current.
    w.worker.complete()
    w.escape()
    assert w.requested_parse is None and not w.config.ascii
    w.tick_once(time.monotonic() + 1)
    assert w.worker.job[-1] is False and w.worker.parsed is parsed
    w.worker.complete()
    w.tick_once(time.monotonic() + 1)
    assert not w.preview.ascii_only and w.preview.generation == w.doc.generation
    assert w.parsed is parsed and not w.overlays


def test_fragment_navigation_defers_parsing_and_reuses_ast_for_width(workspace, monkeypatch):
    w = workspace
    w.worker = ControlledPreview()
    w.preview_width += 5
    parsed = w.parsed
    monkeypatch.setattr(w, 'parse_sync', lambda *args: pytest.fail('synchronous parse in navigation'))
    original = w.doc.cursor
    w.request_fragment('two')
    assert w.doc.cursor == original and w._pending_fragment
    assert w.worker.parsed is parsed
    w.worker.complete()
    w.tick_once()
    assert w.doc.row_of() == 4 and w.layout.focus == m.Tile.VIEW
    assert w._pending_fragment is None and w.preview.width == w.preview_width


@pytest.mark.parametrize('change', ['edit', 'cursor', 'switch', 'replace', 'dialog', 'focus'])
def test_late_fragment_navigation_does_not_steal_changed_context(workspace, change):
    w = workspace
    w.worker = ControlledPreview()
    w.preview_width += 5
    w.request_fragment('two')
    w.worker.complete()
    if change == 'edit':
        w.doc.insert('prefix ')
    elif change == 'cursor':
        w.doc.move_to(2)
    elif change == 'switch':
        w.new_document()
    elif change == 'dialog':
        w.info('New dialog', 'Keep this context')
    elif change == 'focus':
        w.set_layout('focus', m.Tile.FILESYSTEM)
    else:
        w.set_document(m.DocumentModel('# Other'))
    active, doc, position = w.active_document_id, w.doc, w.doc.cursor
    w.tick_once(time.monotonic() + 1)
    assert w.active_document_id == active and w.doc is doc and w.doc.cursor == position
    assert w._pending_fragment is None and w.layout.focus != m.Tile.VIEW


def test_open_existing_fragment_requests_preview_without_parsing(workspace, tmp_path, monkeypatch):
    w = workspace
    path = tmp_path / 'linked.md'
    path.write_text('# Destination\n\n## Target\n', encoding='utf-8')
    w.open_path(path)
    w.worker = ControlledPreview()
    monkeypatch.setattr(w, 'parse_sync', lambda *args: pytest.fail('synchronous parse in open'))
    w.open_path(path, 'target')
    assert w._pending_fragment and w.worker.job[1] == path.read_text(encoding='utf-8')
    w.worker.complete()
    w.tick_once()
    assert w.doc.row_of() == 2 and w.layout.focus == m.Tile.VIEW
