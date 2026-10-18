# Six appearance themes

These standalone `md-editor-theme/1.1` themes work with the existing theme
picker. Open Ctrl+P and choose the theme-selection command, or launch one:

```sh
python mdbm.py --theme botanical-ledger examples/appearance-theme-showcase.md
```

Replace `botanical-ledger` with any ID below. To keep a theme between launches,
set `theme = "<id>"` in the existing `[appearance]` section of `mdbm.conf`.
The application's existing session restoration may also restore a committed
theme choice; use `--no-restore` for an isolated launch.

## Botanical Ledger

**ID:** `botanical-ledger` · **Light / static**

Warm herbarium paper (`#f2eddf`), forest ink (`#253b30`), green headings,
copper code, blue-green links and muted plum quotations. Repeated pressed-leaf
modules sit over ledger-like ladders; tiny seed marks decorate the margins.
Designed for prose, notes and long reading sessions with a warm paper palette.

## Tidal Observatory

**ID:** `tidal-observatory` · **Dark / animated**

Deep navy (`#081c29`), sea-glass text (`#d7ecec`), turquoise headings,
gold code and coral accents. Current bands, instrument-cross motifs and sparse
buoys frame the workspace. The buoys drift through a slow 18-second spatial
cycle; no whole-screen colour cycling is used.

## Ember Foundry

**ID:** `ember-foundry` · **Dark / animated**

Charcoal brown (`#211915`), warm ivory (`#f3e5d3`), copper-orange headings,
brass code and oxidized turquoise links. Riveted machine modules and rails
surround sparse sparks on a 16-second drift cycle. A warm industrial treatment
for technical documents and code-heavy notes.

## Polar Blueprint

**ID:** `polar-blueprint` · **Light / static**

Glacial white (`#edf4f8`), blue-black text (`#213a51`), cobalt headings,
violet code and deep teal links. Registration crosses, square drafting modules
and connector marks form a precise technical grid. All decoration is static.

## Velvet Nocturne

**ID:** `velvet-nocturne` · **Dark / animated**

Plum black (`#201729`), pearl text (`#ede2f1`), antique-gold headings,
lavender code and lilac links. Repeated diamond ornaments, brackets and rose
stars create a quiet geometric frame. Stars drift over 28 seconds.

## Desert Cartography

**ID:** `desert-cartography` · **Light / static**

Sandstone paper (`#f5e6ca`), umber text (`#493b2a`), terracotta headings,
brown code and turquoise links. Stepped contour lines, waypoint specks and
compass roses turn the margins into a small atlas. A static travel-journal
palette with restrained ornament.

## Shared behavior

- Each theme defines the editor/preview semantic roles, code-token colours,
  nine decorative layers, focus/dirty/save cues and four portability fallbacks.
- Decorative focus rails use the accent colour. Dirty edit rails use warning
  colour; save success accents the margin, and save failure accents the edit rail.
  Protected focus, selection, cursor and dialogs remain controlled by MDBM.
- Signature motifs hide at 90 columns or fewer. Below 60 columns, additional
  outer decoration hides. ASCII and monochrome fallbacks are included.
- High-contrast mode suppresses nonessential outer decoration. Reduced/off
  motion freezes animation through the host's accessibility policy.
- Background grain is available only when the user enables immersive mode.
  Ordinary document surfaces remain opaque by default.
- The default configuration reserves four cells around the workspace for art.
  Smaller configured margins naturally show less of the decorative design.

## Preview exercise

Use **bold emphasis**, *italic emphasis* and a [local link](tour.md).

> A theme should give the document a setting while keeping its words readable.

| Surface | Example |
| --- | --- |
| Heading | Document structure |
| Code | Syntax and technical notes |
| Link | Navigation |
| Quote | Secondary voice |

```python
def describe_theme(name: str) -> str:
    # Keep the words at the centre of the workspace.
    count = 6
    return f"{name}: one of {count} appearance themes"
```

The actual-cell comparison gallery is at
`docs/screenshots/appearance/gallery.html`. Regenerate it with
`python tests/render_appearance_gallery.py` (requires Pillow for PNG previews).
