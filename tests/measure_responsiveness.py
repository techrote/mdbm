"""Reproducible input-to-composed-frame probe, separate from native-terminal QA.

Run from the checkout: python tests/measure_responsiveness.py --lines 100000
This reports measurements, not a timing-sensitive pass/fail test. Source edits
still require a full Markdown parse. Width-only reflow must report one parser
call across both stages. Run on an otherwise idle machine for comparable data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mdbm as m


def percentiles(values):
    ordered = sorted(values)
    return {
        'samples': len(ordered),
        'p50_ms': round(statistics.median(ordered), 3),
        'p95_ms': round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))], 3),
        'max_ms': round(max(ordered), 3),
    }


def wait_for_preview(workspace, width, timeout):
    started = time.perf_counter()
    latencies = []
    composition = []
    while (workspace.preview.generation != workspace.doc.generation or
           workspace.preview.width != workspace.preview_width):
        sample = time.perf_counter()
        workspace.handle_key('right', 'input.right')
        workspace.tick_once()
        workspace.frame(width, 24)
        latencies.append((time.perf_counter() - sample) * 1000)
        composition.append(workspace.frame_ms[-1])
        if time.perf_counter() - started > timeout:
            raise RuntimeError('Preview probe exceeded its timeout')
        time.sleep(.01)
    return {
        'total_seconds': round(time.perf_counter() - started, 3),
        'input_to_composed_frame': percentiles(latencies),
        'composition_only': percentiles(composition),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lines', type=int, default=100000)
    parser.add_argument('--timeout', type=float, default=180)
    options = parser.parse_args()
    if not 4 <= options.lines <= 500000:
        parser.error('--lines must be between 4 and 500000')
    text = ''.join(
        f'## Section {index}\n\nA paragraph with **bold text**, [link](#section-0), and ordinary words.\n\n'
        for index in range(options.lines // 4))
    source_sha256 = hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='mdbm-latency-') as temporary:
        root = Path(temporary)
        config = m.load_config(root / 'mdbm.conf', raw={
            'schema': 'mdbm-config/1.0',
            'application': {'root': str(root)},
            'appearance': {'theme': 'minimal', 'motion': 'off'},
        })
        workspace = m.Workspace(config, restore=False, persist=False)
        workspace.set_document(m.DocumentModel(text))
        workspace.frame(80, 24)  # Prime initial composition outside the input probe.
        workspace.worker = m.PreviewWorker()
        workspace.search_worker = m.DocumentSearchWorker()
        try:
            started = time.perf_counter()
            workspace._request_preview()
            request_ms = (time.perf_counter() - started) * 1000
            initial = wait_for_preview(workspace, 80, options.timeout)
            initial.update(
                request_ms=round(request_ms, 3),
                parse_seconds=round(workspace.worker.last_parse_seconds, 3),
                layout_seconds=round(workspace.worker.last_layout_seconds, 3))
            workspace.frame(100, 24)  # Resolve the new width before enqueueing.
            started = time.perf_counter()
            workspace._request_preview()
            request_ms = (time.perf_counter() - started) * 1000
            reflow = wait_for_preview(workspace, 100, options.timeout)
            reflow.update(request_ms=round(request_ms, 3),
                          layout_seconds=round(workspace.worker.last_layout_seconds, 3))
            for _ in range(7):
                workspace._append_document(m.DocumentModel(text))
            started = time.perf_counter()
            workspace.search_documents('absent-search-marker')
            search_request_ms = (time.perf_counter() - started) * 1000
            while workspace._document_search:
                workspace._poll_document_search()
                if time.perf_counter() - started > options.timeout:
                    raise RuntimeError('Search probe exceeded its timeout')
                time.sleep(.001)
            print(json.dumps({
                'schema': 'mdbm-responsiveness/1.0',
                'version': m.VERSION,
                'source_sha256': source_sha256,
                'python': platform.python_version(),
                'platform': platform.platform(),
                'measurement': 'input handler to composed cells; excludes physical terminal presentation',
                'source_lines': text.count('\n') + 1,
                'source_utf8_bytes': len(text.encode('utf-8')),
                'initial': initial,
                'width_only_reflow': reflow,
                'parser_calls_across_initial_and_reflow': workspace.worker.parse_count,
                'open_buffer_search': {
                    'documents': len(workspace.documents),
                    'aggregate_source_utf8_bytes': len(text.encode('utf-8')) * len(workspace.documents),
                    'request_ms': round(search_request_ms, 3),
                    'total_seconds': round(time.perf_counter() - started, 3),
                },
            }, indent=2))
        finally:
            workspace.worker.close()
            workspace.search_worker.close()


if __name__ == '__main__':
    main()
