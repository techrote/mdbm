# Technical design

Status: implementation-ready architecture; consult the implementation report for measured coverage.

`mdbm.py` is the sole runtime Python source. Tests and documentation are separate. Internal sections own bootstrap, immutable records, config/persistence/path policy, theme compilation, Markdown/source mapping, text editing, commands/state reduction, geometry, cell composition, overlays/input, async orchestration and CLI.

## Event and state flow

`terminal event → normalized input → hit/focus or key lookup → command registry → state mutation/reducer → geometry → composed cell buffer → prompt_toolkit screen diff → terminal`

The application owns one custom UIControl and one unscrolled Window. It supplies styled text runs from its composed cell grid. prompt_toolkit owns terminal initialization, mouse decoding, cursor placement, alternate-screen restoration and the physical screen diff; MDBM owns cell layout, cell safety, protection planes, the logical damage comparison and all hit rectangles. This avoids two competing terminal renderers.

Frozen dataclasses describe rectangles, layout settings, cells, hits, theme programs, parse results and commands. The controller is the only owner of mutable runtime state. Document editing uses a text string, line-start index, cursor and optional exclusive-end anchor. Undo/redo stores bounded replacement deltas. Glyph conversion retains source offsets, so tabs, combining marks and double-width characters cannot silently offset clicks. Unsupported terminal emoji sequences degrade to a safe cell while their original source remains intact.

## Rendering

The geometry solver reserves the ribbon and status strip first, computes operational minimum dimensions, then allocates user artspace and theme decoration in priority order. If split minimums fail, a compact single-tile viewport is used. Structural dividers are always exactly one cell. Tile title controls have host-owned full-width targets.

Canvas cells have glyph, style, continuation and protection metadata. Wide cells are clipped atomically, never split. Decorative layer composition supports replace, underlay, glyph-only, foreground-only, background-only, mask, transparent-cell and dither-blend. Paint calls are clipped and budgeted. Host content and protected controls are painted after decoration. The host style resolver enforces accessibility and quantizes colours deterministically. Logical cell damage is measured, while prompt_toolkit performs the terminal-level diff.

A frame's hit map is rebuilt from final rectangles and includes z-order. Overlays capture input; an outside click closes only the top overlay and is consumed. Divider and editor drags use explicit capture records, so focus changes and Escape can cancel correctly. Menus keep independent hover and keyboard-selection fields.

## Background work

A preview worker handles parsing and layout. Debounce and document
identity, generation, width and ASCII-layout policy prevent stale publication.
A changed document replaces the pending request instead of accumulating jobs.
An old request marker is not proof of completion: activation reuses a matching
completed preview or resubmits missing work. Inactive results populate only
their owning document's cache. Recovery must invalidate earlier model
generations before a pending worker can publish into a replacement buffer.
Only immutable text snapshots cross the worker boundary; themes never see
them. Preview rows carry source ranges and optional safe-link metadata.
Heading navigation and edit-to-view reveal use source maps.

Parsed Markdown is independent of width and ASCII wrapping. The controller
passes its matching immutable parse into width-only reflow; the worker also
keeps an LRU capped at four document parses and eight Mi source characters.
There is one running job, one replaceable pending job and one latest result.
Superseded layout and post-parser work check cancellation, with cooperative
slices during long layout loops. The Markdown library's parse call still
processes the full source. Printable ASCII wrapping uses a cell-equivalent
fast path; Unicode, control and grapheme handling retain the full safety path.

Fragment navigation records source object, generation, root, caret/selection,
focus and overlay ownership, then waits for a matching preview. Open-buffer
search uses a separate replaceable worker with 64 Ki-character scan chunks,
immutable text/line-index references and at most 200 matches. Closing or
superseding the search prevents late publication. Results revalidate their
document object, generation and source range before navigation.

A serial disk worker has at most 72 accepted outstanding jobs, including
running work and unpolled completions. File opens construct models off-thread;
only the latest unchanged navigation intent may activate the completed tab.
Saves capture source, path, expected fingerprint and newline/BOM metadata.
Completion acknowledges the saved-text digest on the originating model, so
typing during a save leaves newer edits dirty. File destinations and original
source paths stay reserved until completion. Guarded continuations cannot
discard newer text. Session/recovery jobs coalesce pending snapshots, moving
replacements to the queue tail to preserve ordering around transactions.
The final shutdown clear follows accepted work; ownership is released only
after the worker drains. All mutable UI state remains controller-owned.

A host animation tick is bounded by configuration and capability/accessibility policy. Spatial/shape tracks may sample at the bounded theme cadence, while style/palette/visibility tracks use the stricter anti-flash transition cap. Script subprocesses use asyncio streams with output truncation and explicit cancellation. Recovery writes are atomic and scheduled after idle edits; save and dirty guards run in the controller so state cannot report saved before disk publication succeeds.

## Persistence

`mdbm.conf` is read-only during normal operation. Dynamic settings live in `.mdbm/session.json`, recovery in a separate bounded snapshot. File publication uses same-directory temporaries, flush/fsync and replace, with exclusive creation for new paths. Original content fingerprints detect conflicts. Restored source retains its original fingerprint. Serialized fields are individually validated before application.

Session schema 2.0 stores the open-document set plus validated committed
appearance preferences and custom workspace presets. Recovery storage schema
3.0 separates a bounded manifest from immutable SHA-256 UTF-8 blobs; schema
1/2 recovery remains readable through a bounded streaming envelope. Writer
and reader share source/metadata limits, and publication accounts for both
manifests and blobs against a pool quota. No retained snapshot is evicted to
make room. See [the storage contract](RECOVERY_STORAGE.md) for exact budgets.

A new primary owner archives the old recovery slot before any fresh write.
Scoped restore persists residual records before consuming the source bundle;
consumption follows successful current-snapshot publication. Recovery change
signatures exclude timestamps and source text, using existing content digests
and metadata instead. The inventory pages every archive by publication time
without materializing source text; selected recovery is loaded on demand.

Persistent OS-held guards serialize primary ownership and recovery publication.
The guard inode is never unlinked, preventing simultaneous stale reclaimers
from removing a successor's lock. Per-instance leases protect live secondary
snapshots. Legacy owner checks retain unknown owners; the Windows probe uses
a SYNCHRONIZE-only handle and zero-time wait without console signalling.

The CLI supports path, config, theme, safe-theme, validate-theme, no-restore and diagnostics. Additional deterministic snapshot and benchmark hooks are development-only CLI conveniences documented in README. Dependency failures print a sanitized installation hint outside full-screen mode. UI exceptions unwind the prompt_toolkit application and attempt recovery without printing raw untrusted terminal data.
