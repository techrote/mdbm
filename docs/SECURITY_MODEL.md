# Security model

Status: highest-precedence contract.

## Boundaries

Themes are untrusted TOML data. The compiler receives bytes and already-authorized theme objects, never a document, path, key event, environment, clipboard or application command callback. Theme inheritance resolves stable IDs in the host-discovered catalog, not paths or imports. Unknown fields, unsupported schema versions, terminal controls, style injection, invalid references, cycles and over-budget objects fail validation atomically. No eval, exec, import-by-theme, expression interpolation or terminal escape passthrough is permitted.

Only host commands mutate documents, files, focus, layout and processes. Decorative visibility is allowed; functional visibility is not theme-controlled. Themes draw into host-declared decorative slots. Normal content and all protected indicators are composed afterwards. Host menus, alerts, confirmations, selection, cursor and focused borders are never occluded by a theme. Immersive mode permits only behind-content decoration and never changes this precedence.

## Display safety

Every untrusted display string is converted to safe grapheme cells. C0/C1 controls, ESC sequences, bidi controls, surrogate code points and unsafe widths are rejected in themes and visibly replaced when displaying existing source or metadata. Newlines and tabs in source have host-defined geometry, not escape behavior. Theme strings never become prompt_toolkit styles verbatim: validated colours and whitelisted flags are compiled to styles. Clipboard paste is sanitized at insertion. Existing file contents are not silently rewritten just because the renderer suppresses a control character.

## Resource envelope

Theme bytes ≤1 MiB; named objects ≤4096; nesting/reference depth ≤16; inheritance ≤8; each string ≤16 KiB; scene layers ≤64; art extent ≤256×256; total decoded art ≤262144 cells; animation frames ≤256; active tracks ≤32; configured FPS ≤30, default 12. Pattern evaluation is viewport-clipped and charged to a per-frame operation budget. Iteration bounds and finite numbers are validated before expansion. The host can disable all motion. Potentially flash-like whole-style, palette and visibility tracks remain capped at two transitions per second; spatial/shape animation may use the bounded theme cadence up to the 30 FPS ceiling. No inverse/blink style or full-screen inversion exists. Unfocused/invisible tracks pause.

Files and recovery have explicit size limits. Undo uses bounded edit deltas rather than unbounded full-document snapshots. Background parsing has one running job and a latest-generation request, not an unbounded queue. Script output has a fixed byte budget while the pipe continues to drain.

## File system

Workspace operations reject traversal outside the canonical root and symbolic-link components, including safe-looking links pointing back into the root. `.mdbm` internals are not user mutation targets. Saves use a temporary sibling, fsync, content-fingerprint conflict checks and atomic publication. Creation uses an exclusive no-clobber path. Reads and saves recheck path safety. Unix no-follow file flags are used where available. This is protection against malicious file names, links and ordinary concurrent edits, not a claim of isolation against a hostile process with the same OS account racing directory replacement. Windows junction/reparse behavior requires the native acceptance pass.

Local links resolve relative to the active source and must remain within the workspace. Only HTTP, HTTPS and mailto may be handed to a browser, after explicit confirmation; credential-bearing URLs and terminal controls are rejected. No Markdown image is fetched. External links never become shell commands or user-supplied process argument lists; confirmed URLs are passed only to the browser dispatcher.

Recovery files are plaintext, private-permission local data, not encrypted. Treat the workspace account as trusted. Recovery preserves the old disk fingerprint so restoring a snapshot cannot erase a subsequent external edit without conflict confirmation. Malformed or oversized state is ignored with a diagnostic. The state directory may not be a symbolic link. Concurrent state writers are isolated or locked; normal document conflict guards remain in force regardless.

## Scripts

Configuration is trusted operational data, but it does not imply permission to run a script automatically. Only explicit allowlisted sibling basenames ending in `.py` are eligible, excluding `mdbm.py` case-insensitively and by resolved identity. Arguments are a JSON array, never a shell string. Execution uses the current Python interpreter with shell disabled, a fixed working directory and bounded output capture. Cancel terminates the process and, on POSIX, its process group. Script children on Windows may require OS-level cleanup; do not promise a sandbox or universal process-tree cancellation. Themes, document events and recovery never launch scripts.

## Failure policy

A failed theme retains the last known-good compiled scene or falls back to embedded Minimal. A failed preview leaves editing usable. Failed saves retain dirty state and recovery. Failed trash operations retain the source. Invalid config values fall back individually; unrecognized keys produce diagnostics. Emergency palette, restore and exit bindings remain host-owned. No destructive default or silent overwrite is acceptable.
