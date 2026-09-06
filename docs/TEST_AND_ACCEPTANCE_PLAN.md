# Test and acceptance plan

Status: contract; `IMPLEMENTATION_REPORT.md` lists tests actually run.

Run `python -m pytest -q`. Tests cover strict theme/config validation, hostile Unicode, bounds, inheritance/reference cycles, RLE expansion, all patterns, masks and layer modes; document edit deltas and grapheme navigation; source mapping; atomic save, conflict and symlink guards; recovery; geometry; command parity; pointer capture/rollback; overlay click-through; theme preview rollback; and safe script arguments/cancellation.

Deterministic randomized tests mutate nested TOML-like objects, strings, geometry, visibility/order combinations and edit sequences. They use seeded standard-library randomness so they remain runnable without a property-testing framework. Golden cell hashes cover 40×12, 80×24, 120×40 and 200×60 with three reference themes, colour-depth modes, ASCII/Unicode, reduced motion and immersion. Snapshots are cell buffers, not screenshots of an unrelated mock-up.

PTY acceptance launches the actual application in an alternate screen, sends real keys and mouse reports, edits/saves a file, tests dirty-exit cancellation and verifies exit status plus restored terminal echo. The POSIX PTY tests are skipped on Windows; they can run on macOS but do not establish native Terminal.app or iTerm2 behavior. A separate platform matrix is recorded in the release report.

Performance probes report distributions at 160×50, not a single best sample. Measure composition and end-to-end terminal latency separately: a fast composition result does not establish p95 input-to-frame <50 ms. Exercise a 100000-line source, bounded undo, preview work queue and script output. Verify unchanged frames have zero logical damage and local caret changes do not force an application-wide terminal clear.

Manual checks still required on Windows Terminal, Linux VTE/xterm and macOS Terminal/iTerm2: all button releases, both divider buttons, Alt latching, emoji/CJK widths, native clipboard behavior, resize while dragging, terminal focus events, Ctrl+S/XON-XOFF handling, crash restoration, Send2Trash integration, filesystem permissions/junctions and process cancellation. Reference-machine timing and accessibility review are release gates, not assertions inferred from passing unit tests.
