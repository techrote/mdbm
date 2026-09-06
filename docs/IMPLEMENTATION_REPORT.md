# MDBM 1.0.0a2 — implementation and verification report

## Release decision

**Runnable implementation alpha; full requested-plan acceptance is not claimed.** The delivered source implements the four-tile workspace and its central interaction, file, recovery, Markdown, theme and script paths. It is not an empty scaffold or an implementation prompt standing in for an application.

The sole runtime Python source is `mdbm.py`. Python under `tests/` is verification/development tooling, not application runtime. Configuration, themes, examples, documentation, launchers, test fixtures and generated recovery are separate data/support files.

This report distinguishes implementation from verification and from remaining scope. It does not weaken the higher-precedence security/product contracts. The original input is preserved in `REQUESTED_PLAN.md`. No pre-existing repository, old theme draft, reference-image set, obsolete stub files, or separate ribbon specification accompanied that input. Accordingly, the published 1.0 theme compatibility reader is an explicitly documented subset; the ribbon is File / Edit / Tile / Navigate / Theme / Scripts / Help.

## Delivered behavior

| Area | Implemented in this alpha | Qualification |
| --- | --- | --- |
| Workspace | Four persistent tile identities; Filesystem/Contents switching; View/Edit show, hide and swap; column toggles; focused compact mode; protected restore routes | Full native-terminal interaction matrix remains open |
| Layout | 30/70 and 50/50 defaults; four-cell user artspace; shrink-before-content priorities; exact one-cell structural split targets; clamped left/right drag capture, commit and Escape rollback | Decorative layout is slot-based rather than arbitrary scene layout |
| Editing | Unicode-aware cell mapping; grapheme navigation/deletion; caret placement; selection, word/line multi-click, drag autoscroll; undo/redo; find/replace; go-to; application clipboard; Standard and focused Vim modes | Not full Vim; no native clipboard bridge; ambiguous emoji sequences degrade safely |
| Filesystem | Rooted tree, whole-row selection, expansion, new/save/save-as, directory creation, rename/move, system trash, conflict guards | Filesystem operations are synchronous; native Windows junction/permission behavior needs testing |
| Markdown | CommonMark parser, tables, tasks, strike, HTTP(S) autolinks, footnotes, highlighted fences, escaped HTML; heading hierarchy and block source maps | Local execution used bootstrap task/footnote rendering; official plugin path not locally verified; not a full GFM autolink suite |
| Source synchronization | One replaceable pending parse, generation/width tags, edit-to-preview reveal, preview-to-source reveal, selection preservation | Full-document parsing; no incremental parser; stale Contents and preview actions are temporarily held |
| Commands and menus | 114 registered command IDs; palette, ribbon, context menus, nested submenu delays and inward placement; modal outside-click consumption; registry parity checks | Parity metadata is not proof that every terminal exposes every key combination |
| Themes | Strict TOML compiler, closed shapes, references/inheritance, fallback profiles, patterns, token-grid/RLE art, motifs, masks, compositing, typed animation/rule properties, three original reference aesthetics plus three animated examples, safe embedded theme | The requested fine-grained semantic part/state vocabulary and some advanced decorative facilities remain partial |
| Theme transactions | Validate before preview; keyboard/deliberate-click preview; commit; Escape rollback; committed theme ID separated from preview state | A runtime painter fault falls back to Minimal |
| Persistence | Atomic session/recovery state; manually maintained config untouched; per-instance state lock; isolated concurrent recovery; snapshots retain original fingerprints | Recovery is local plaintext; same-account adversarial filesystem races are not an isolation boundary |
| Scripts | Explicit sibling allowlist; excludes the application; JSON argument arrays; exec-style subprocess launch; bounded sanitized output; cancellation and exit status | Trusted scripts, not a sandbox; Windows descendant-process cancellation is incomplete |
| Terminal backend | Custom UIControl and owned cell grid; PTK terminal lifecycle/input/mouse/physical diff; ASCII/colour-depth degradation; focus protocol; cleanup on normal/error paths | Tested through POSIX PTYs, not the requested set of native terminals |

## Theme safety and remaining theme scope

The compiler enforces the supplied numeric limits: 1 MiB theme input, 4,096 named/expanded entries, depth 16 and inheritance depth 8, 16 KiB strings, 64 scene layers, 256×256 art extents, 262,144 decoded art cells, 256 animation frames, 32 tracks and a 30 FPS ceiling. Generator evaluations are charged even when masks/offsets would skip output. Opaque normal content and host-protected controls sit above decorative layers. Invalid themes do not gain access to command callbacks, document text, paths, raw keys, clipboard or subprocess APIs.

The delivered compiler is not the entirety of the proposed future theme vocabulary. In particular:

- Styles cover coarse semantic roles; menus, confirmation controls, selection and cursor use host-safe styling rather than a fully themeable component/part hierarchy. State rules currently receive active/focused/dirty, not every declared orthogonal state.
- There is no general variable-substitution language, arbitrary title/footer-slot engine or complete open/broken/nested border authoring system. Palette ramps and deterministic patterns exist; general spatial gradients are not a separate complete DSL.
- Explicit glyph transform maps work, but rotated non-square art is not a full geometry-transform system. Responsive conditions use the documented alpha vocabulary rather than every proposed viewport condition.
- Potentially flash-like `style`, `palette_phase`, and `visibility` tracks are limited to two transitions per second. Spatial/shape animation may use the bounded theme cadence up to 30 FPS, while off/reduced motion suppresses tracks. A rigorous whole-scene flash-rate audit across overlapping state/cue changes has not been performed; that broader safety acceptance gate remains open.

These are remaining requested-plan items, not merely optional image-backend stretch goals. The accepted alpha grammar and its limits are published in `THEME_FILE_SPEC.md`.


## 1.0.0a2 animated-example increment

This revision adds three data-only themes: `maintenance-ecosystem`, `relay-moth-swarm`, and `diagnostic-aurora`, plus `examples/animated-theme-showcase.md` and actual-compositor GIF captures under `docs/screenshots/animated/`. The motion design is derived from the lunar-service maintenance toy vocabulary: pulsing service lamps, cable/crawler movement, relay-moth flutter and formation drift, prismatic coolant, diagnostic aurora bands, and finite arc bursts. No executable theme hooks were added.

The original compositor sampled every animation property at 2 Hz. a2 narrows that restriction to potentially flash-like `style`, `palette_phase`, and `visibility` changes. Spatial/shape properties (`offset`, `pattern_phase`, `density`, `mask_threshold`, sparse `glyph_frame`) can now use the bounded theme cadence up to 30 FPS, while the application/session FPS cap and off/reduced policies still apply. This produces materially smoother crawlers, moths, falling particles and waves without permitting rapid whole-style or visibility flashing. The sample configuration binds **F8** to `cue.user-1` so two demo themes can fire a finite manual arc burst.

## Automated verification

**Final suite: 513 passed, no failures or skips, four test-harness deprecation warnings.** See `verification/pytest.txt` and `verification/junit.xml` for the actual run. The count includes parameterized cases, not 513 independent features.

The suite covers closed config/theme validation, malformed shapes and hostile Unicode, reference cycles and legacy normalization, bounded art and generator work, deterministic pattern output, all visibility/order combinations at multiple sizes, source maps, stale publication/interaction guards, text edit deltas and grapheme behavior, click-to-focus-and-land, selection preservation, multiple-click selection, divider rollback, overlays, theme preview persistence, file conflicts, newline/BOM handling, symlink rejection, recovery integrity, state locking, script arguments/output/cancellation and command/hit metadata parity.

**96 golden configurations** continue to cover the three original reference themes across four sizes (40×12, 80×24, 120×40, 200×60), and eight profiles (truecolour, ANSI-256, ANSI-16, monochrome, ASCII, reduced motion, immersive, high contrast). They hash the actual composed style/text runs after colour quantization. They are regression baselines generated from this implementation, not an independent oracle. The static PNG/HTML gallery now includes all six file-based themes and is generated from the same cell buffers, not a concept mock-up. Three animated GIF captures are also generated from actual successive compositor frames by `tests/render_animation_gallery.py`; they are documentation aids, not browser reimplementations. The animated themes are additionally tested for real frame-to-frame change under normal motion, deterministic freezing under reduced motion, finite cue bursts, and operation budgets.

**Four real POSIX PTY tests** launch the full application and exercise keyboard/bracketed paste, a real SGR mouse click followed by insertion, atomic saving, dirty-exit cancellation/discard, terminal resize/focus reports and clean exit. Cleanup observations include alternate-screen, mouse, bracketed-paste and focus-reporting disable sequences. These tests do not substitute for native Windows Terminal, Terminal.app, iTerm2 or Linux GUI-terminal testing.

Python 3.13's PTY harness warned that `forkpty()` was invoked from a multithreaded parent. All four PTY tests completed and passed. The warning is retained in the logs, not filtered out or represented as an application assertion failure.

### Environment and dependency boundary

| Component | Locally exercised |
| --- | --- |
| Python | 3.13.5 |
| Operating environment | Linux x86-64 container |
| prompt-toolkit | **3.0.52 — below the required 3.0.53 minimum** |
| markdown-it-py | 4.2.0 |
| mdit-py-plugins | **Not installed** |
| Pygments | 2.20.0 |
| wcwidth | 0.7.0 |
| Send2Trash | 2.1.0 |
| regex | 2026.5.9 |

Package-index installation was unavailable in this environment. The declared requirement bounds remain those in the plan, with `regex` added for grapheme segmentation. Missing extensions/older PTK are explicitly diagnosed at runtime. The full suite therefore establishes the bootstrap execution path, **not** validation against all declared release versions. The supplied CI matrix installs strict dependencies and explicitly asserts that the real plugin path is active. That remote CI workflow is supplied but has not been executed here.

## Performance measurements

The reproducible probe and machine-readable output are `tests/performance_probe.py` and `verification/performance.json`. These are measurements of this environment, not estimates for the user's machine.

### Owned-cell composition, 160×50

Fixed source, fixed seed, 5 warm-ups and 60 measured frames per theme. These samples do **not** include keyboard transport, terminal presentation or parser scheduling.

| Theme | Median | p95 | Maximum observed |
| --- | ---: | ---: | ---: |
| Minimal | 11.877 ms | 16.971 ms | 92.715 ms |
| Restrained Monochrome | 17.075 ms | 20.220 ms | 87.470 ms |
| Neon Circuitry | 20.669 ms | 22.231 ms | 90.678 ms |
| Dense Glitch Mosaic | 29.618 ms | 33.996 ms | 95.533 ms |

Identical frames produced zero logical damage in all four cases. The sampled single-character edit changed 18 cells in three themes and 157 in Neon Circuitry, whose dirty-state decorative rule also changes. MDBM currently recomposes the owned grid; PTK diffs the physical screen. Zero logical damage does not imply zero CPU work.

The maxima expose scheduler/GC variability, and p95 composition alone does not establish the requested **p95 input-to-frame below 50 ms**. That end-to-end acceptance gate is unverified, particularly while a Python parsing worker is busy.

### 100,000-line document

One isolated process, 3,600,000 UTF-8 bytes and a final newline (100,001 indexed lines). Line-start storage used 400,004 bytes. Construction took 34.855 ms. Sample insertion timings at the beginning, middle and end were 5.864, 3.726 and 0.426 ms, respectively; these are model edits, not end-to-end terminal input.

Full parse plus 100-column layout took **22,243.429 ms**, producing 36,364 preview rows. Process peak resident memory was **189.707 MiB**, including interpreter and dependencies. The full source remained available. This validates one large-document probe, not all adversarial documents. The single Python worker and bounded pending queue keep work from accumulating, but do not make parsing incremental or immediately cancellable.

## Material fixes found during verification

Tests drove fixes for narrow-table row width, overlay hit-role mismatches, source-generation reuse when switching documents, one-cell gutter/caret agreement, parent-submenu hover lifetime, skipped-generator budget accounting, expanded palette and padded-art accounting, malformed recovery Unicode, custom recovery-directory protection, uncommitted theme persistence, stale modal intentions beneath the global palette, stale preview links/Contents, grapheme interior placement, per-block preview row limits and rename baseline conflict handling. This list records implemented fixes; it does not imply an exhaustive independent security audit.

## Remaining acceptance work

The full requested plan is not done until the release dependency path, native platform matrix, complete semantic theme vocabulary, accessibility/flash checks, and reference-machine end-to-end timing have been verified or implemented as appropriate. Other material limitations are synchronous filesystem/recovery I/O, full-document parsing, the documented Vim/clipboard/legacy subsets, hard-link-dependent no-clobber creation, and lack of a universal Windows process-tree cancellation mechanism.

File operations use checked paths and atomic publication, but cannot promise compare-and-swap isolation against a hostile same-user process replacing directories between checks. Directory move races and platform-specific reparse semantics require further hardening/testing. Actual system-trash integration was not exercised against the host desktop; failure behavior was tested without deleting valuable data.

No executable theme support, automatic scripts, arbitrary terminal escape passthrough, remote Markdown image retrieval, silent conflict overwrite or permanent-delete fallback was introduced to fill a feature gap.

## Packaging verification

A fresh `1.0.0a2` wheel was built and installed into an isolated virtual environment. The installed console entry point reported `MDBM 1.0.0a2`; installed data discovery found all six themes plus the animated showcase; all three new themes validated from the installed `share/mdbm/themes` tree; and an installed `maintenance-ecosystem` snapshot rendered successfully. Because this offline runner exposes its preinstalled dependencies through a nonstandard host path, the smoke test explicitly supplied that host site-packages path; it therefore verifies wheel layout, entry points, package-data discovery and execution, **not** the still-missing strict dependency-version gate. See `verification/wheel-build.txt`, `verification/wheel-install.txt` and `verification/wheel-smoke.txt`. The source ZIP remains the recommended delivery, with Windows and POSIX launchers.

## Reproduction

```sh
python -m pip install -e ".[test]"
python -m pytest -q --junitxml=docs/verification/junit.xml
python mdbm.py --validate-theme themes/restrained-monochrome.theme
python mdbm.py --validate-theme themes/neon-circuitry.theme
python mdbm.py --validate-theme themes/dense-glitch-mosaic.theme
python mdbm.py --validate-theme themes/maintenance-ecosystem.theme
python mdbm.py --validate-theme themes/relay-moth-swarm.theme
python mdbm.py --validate-theme themes/diagnostic-aurora.theme
python mdbm.py --validate-theme tests/fixtures/legacy-safe.theme
python mdbm.py --diagnostics --no-restore
python tests/performance_probe.py --output docs/verification/performance.json
python tests/render_gallery.py
python tests/render_animation_gallery.py
```

The two `tests/fixtures/rejected-*.theme` files must fail validation. `tests/update_goldens.py` deliberately regenerates baseline hashes; visual review is required before accepting such changes. PNG generation in the gallery helper is optional and uses locally available Pillow/fonts; no font files are distributed.
