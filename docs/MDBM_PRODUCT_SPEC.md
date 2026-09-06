# MDBM product specification

Status: Implementation contract for 1.0.0a2. The delivered implementation report records verification limits; it does not override this contract.

## Authority and precedence

Security → product behavior → configuration/theme schemas → technical design → tests → roadmap. Themes are presentation data, not plugins. There was no legacy draft, reference-image attachment, existing source tree, or ribbon-heading specification in the input; this build defines the explicit compatible subset and menu headings below rather than claiming compatibility with an unseen format.

## Workspace

Four tile identities persist independently: Filesystem, Contents, Edit, View. The left column selects Filesystem or Contents; the right column initially stacks View above Edit. Defaults are 30/70 and 50/50, four cells of outer user artspace, and all three active tiles visible. Hiding, switching, swapping and resizing never destroy local cursor, selection, scroll or row memories. The active left tile is an operational setting. Column visibility is independent of remembered tile choice.

A one-cell structural divider is the entire resize target. Its decorative bands are not resize targets. Drag with either primary or secondary button. Capture follows the pointer outside the divider, previews clamped ratios, commits once on release, and rolls back on Escape or loss of terminal focus. At constrained sizes, user artspace shrinks before theme decoration, then the workspace presents the focused tile alone. All-hidden mode retains the host ribbon, restore message and global restore command.

The ribbon is **File / Edit / Tile / Navigate / Theme / Scripts / Help**. It is visible for discoverability; F10 and Alt mnemonics activate it. Alt key-release cannot be assumed in VT input, so the portable path is a latch. Ctrl+P opens the searchable command palette, Ctrl+T restores tiles, and Ctrl+Q requests safe exit. These emergency paths cannot be rebound away. Tab/Shift+Tab cycle visible tiles outside modal controls. Alt+1/2/3/4 directly reveal and focus Filesystem/Contents/Edit/View.

## Pointer contract

The host generates hit rectangles from final composed geometry. Whole visible buttons and tree rows are clickable. A click on an unfocused editor places the caret immediately; no second click is required. Drag creates an exclusive-end selection. Double-click selects a word and triple-click a source line. Wheel scroll is tile-local without moving keyboard selection. Right-click preserves a selection containing the pointed position; outside it the caret moves before the context menu opens. A preview click navigates to the start of the associated block, preserving an existing Edit selection rather than silently replacing it. Explicit heading navigation may collapse selection. Link activation is a separate hit target.

A single border click focuses its tile; a double-click or secondary click opens the compact tile menu. Menu hover may open a submenu after a delay but never changes the keyboard-selected row. Escape closes exactly one overlay. Outside-click dismissal consumes the click, with no click-through. Menus are clamped inside the viewport and submenus open inward at the right edge. There is no requirement for terminal pointer-shape support.

## Edit and Markdown

Editing uses UTF-8 source with grapheme-aware cursor/deletion/selection, cell-aware vertical movement, horizontal scrolling, tab expansion, bounded delta undo/redo, literal search and replacement, an application clipboard and bracketed paste. Terminal-managed paste is also accepted. The optional Vim profile provides Normal, Insert and Visual modes; h/j/k/l, w/b, 0/$, i/a/o, x, v, y, p, u, Ctrl+R and : command-palette access. It is intentionally a documented subset, not a Vim emulator.

View parses CommonMark, tables, task lists, strikethrough, autolinks, footnotes and fenced code. HTML is displayed literally and is never executed or sent to a browser automatically. Syntax colour uses Pygments. Heading IDs are deterministic with duplicate suffixes. Every preview row retains a source range. Large preview jobs are debounced and generation-tagged. Editor data remains authoritative when preview is pending or fails.

Filesystem is rooted, expandable and keyboard navigable. Commands support opening, new buffers, file creation through Save As, new directories, rename/move and trash. Mutations are guarded against escaping the root, symbolic links, protected state paths and unsaved data loss. Saving is UTF-8 with the loaded newline/BOM convention retained; mixed-newline input is diagnosed and normalized on an explicit save. External modifications produce a conflict confirmation, not silent overwrite. Trash failure never falls back to permanent deletion.

## Modals and lifecycle

Input dialogs support selection, Home/End, paste, Delete/Backspace and history-free text entry. Destructive dialogs default to Cancel. Dirty navigation/exit offers Save, Discard and Cancel. Save As and conflict confirmations are chained without losing the pending operation. The theme chooser previews on keyboard highlight or deliberate click, commits with Enter or a second activation, and rolls back on Escape. Selected theme and dynamic layout are persisted in session state; hand-edited configuration comments are untouched.

Scripts are sibling `.py` files explicitly named in configuration. The executable itself is excluded. Arguments must be a JSON list of strings. A host confirmation precedes execution; stdout/stderr are captured into a bounded, cancellable, sanitized overlay. Script code is trusted user code and is not a sandbox.

Sessions retain root, active file, theme, layout, focus and tile memories. Recovery snapshots retain unsaved UTF-8 source and the original disk fingerprint. A recovery prompt never writes directly to the source file. State is local, bounded and atomic. Exit and exceptions restore terminal modes through prompt_toolkit. Only a genuine terminal can run the interactive UI; diagnostics and theme validation work non-interactively.
