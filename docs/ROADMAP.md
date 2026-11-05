# Roadmap

## Current build: 1.0.0a4

A single-source, cell-native Markdown workspace with protected host interaction
planes, safe data-only themes, source-mapped preview, guarded files and scripts,
and up to 32 open documents. Multi-document sessions, tabs and conservative
terminal path ingress were delivered in MDBM-001/002/003. The November 2026
pass adds Ctrl+B document filtering, literal search across open buffers,
guarded Save All, recovery/preview repairs, installed-package checks and
permanent Linux/Windows CI. The continuation delivers v3 recovery blobs and
quota, a paginated inventory, OS-held primary ownership, background disk/search
work, parsed-Markdown reflow, deferred fragment navigation, named workspaces,
maximize/restore, appearance transactions and Quiet Study. See
[the continuation record](EXTENSION_PASS_2026-11-03.md) and
[the earlier review](IMPROVEMENT_PASS_2026-11-03.md).

## Next reliability work

1. Qualify Windows Terminal and macOS/Linux GUI terminals using the native
   acceptance checklist, including filesystem reparse points, permissions,
   real system-trash integration, terminal paste and cancellation behavior.
2. Reduce the remaining long-tail latency and time to a complete large preview.
   Source edits still require a full Markdown parse; layout remains proportional
   to the rendered document. Width-only parse reuse, deferred fragment navigation,
   asynchronous open-buffer search and disk publication are implemented. Use
   the new input-to-composed-frame probe and native terminal timing separately.
3. Extend script lifecycle ownership to descendants and inherited output pipes
   on both Windows and POSIX, with explicit process-tree cancellation tests.

## Remaining product-contract work

Complete the fine-grained semantic theme vocabulary, broader Markdown
conformance coverage and accessibility/whole-scene flash auditing against the
published contracts. Improve terminal-specific emoji and clipboard adapters
only against measured native behavior. These gates are not satisfied by unit
tests or compositor screenshots alone.

## Deferred extensions

Workspace-wide indexed search and backlinks, Git indicators, export,
Kitty/iTerm2/Sixel images, theme debugger/hot reload/gallery/signatures,
terminal pointer-shape protocols, synchronized updates, arbitrary docking and
dedicated screen-reader rendering. Named presets for the
existing tile solver and open-buffer search are delivered; unopened-file
indexing and a new docking model remain separate scopes.
