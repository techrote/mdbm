# md-editor-theme/1.1

Status: normative schema for this implementation. A complete runnable example is in each `themes/*.theme` file. All unknown fields fail validation. A failed candidate never partially updates the live theme.

## Root and naming

Required: `schema = "md-editor-theme/1.1"`, `id`, `name`. Optional root fields: `description`, `extends`, `seed`, `seed_mode`, `fps`, `palette`, `ramps`, `styles`, `geometry`, `glyphs`, `art`, `motifs`, `patterns`, `masks`, `layers`, `animations`, `rules`, `variants`, `fallbacks`. IDs match `[a-z][a-z0-9_.-]{0,63}`. Object references use `@namespace.name`; styles and colours accept a bare local name as an ergonomic alias. There is no string interpolation or executable binding language. Typed layer properties are the only animation/rule targets.

Inheritance merges dictionaries recursively, replaces arrays, then validates the resolved object. Maximum eight ancestors; missing IDs and cycles fail. Ancestors come only from the host's discovered theme catalog. Within styles, `extends` names another style; resolution is bounded and cycle-checked. Overlay ordering is base → derived → responsive palette/layer overrides → state rules in declared order → animation sample → accessibility overrides. Later rules win only for the same decorative property.

## Palette and styles

Palette values are `#rrggbb` or a reference to another palette entry. Ramps have `colours` (2–16 colour values), `steps` (2–256); the compiler generates deterministic indexed colours. Styles accept `fg`, `bg`, `bold`, `italic`, `underline`, `dim`, `strike`, and `extends`. Booleans are literal. No raw ANSI, blink, inverse, font escape, named terminal command or prompt_toolkit style string is accepted.

Host semantic roles include background, text, muted, border, title, accent, code, heading, link, quote, table, diagnostic, tree, gutter and status. States are focused, active, previous, hover, pressed, selected, disabled, dirty, caution, destructive, busy, success, warning and error. Selection, cursor, focused outline, menus and confirmations always receive a host-safe protected treatment regardless of styling.

## Geometry and slots

Geometry accepts `outer` [top,right,bottom,left], `border`, `divider`, `spacer`, each bounded. Effective sizes obey host minimum-content limits and user maxima; user additions shrink before theme requests. Themes cannot set tile visibility, ordering or split ratios.

Layers target `outer`, `divider`, `spacer`, `tile.filesystem.chrome`, `tile.contents.chrome`, `tile.edit.chrome`, `tile.view.chrome`, or `behind-content`. The last is ignored without explicit immersive permission. Slots are decorative masks owned by the host. `layer.<id>` is the only rule/track target grammar; a target is not an object reference.

## Object vocabulary

- `glyphs.<id>`: `symbols` list of single-cell graphemes; optional complete `mirror` and `rotate90` maps. Transformations require explicit maps, never guessed Unicode substitutions.
- `art.<id>`: either `rows` of literal glyph strings, or `grid` rows of space-separated token names, or `rle` rows of `count:token` runs. `legend` maps token IDs to literal single-cell glyphs. Optional `style`. Exactly one representation. Decoded dimensions and total cells are bounded before allocation.
- `patterns.<id>`: `kind`, `glyphs`, `style`, `period` [x,y], `density` 0–1, `phase`, `amplitude`. Built-ins: tile, stripe, checker, hatch, grid, scanline, dither, scatter, noise, vignette, bands, dropout, slice-displacement, fragmentation, colour-channel-offset, rails, ladders, stepped-path, brackets, connectors. Evaluation is deterministic and clipped to the slot.
- `motifs.<id>`: an `art` or `pattern` reference, plus optional `gap` [x,y]. This is a reusable repeat module.
- `masks.<id>`: `pattern` reference, `threshold` 0–1 and optional `invert` boolean. Mask objects cannot read content or protected pixels.
- `layers`: array of records with `id`, `slot`, one of `art`/`pattern`/`motif`, `style`, `mode`, `offset` [x,y], `repeat`, `opacity`, `mask`, `visible`, `family`, `transform`. All optional except id, slot and source. Modes: replace, underlay, glyph-only, foreground-only, background-only, mask, transparent-cell, dither-blend. Transform: none/mirror/rotate90, requiring a matching glyph family.
- `rules`: array of `{target, states, cue, set}`. States are checked against host-provided semantic state for the layer's tile; `cue` is an optional typed host cue. Allowed `set` fields: style, opacity, density, offset, visible, phase, mask_threshold, palette_phase. These properties affect decoration only.
- `animations`: array of `{id, target, property, values, duration, loop, cue}`. Properties: glyph_frame, style, palette_phase, pattern_phase, offset, density, mask_threshold, visibility. Values are checked per property; ≤256 frames; ≤32 simultaneously active. Duration 0.5–3600 seconds. Ambient tracks loop; event tracks are finite. Off freezes at the base scene. Reduced motion disables tracks. Potentially flash-like `style`, `palette_phase`, and `visibility` tracks are sampled at ≤2 transitions/second. Spatial/shape tracks (`offset`, `pattern_phase`, `density`, `mask_threshold`, and sparse `glyph_frame`) may sample at the bounded theme cadence, never above 30 FPS; the application/session FPS cap still applies. High motion may raise the spatial sampling target, while reduced/off disables animation. This permits fluid crawlers, moths, rain and waves without allowing rapid whole-style or visibility flashing.

`seed_mode` is fixed/session/phased. Configuration may force a fixed seed. Phase changes are clock-derived, never driven by random global state. Host cues: startup, focus-change, tile-show, tile-hide, tile-swap, file-activate, dirty-change, save-success, save-failure, mode-change, menu-open, menu-close, no-context-actions, and user-1 through user-8. Cues contain no keys, document text, paths or script hooks.

## Responsive variants and fallback

`variants` is an ordered array of `{when, palette, hide_layers, show_layers}`. Conditions: min_width, max_width, colour_depth, unicode, motion, high_contrast. Only declared layers can be enabled or hidden. Every portable theme declares `[fallbacks.ascii]`, `[fallbacks.monochrome]`, `[fallbacks.narrow]`, `[fallbacks.reduced_motion]`. Fallback records accept `glyph`, `hide_layers`, `palette`; reduced motion is also enforced independently by the host. ASCII fallback substitutes non-ASCII decorative glyphs. Narrow fallback applies below 60 columns. Colour quantization uses deterministic Euclidean RGB matching; monochrome emits no colour escape instructions.

## Legacy 1.0 compatibility

The input did not supply the older draft. Consequently this release publishes a strict, testable subset instead of pretending to parse an unknown schema: schema/id/name/description/palette/styles/geometry plus `textures`→patterns, `panels`→layers and `art`/`animations` in the shapes above. `schema = "md-editor-theme/1.0"` compiles through this normalizer. Missing fallback records are supplied conservatively. Any legacy scripts, shortcuts, bindings, commands, expressions, raw ANSI or unknown constructs are rejected. The conformance fixture shows the exact accepted subset. Arbitrary historical 1.0 compatibility is not claimed.
