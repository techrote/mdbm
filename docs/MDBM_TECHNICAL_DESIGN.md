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

A single worker handles parsing and preview layout. Debounce and generation/width tags prevent stale publication. A changed document replaces the pending request instead of accumulating jobs. Only immutable text snapshots cross the worker boundary; themes never see them. Preview rows carry source ranges and optional safe-link metadata. Heading navigation and edit-to-view reveal use source maps.

A host animation tick is bounded by configuration and capability/accessibility policy. Spatial/shape tracks may sample at the bounded theme cadence, while style/palette/visibility tracks use the stricter anti-flash transition cap. Script subprocesses use asyncio streams with output truncation and explicit cancellation. Recovery writes are atomic and scheduled after idle edits; save and dirty guards run in the controller so state cannot report saved before disk publication succeeds.

## Persistence

`mdbm.conf` is read-only during normal operation. Dynamic settings live in `.mdbm/session.json`, recovery in a separate bounded snapshot. File publication uses same-directory temporaries, flush/fsync and replace, with exclusive creation for new paths. Original content fingerprints detect conflicts. Restored source retains its original fingerprint. Serialized fields are individually validated before application.

The CLI supports path, config, theme, safe-theme, validate-theme, no-restore and diagnostics. Additional deterministic snapshot and benchmark hooks are development-only CLI conveniences documented in README. Dependency failures print a sanitized installation hint outside full-screen mode. UI exceptions unwind the prompt_toolkit application and attempt recovery without printing raw untrusted terminal data.
