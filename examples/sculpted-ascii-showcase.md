# Sculpted ASCII relief

Three standalone MDBM themes developed from the Underground NFO collection.
All decorative glyphs are printable ASCII. The drawings use contours,
perspective, hatching and negative space to describe solid objects.

## Iron Intaglio

ID: `iron-intaglio`. Based on Rust Bastion.

A cast-iron die plate with a raised FORGE inscription and a concentric machined
boss. Parallel underscores describe the plate's top lip; slashes turn that lip
into a receding plane. Vertical bars close the front faces, equals signs give
the letter stems a milled finish, and colons describe the narrow side wall.
Dark `@` counters recess the lettering; `%` hatching deepens the boss's side.
Pier brackets and repeating stepped moldings carry this construction into the
workspace borders. Copper edges sit against warm iron-black surfaces.

## Obsidian Reliquary

ID: `obsidian-reliquary`. Based on Void Cathedral.

A carved shrine with nested pointed arches, fluted piers and a faceted seal.
Repeated `/` and `\` contours show successive arch depths. Vertical strokes
and colons form the pilaster flutes. Dense `%` and `#` marks occupy low recesses,
while sparse dots describe weathering within the vaults. The inverted base
wedges and repeated pointed molding connect the architectural relief to the
tile chrome. Amethyst outlines and pale bevels sit above violet-black cavities.

## Glacier Monolith

ID: `glacier-monolith`. Based on Cryo Vault.

An oblique ice block with an inset ICE inscription and fractured crystal fins.
Long underscores define polished planes; diagonal slashes cut across them as
refraction seams. Nested triangular outlines describe thickness. Sparse dots
and colons shade small facets; dense percent hatching sits at the fracture
roots. Folded crystalline piers and sawtooth moldings extend the geometry into
the borders. Ice-white highlights face blue side planes and deep cyan cavities.

## Use and inspect

```sh
python mdbm.py --theme iron-intaglio examples/sculpted-ascii-showcase.md
```

Replace the theme ID to select another design, or use the existing theme picker
through Ctrl+P. These files are already in `themes/`. No runtime changes or
configuration edits are required to launch them.

Use approximately **140 columns by 58 rows** for the full composition. The
theme requests eight top-margin rows; with the default four user rows this
provides twelve rows for the sculpture. The large relief hides below 120
columns. Below 60 columns, outer ornament hides too. The host can trim margins
at short terminal heights; a wide but short terminal may crop the artwork.

Each theme has 34 decorative layers. Five character families separate light
edges, outlines, recesses, hatching and face marks. A displaced back contour
adds a cast shadow without filling the letter counters. Borders use the same
sculptural vocabulary as their corresponding header.

All three are static. ASCII mode preserves their artwork directly. Monochrome
preserves contours and hatching while dropping displaced shadows; high contrast
suppresses the outer sculpture. The host retains control of focus, selection,
cursor, menus and confirmations. Decorative edit outlines indicate dirty state
and save outcomes without changing document text.

Inspect `examples/relief-studies.nfo` in a monospaced editor to see the original
ASCII reliefs without colour. Full workspace previews and a close-up sheet are
in `docs/screenshots/relief/`. Regenerate them with
`python tests/render_relief_gallery.py` (MDBM dependencies and Pillow required).
These are actual-cell renders, not native terminal screenshots; font metrics
can affect how continuous diagonals and shading appear.
