# Roadmap

## Current build: 1.0.0a3

A single-source, cell-native Markdown workspace with protected host interaction
planes, safe data-only themes, source-mapped preview, guarded files and scripts,
and up to 32 open documents. Multi-document sessions, tabs and conservative
terminal path ingress were delivered in MDBM-001/002/003. The November 2026
pass adds Ctrl+B document filtering, literal search across open buffers,
guarded Save All, recovery/preview repairs, installed-package checks and
permanent Linux/Windows CI. See [the review and verification record](IMPROVEMENT_PASS_2026-11-03.md).

## Next reliability work

1. Define an aggregate recovery-storage budget or per-document snapshot format
   so the maximum multi-document set cannot exceed the reader's bundle budget.
   Include deferred snapshot inventory/retention and simultaneous-startup
   stale-lock reclamation in that design.
2. Qualify Windows Terminal and macOS/Linux GUI terminals using the native
   acceptance checklist, including filesystem reparse points, permissions,
   real system-trash integration, terminal paste and cancellation behavior.
3. Improve large-document responsiveness: reuse parsed Markdown for width-only
   reflow, avoid synchronous fragment parsing, and measure end-to-end input
   latency while parsing/searching and writing recovery.
4. Extend script lifecycle ownership to descendants and inherited output pipes
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
terminal pointer-shape protocols, synchronized updates, arbitrary host-approved
layout presets and dedicated screen-reader rendering. Open-buffer search is
delivered; unopened-file indexing remains a separate scope.
