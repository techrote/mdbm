# Test and acceptance plan

Status: contract; `EXTENSION_PASS_2026-11-03.md` lists current verification,
and `IMPLEMENTATION_REPORT.md` retains the original measurements.

Run `python -m pytest -q`. Tests cover strict theme/config validation, hostile Unicode, bounds, inheritance/reference cycles, RLE expansion, all patterns, masks and layer modes; document edit deltas and grapheme navigation; source mapping; atomic save, conflict and symlink guards; recovery; geometry; command parity; pointer capture/rollback; overlay click-through; theme preview rollback; and safe script arguments/cancellation.

Deterministic randomized tests mutate nested TOML-like objects, strings, geometry, visibility/order combinations and edit sequences. They use seeded standard-library randomness so they remain runnable without a property-testing framework. Golden cell hashes cover 40×12, 80×24, 120×40 and 200×60 with three reference themes, colour-depth modes, ASCII/Unicode, reduced motion and immersion. Snapshots are cell buffers, not screenshots of an unrelated mock-up.

PTY acceptance launches the actual application in an alternate screen, sends real keys and mouse reports, edits/saves a file, tests dirty-exit cancellation and verifies exit status plus restored terminal echo. The POSIX PTY tests are skipped on Windows; they can run on macOS but do not establish native Terminal.app or iTerm2 behavior. A separate platform matrix is recorded in the release report.

PTY visible-text assertions use a reconstructed VT screen because a physical
diff may skip characters already present on screen. Raw EOF and terminal-mode
cleanup assertions remain separate. The multi-document journey covers Ctrl+B,
searching another open buffer, Save All, and exact saved file contents.

Recovery regressions use actual persisted-session startup and explicit-file
startup, Later/Discard/Restore, sibling retention, current dirty conflicts,
invalid later records, capacity and identity collisions. Worker regressions
control pending requests and completion order, including inactive results,
overwritten slots, document replacement and ASCII-policy changes. State-lock
tests use a real live child PID and its subsequent exit on Linux and Windows.

The continuation adds schema3 and legacy32-document roundtrips, UTF-8/escaping
and metadata limits, quota failure retention, corruption handling, more than
64 archive discovery, bounded paging, live secondary ownership and true
simultaneous-process stale reclamation/crash cases. Recovery inventory actions
must retain the source archive on read/validation/publication failure, late
shutdown continuation and cancelled/superseded modal reads.

Background I/O regressions hold real disk jobs at explicit barriers while the
controller edits and composes frames. They verify immutable save baselines,
destination reservations, conflict confirmation, latest navigation ownership,
coalescing order, queue capacity and the final shutdown clear. Preview/search
regressions control request/completion interleavings and verify width-only parse
reuse, Unicode/control wrapping equivalence, cancellable chunked search and
fragment ownership across focus, document, generation and modal changes.

Workspace and appearance tests cover preview/apply/cancel, committed-only
session persistence, custom preset validation/capacity, exact maximize/restore,
keyboard/pointer routes, and usable scrolling/hit rows down to 20×6. Quiet Study
is checked across the representative sizes and colour/accessibility profiles.
The existing 96 golden baselines remain unchanged.

Permanent CI runs Ubuntu Python 3.11/3.13 and Windows Python 3.13 without
filtering the Windows suite. Every job verifies release Markdown plugins and
installs the built wheel into fresh target and prefix directories outside the
source tree, then compiles all packaged themes and exercises the console entry
point. Only platform-specific PTY tests are skipped on Windows.

Performance probes report distributions at 160×50, not a single best sample. Measure composition and end-to-end terminal latency separately: a fast composition result does not establish p95 input-to-frame <50 ms. Exercise a 100000-line source, bounded undo, preview work queue and script output. Verify unchanged frames have zero logical damage and local caret changes do not force an application-wide terminal clear.

`python tests/measure_responsiveness.py --lines 100000` separately reports
initial parse/layout and width-only reflow, parser invocation count, request
latency, input-handler-to-composed-frame distributions, composition-only
distributions and an eight-buffer search. Its JSON includes the runtime source
digest. It excludes physical terminal presentation; record maximum observations
as well as p50/p95, and never treat it as native reference-machine acceptance.

Manual checks still required on Windows Terminal, Linux VTE/xterm and macOS Terminal/iTerm2: all button releases, both divider buttons, Alt latching, emoji/CJK widths, native clipboard behavior, resize while dragging, terminal focus events, Ctrl+S/XON-XOFF handling, crash restoration, Send2Trash integration, filesystem permissions/junctions and process cancellation. Reference-machine timing and accessibility review are release gates, not assertions inferred from passing unit tests.
