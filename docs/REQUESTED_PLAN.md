# MDBM: Visually Rich Markdown Reader/Editor Implementation Plan

## Summary

Build MDBM as a cross-platform terminal Markdown workspace with one runtime source file, `mdbm.py`, plus `mdbm.conf`, `themes/*.theme`, documentation, tests, and runtime recovery state.

The application will use `prompt_toolkit` for terminal input, mouse events, keyboard handling, and screen lifecycle, but MDBM will own a cell-level compositor. This preserves mature terminal interaction while enabling the dense monochrome, neon architectural, layered, patterned, and glitch aesthetics shown in the references.

Themes remain untrusted presentation data. They can create elaborate scenes, multi-cell chrome, artspace, patterns, deterministic procedural effects, and animations, but cannot execute code, inspect documents, bind keys, alter commands, or obscure protected interaction indicators.

## Documentation Deliverables

Create these implementation-ready documents before coding:

- `MDBM_PRODUCT_SPEC.md`: four-tile behavior, layouts, commands, menus, mouse/keyboard contracts, Markdown behavior, file lifecycle, and session restoration.
- `MDBM_TECHNICAL_DESIGN.md`: single-file internal architecture, state flow, rendering, concurrency, source mapping, persistence, and terminal backend.
- `CONFIG_FILE_SPEC.md`: normative TOML schema for `mdbm.conf`, command bindings, layout, appearance, capabilities, scripts, and accessibility.
- `THEME_FILE_SPEC.md`: revise the draft to `md-editor-theme/1.1`, resolve contradictions, and define the compositor DSL and 1.0 compatibility behavior.
- `SECURITY_MODEL.md`: trust boundaries for themes, config, documents, paths, links, scripts, recovery data, and terminal output.
- `TEST_AND_ACCEPTANCE_PLAN.md`: automated, fuzz, golden-render, PTY, interaction, performance, accessibility, and manual terminal tests.
- `ROADMAP.md`: phased implementation, stretch goals, and explicitly deferred features.
- `IMPLEMENTATION_PROMPT.md`: self-contained build prompt with document precedence, milestones, constraints, verification commands, and definition of done.

Document precedence will be: security → product behavior → config/theme schemas → technical design → tests → roadmap. The implementation prompt must never authorize weakening a higher-precedence document.

## Product and Interaction Contract

### Workspace layout

- Model four persistent tile types: `Filesystem`, `Contents`, `Edit`, and `View`.
- The left column shows either Filesystem or Contents. A border button, menu command, and keyboard command switch them without losing either tile’s selection or scroll state.
- The right column starts with View above Edit. Either may be hidden, allowing the other to fill the column; a host-owned border button swaps their vertical positions.
- Either column may be hidden. If all tiles are hidden, retain a host-owned restore strip, `Tile` menu, command-palette entry, and global keyboard shortcut.
- Startup defaults:
  - Filesystem visible on the left.
  - View above Edit on the right.
  - Left/right split approximately 30/70.
  - View/Edit split 50/50.
  - Four cells of outer user artspace on every edge.
- Structural split handles remain exactly one cell wide/high. Decorative borders and artspace may visually extend around them but cannot change their hit geometry.
- At very small sizes, decoration shrinks before content. If the normal split cannot satisfy minimum content dimensions, show one focused tile at a time with direct tile switching.

### Theme geometry and user artspace

For every outer margin, tile border band, divider band, and spacer:

`effective size = min(theme request + user extra, user maximum, space remaining after minimum content)`

- Shrink user-added artspace first, followed by theme decoration.
- Never shrink protected controls or content below their minimum usable dimensions.
- A theme may provide repeating patterns for additional user artspace.
- Layout visibility, tile ordering, and split ratios remain operational settings, never theme decisions.
- Immersive behind-content art requires an explicit user setting. Cursor, selection, focus, menus, alerts, title controls, and destructive confirmations remain protected even in immersive mode.

### Perfect mouse behavior

- Build a per-frame hit map from final layout geometry; themes never create or modify hit targets.
- Every visible control receives its full visible cell area as a direct target.
- Clicking an unfocused tile both focuses it and completes the pointed action.
- Support precise caret placement, drag selection, word/line multi-click selection, wheel scrolling, link activation, row selection, title controls, and nested menus.
- Right-click inside an editor selection preserves it; right-click outside places the caret before opening the menu.
- Border single-click focuses; border double-click and right-click open the compact tile menu.
- Divider dragging uses pointer capture, live preview, clamped bounds, cancellation rollback, and one committed resize action on release. Left and right drag both work when initiated on a divider.
- Hover cannot move keyboard selection. Submenus use stable open/close delays and inward placement near terminal edges.
- Clicking outside dismisses transient overlays without changing underlying caret, selection, scroll, or focus.
- Pointer-shape changes are capability-dependent refinements, not a portability requirement.

### Flawless keyboard behavior

- All actions route through a central command registry shared by mouse handlers, menus, and key bindings.
- Every pointer action has a keyboard path; automated parity checks fail if an actionable hit target lacks one.
- Standard profile: arrows, Home/End, Page keys, Tab/Shift-Tab focus traversal, Ctrl editing shortcuts, Enter activation, Escape dismissal/backtracking, and discoverable menu mnemonics.
- Optional Vim profile: documented Normal, Insert, and Visual navigation/editing behavior without changing command semantics.
- Alt uses hold-to-show where detectable. Otherwise Alt or F10 latches the ribbon until activation, Escape, or focus return.
- Escape unwinds exactly one transient layer at a time and never strands focus.
- Conflicting or unreachable configured bindings produce diagnostics and retain an unthemed emergency command path.

### Tile responsibilities

- Filesystem:
  - Rooted, expandable tree with whole-row pointer targets.
  - Open, create, save/save-as, rename, move, and trash operations.
  - Atomic saves, dirty-state guards, overwrite/conflict prompts, symlink/root-boundary checks, and trash rather than permanent deletion where supported.
- Contents:
  - Live heading hierarchy generated from the current Markdown parse.
  - Direct heading navigation, keyboard tree navigation, filtering, and remembered row/scroll state.
- Edit:
  - Unicode-aware caret and selection, undo/redo, search, replace, clipboard operations, source diagnostics, and standard/Vim profiles.
- View:
  - CommonMark plus tables, task lists, strikethrough, autolinks, footnotes, fenced-code highlighting, and escaped non-executable HTML.
  - Clickable internal headings, safe local-file links, and confirmed external links.
- Maintain a bidirectional block source map: edit navigation can reveal the corresponding preview block, and clicking preview content focuses the nearest source range without discarding an existing selection unexpectedly.
- Parsing and preview rendering are debounced and generation-tagged so stale background results cannot replace newer text.

### Menus, themes, scripts, and restoration

- Provide the Alt/F10 ribbon headings from the reference spec and compact pointer-anchored context menus.
- Theme selection opens a reversible chooser: keyboard highlight or deliberate pointer selection previews a validated theme; Enter/click commits and persists it; Escape restores the prior theme.
- Discover canonical TOML themes from `themes/*.theme`; select by stable theme ID rather than path.
- Scripts menu lists only sibling scripts explicitly allowlisted in `mdbm.conf`; always exclude `mdbm.py`.
- Collect script arguments as an unambiguous argument list, then run `[sys.executable, script, *args]` with `shell=False`. Capture output in a cancellable overlay. Themes and visual cues can never run scripts.
- Keep manually edited preferences in `mdbm.conf`; store dynamic session state atomically in a local `.mdbm/` state directory so config comments are not rewritten.
- Restore root, active file, theme, tile visibility/order, split ratios, positions, and safe recovery snapshots. Dirty exit offers Save, Discard, and Cancel.

## Public Interfaces, Theme System, and Safety

### Runtime and CLI

Use Python 3.11+ and one runtime source file. Tests remain under `tests/`; packaging metadata is allowed in `pyproject.toml`. Remove the obsolete empty Python stubs when implementation begins.

Dependencies:

- `prompt-toolkit>=3.0.53,<4` for portable full-screen terminal I/O and events.
- `markdown-it-py>=4.2,<5` and `mdit-py-plugins>=0.6.1,<0.7` for CommonMark and selected extensions.
- `Pygments`, `wcwidth`, and `Send2Trash>=2.1,<3`.

Expose:

- `python mdbm.py [path]`
- `--config PATH`
- `--theme ID`
- `--safe-theme`
- `--validate-theme PATH`
- `--no-restore`
- `--diagnostics`

### Configuration schema

Define `schema = "mdbm-config/1.0"` with closed sections for:

- application/workspace paths;
- tile visibility, ordering, split ratios, and minimum sizes;
- user artspace additions and maxima;
- standard or Vim input profile and command-ID bindings;
- selected theme, theme directories, immersive mode, seed override, and motion intensity;
- terminal capability overrides;
- accessibility policies;
- script allowlist;
- session/recovery timing.

Precedence: built-in safe defaults → `mdbm.conf` → explicit CLI overrides → current-session changes. Invalid operational values fall back individually; an unreadable config starts with safe defaults and a diagnostic.

### Theme schema 1.1

Retain the `md-editor-theme` family and add an explicit 1.0 compatibility compiler. Canonical new themes use `schema = "md-editor-theme/1.1"` and the `.theme` suffix.

Resolve the draft’s contradictions by:

- Removing theme scripts and raw shortcut handling.
- Replacing them with typed, one-way visual cues.
- Publishing an exact namespace/reference registry.
- Separating object references, variable substitution, bindings, and rule-target grammar.
- Making rules structured and incapable of showing, hiding, moving, or disabling functional regions.
- Defining deterministic inheritance, overlay, fallback, state precedence, and failure isolation.
- Rejecting unsafe 1.0 constructs while mapping valid legacy panels, art, textures, and animations into the 1.1 compiled model.

Add these safe visual capabilities:

- Semantic components and parts for every tile, ribbon, menu, item, divider, title button, status area, dialog, notification, pointer feedback, editor role, and preview role.
- Orthogonal states including focused, active, previous, hover, pressed, selected, disabled, dirty, caution, destructive, busy, success, warning, and error.
- Multi-cell chrome bands, open/broken/nested borders, title/footer slots, gutters, corner ornaments, shadows, and repeating artspace.
- Palette ramps, gradients, deterministic terminal quantization, foreground/background styling, and dithering.
- Glyph families with explicit transform maps; safe mirror/rotation never guesses Unicode equivalents.
- Token-grid and bounded RLE art, multi-row sprites, reusable motifs, rails, ladders, stepped paths, grids, brackets, connectors, and repeated modules.
- Built-in patterns: tile, stripe, checker, hatch, grid, scanline, dither, scatter, seeded noise, vignette, bands, dropout, slice displacement, fragmentation, and colour-channel offset.
- Masks and protected planes; layers can target only declared decorative slots.
- Compositing modes limited to replace, underlay, glyph-only, foreground-only, background-only, mask, transparent-cell, and deterministic dither blend.
- Typed animation tracks for glyph frame, style, palette phase, pattern phase, offset, density, mask threshold, and visibility.
- Ambient animation plus finite event bursts, with off/reduced/normal/high user intensity.
- Deterministic seed modes: fixed, per-session, and continuously phased. Config may force fixed output.
- Responsive variants by viewport, colour depth, Unicode support, accessibility profile, and motion policy.
- Required ASCII, monochrome, narrow-terminal, and reduced-motion fallbacks for portable themes.

Host cues include startup, focus change, tile show/hide/swap, file activation, dirty-state change, save success/failure, mode change, menu open/close, no-context-actions feedback, and a bounded set of user cue slots. Config binds keys to cue commands; themes only paint them.

### Security and resource envelope

- Themes cannot execute code, access files/network/environment/clipboard, emit ANSI, inspect document text or paths, create commands, or receive raw key data.
- Treat theme, Markdown content, filenames, link labels, and terminal-facing metadata as untrusted display input.
- Reject control characters, ESC/CSI/OSC/DCS/APC/PM, bidi overrides, invalid graphemes, unsafe widths, and output outside host-owned surfaces.
- Render cursor, focus, selection, menus, alerts, confirmations, and recovery controls on an unoccludable host plane.
- Enforce at least:
  - 1 MiB theme file;
  - 4,096 total named objects;
  - reference depth 16 and inheritance depth 8;
  - 16 KiB maximum string;
  - 64 layers per scene;
  - 256×256 maximum art extent and 262,144 decoded art cells per theme;
  - 256 frames per animation;
  - 32 simultaneous animations;
  - 12 FPS default and 30 FPS hard ceiling;
  - viewport-clipped generators and a bounded per-frame paint-operation budget.
- Cap high-contrast flashes below three per second, prohibit rapid full-screen inversion, pause invisible/unfocused animation, and let host accessibility settings override every theme.
- Invalid themes switch atomically to the last known-good theme or the embedded minimal theme.

## Architecture and Implementation Sequence

Organize `mdbm.py` into clearly delimited internal layers despite remaining one file:

1. CLI/bootstrap and crash-safe terminal restoration.
2. Immutable enums/dataclasses for app, document, tile, layout, command, event, hit target, cell, scene, theme, cue, and diagnostics.
3. Config, session, recovery, and path-safety services.
4. Theme parser, validator, compatibility normalizer, compiler, and cache.
5. Markdown parser, heading index, source map, preview layout, and syntax styling.
6. Command registry and atomic state reducer.
7. Geometry solver and per-frame hit-map construction.
8. Cell canvas, layer compositor, protected planes, and diff renderer.
9. Mouse capture, keyboard profiles, menus, overlays, and ribbon controller.
10. Async file parsing, filesystem operations, script subprocesses, and application controller.

Normative event flow:

`terminal event → normalized input → hit-test/focus or key lookup → command → atomic state update → layout → scene composition → cell diff → terminal output`

Implementation milestones:

1. Finalize all documents, schemas, examples, conformance fixtures, and the implementation prompt.
2. Establish Python 3.11 tooling, dependencies, `mdbm.py` bootstrap, terminal cleanup, diagnostics, and test harness.
3. Implement config/session models, command registry, focus model, and deterministic state transitions.
4. Implement the cell canvas, Unicode width handling, clipping, diff rendering, hit map, and minimal embedded theme.
5. Implement theme 1.0 normalization and the complete safe 1.1 compiler, followed by three reference themes: restrained monochrome, neon circuitry, and dense glitch mosaic.
6. Implement two-column layout, switching/hiding/swapping, artspace sizing, resize capture, border controls, and responsive compact mode.
7. Implement editor buffer, Filesystem and Contents tiles, Markdown parser, View renderer, and bidirectional source mapping.
8. Implement menus, context rules, ribbon behavior, theme chooser, configured scripts, file mutations, and recovery.
9. Complete accessibility, capability degradation, performance budgets, fault isolation, documentation examples, and release packaging.

## Test Plan and Definition of Done

- Unit-test parsing, state transitions, command dispatch, layout math, source maps, file operations, recovery, and each theme object type.
- Property/fuzz-test TOML nesting, cycles, hostile Unicode, massive repeats, malformed RLE, overflow geometry, rule expansion, and control-sequence injection.
- Golden-test composed cell buffers at 40×12, 80×24, 120×40, and 200×60 under truecolour, ANSI-256, ANSI-16, monochrome, Unicode, ASCII, reduced-motion, and immersive-mode profiles.
- Simulate every mouse sequence: focus-and-land click, selection preservation, multi-click, drag/autoscroll, right-click menus, title menus, nested menu grace, split cancellation, and edge-clamped overlays.
- Generate a mouse/keyboard parity matrix from the command registry; fail tests for pointer-only commands, unreachable controls, or focus traps.
- Test every visibility/order combination, including hidden columns and empty-canvas recovery.
- Verify tile-local caret, selection, scroll, and row memories survive focus changes, menus, hiding, swapping, and session restoration.
- Test atomic save, conflict detection, trash failures, symlink boundaries, crash recovery, stale parse suppression, script cancellation, and dirty exit.
- Test malicious themes cannot emit terminal controls, cover protected cells, trigger commands/scripts, exceed budgets, or break editor operation.
- Performance acceptance on the reference machine: p95 input-to-frame below 50 ms at 160×50, stable drag feedback, bounded memory for 100,000-line documents, and no full redraw when a cell-local update suffices.
- Manually verify Windows Terminal plus representative Linux/macOS VT terminals, including mouse press/release/drag, Alt fallback, wide glyphs, resize, focus reporting, and terminal restoration after exceptions.
- Done means all four tiles and their direct interactions work, every mouse action has keyboard parity, the three aesthetic reference classes are demonstrably expressible, invalid themes fail safely, and `mdbm.py` remains the sole runtime Python source.

## Stretch Goals and Future Features

- Kitty, iTerm2, and Sixel image backends behind explicit capabilities and permissions.
- Interactive theme inspector, hot reload, resolved-scene viewer, visual budget meter, and animation timeline debugger.
- Signed theme packages, metadata previews, and a safe theme gallery.
- Advanced search, backlinks, document tabs, workspace-wide index, Git diff indicators, and export.
- Terminal-supported pointer shapes, richer clipboard adapters, and synchronized-update protocols.
- Additional host-approved layout presets without granting arbitrary functional layout control to themes.
- Screen-reader-specific rendering, higher-contrast generated variants, and expanded accessibility audits.

Assumptions: v1 is cell-native; Textual is not used; themes never contain executable code; tests and documentation may use separate files; runtime-generated `.mdbm/` state is not application source; and the current empty scaffold carries no compatibility obligations beyond the documented theme 1.0 reader.
