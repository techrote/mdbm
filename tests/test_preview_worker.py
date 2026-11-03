"""Deterministic interleavings for the worker's replaceable pending/result slots."""
import hashlib
import time

import pytest

import mdbm as m


class ControlledWorker:
    """Same lossy handoff contract as PreviewWorker, with explicit completion."""
    def __init__(self):
        self.pending = None
        self.result = None
        self.requests = []

    def request(self, *job):
        self.requests.append(job)
        self.pending = job

    def take(self):
        job, self.pending = self.pending, None
        return job

    def complete(self, job):
        ident, text, generation, width, ascii_only = job
        self.result = (ident, generation, width,
                       m.parse_and_layout(text, generation, width, ascii_only))

    def poll(self):
        result, self.result = self.result, None
        return result


def tick(workspace):
    workspace.tick_once(time.monotonic() + 1)


def enqueue_edit(workspace):
    workspace.doc.insert('changed ')
    tick(workspace)
    return workspace.worker.take()


def test_inactive_completion_is_cached_without_cross_document_publication(workspace):
    w = workspace
    w.worker = ControlledWorker()
    first_id = w.active_document_id
    w._view_links = ['https://stale.example/']
    w._selected_link = 0
    w.memories[m.Tile.VIEW].scroll = 10000
    first_job = enqueue_edit(w)

    w.new_document()
    tick(w)
    second_id, second_preview = w.active_document_id, w.preview
    w.worker.complete(first_job)
    tick(w)

    assert w.active_document_id == second_id and w.preview is second_preview
    first = w._state_by_id(first_id)
    assert first.preview.generation == first.doc.generation
    assert first.view_links == [] and first.selected_link == -1
    assert first.memories[m.Tile.VIEW].scroll < len(first.preview.rows)
    assert w._view_links == [] and w._selected_link == -1

    w.activate_document(first_id)
    tick(w)
    assert w.preview is first.preview
    assert len(w.worker.requests) == 2  # Reuse the completed cache.


def test_replaced_pending_request_is_resubmitted_on_return(workspace):
    w = workspace
    w.worker = ControlledWorker()
    first_id = w.active_document_id
    w.doc.insert('changed ')
    tick(w)  # A is pending but has not begun.
    w.new_document()
    tick(w)  # B replaces pending A.
    w.worker.complete(w.worker.take())
    tick(w)

    w.activate_document(first_id)
    tick(w)
    assert len(w.worker.requests) == 3
    job = w.worker.take()
    assert job[0] == first_id
    w.worker.complete(job)
    tick(w)
    assert w.preview.generation == w.doc.generation


def test_overwritten_unpolled_result_is_resubmitted_on_return(workspace):
    w = workspace
    w.worker = ControlledWorker()
    first_id = w.active_document_id
    first_job = enqueue_edit(w)
    w.new_document()
    tick(w)
    second_job = w.worker.take()
    w.worker.complete(first_job)
    w.worker.complete(second_job)  # B overwrites A before the next UI tick.

    w.activate_document(first_id)
    tick(w)
    assert len(w.worker.requests) == 3
    assert w.worker.pending[0] == first_id
    w.worker.complete(w.worker.take())
    tick(w)
    assert w.preview.generation == w.doc.generation


@pytest.mark.parametrize('change', ['generation', 'width', 'closed'])
def test_inactive_obsolete_completions_do_not_replace_caches(workspace, change):
    w = workspace
    w.worker = ControlledWorker()
    first_id = w.active_document_id
    old_job = enqueue_edit(w)
    if change == 'generation':
        w.doc.insert('newer ')
        tick(w)
    elif change == 'width':
        w.preview_width += 7
        tick(w)
    w.new_document()
    if change == 'closed':
        w._drop_document_states([first_id])
    first = w._state_by_id(first_id)
    first_preview = first.preview if first else None
    active_preview = w.preview

    w.worker.complete(old_job)
    tick(w)
    assert w.preview is active_preview
    if first:
        assert first.preview is first_preview
    else:
        assert w._state_by_id(first_id) is None


def test_cached_preview_reflows_after_resize_while_inactive(workspace):
    w = workspace
    w.worker = ControlledWorker()
    first_id = w.active_document_id
    first_preview = w.preview
    w.new_document()
    w.preview_width += 9
    w.activate_document(first_id)
    tick(w)
    assert w.worker.pending[0] == first_id
    assert w.worker.pending[3] != first_preview.width
    w.worker.complete(w.worker.take())
    tick(w)
    assert w.preview.width == w.preview_width


def test_synchronous_preview_fulfils_request_and_active_stale_result_is_rejected(workspace):
    w = workspace
    w.worker = ControlledWorker()
    job = enqueue_edit(w)
    w.preview_width += 4
    w.parse_sync()
    current = w.preview
    w.worker.complete(job)
    tick(w)
    assert w.preview is current
    assert len(w.worker.requests) == 1


def test_error_does_not_retry_each_tick_but_reactivation_can_retry(workspace):
    w = workspace
    w.worker = ControlledWorker()
    first_id = w.active_document_id
    job = enqueue_edit(w)
    w.worker.result = (first_id, job[2], job[3], 'controlled parser failure')
    tick(w)
    tick(w)
    assert len(w.worker.requests) == 1
    assert 'controlled parser failure' in w.notice
    w.new_document()
    w.activate_document(first_id)
    tick(w)
    assert len(w.worker.requests) == 2


def test_payload_identity_must_match_request_envelope(workspace):
    w = workspace
    current = w.preview
    payload = m.parse_and_layout('# wrong generation', w.doc.generation + 1,
                                 w.preview_width, False)
    assert not w.accept_preview(w.doc.generation, w.preview_width, payload)
    assert w.preview is current


def recovery_for_active(workspace, text='# Recovered'):
    item = workspace._recovery_document_record(workspace._active_state())
    item.update(text=text, digest=hashlib.sha256(text.encode()).hexdigest())
    return {'schema': 'mdbm-recovery/2.0', 'root': str(workspace.files.root),
            'active_document': workspace.active_document_id, 'documents': [item]}


@pytest.mark.parametrize('inactive', [False, True])
@pytest.mark.parametrize('edited_before', [False, True])
def test_recovery_replacement_rejects_previous_model_completion(
        workspace, inactive, edited_before):
    w = workspace
    w.worker = ControlledWorker()
    w._append_document(m.DocumentModel('# Original'))
    ident, original = w.active_document_id, w.doc
    if edited_before:
        original.insert('temporary ')
        original.undo()
    assert not original.dirty
    tick(w)
    old_job = w.worker.take()
    w.restore_recovery_bundle(recovery_for_active(w))
    recovered = w.doc
    assert w.active_document_id == ident and recovered is not original
    assert recovered.generation > original.generation
    if inactive:
        tick(w)  # Schedule recovered A before switching away.
        w.new_document()

    active_preview = w.preview
    w.worker.complete(old_job)
    tick(w)
    assert w.preview is active_preview
    if inactive:
        state = w._state_by_id(ident)
        assert state.preview.generation != recovered.generation
        w.activate_document(ident)
        tick(w)

    assert w.worker.pending[0] == ident
    assert w.worker.pending[1] == '# Recovered'
    w.worker.complete(w.worker.take())
    tick(w)
    assert w.doc is recovered
    assert w.parsed.headings[0].title == 'Recovered'


def test_recovery_of_closed_document_gets_fresh_live_identity(workspace):
    w = workspace
    w.worker = ControlledWorker()
    w._append_document(m.DocumentModel('# Original'))
    old_ident = w.active_document_id
    record = recovery_for_active(w)
    tick(w)
    old_job = w.worker.take()
    w.close_document()
    assert w._state_by_id(old_ident) is None
    w.restore_recovery_bundle(record)
    new_ident = w.active_document_id
    assert new_ident != old_ident and w.doc.text == '# Recovered'
    current = w.preview
    w.worker.complete(old_job)
    tick(w)
    assert w.preview is current and w.worker.pending[0] == new_ident
    w.worker.complete(w.worker.take())
    tick(w)
    assert w.parsed.headings[0].title == 'Recovered'


def test_ascii_policy_invalidates_inactive_completed_layout(workspace):
    w = workspace
    w.worker = ControlledWorker()
    w.set_document(m.DocumentModel('界界界界'))
    w.parse_sync(4)
    ident = w.active_document_id
    assert len(w.preview.rows) == 2 and not w.preview.ascii_only
    w.new_document()
    w.toggle_accessibility('ascii_only')
    w.activate_document(ident)
    tick(w)
    assert w.worker.pending[4] is True
    w.worker.complete(w.worker.take())
    tick(w)
    assert w.preview.ascii_only and len(w.preview.rows) == 1
    assert ''.join(s.text for s in w.preview.rows[0].spans) == '????'


@pytest.mark.parametrize('inactive', [False, True])
def test_ascii_toggle_rejects_old_policy_completion(workspace, inactive):
    w = workspace
    w.worker = ControlledWorker()
    w.set_document(m.DocumentModel('界界界界'))
    w.preview_width = 4
    ident = w.active_document_id
    tick(w)
    old_job = w.worker.take()
    w.toggle_accessibility('ascii_only')
    tick(w)
    if inactive:
        w.new_document()
    current = w.preview
    w.worker.complete(old_job)
    tick(w)
    assert w.preview is current
    if inactive:
        w.activate_document(ident)
        tick(w)
    assert w.worker.pending[0] == ident and w.worker.pending[4] is True
    w.worker.complete(w.worker.take())
    tick(w)
    assert w.preview.ascii_only and len(w.preview.rows) == 1
