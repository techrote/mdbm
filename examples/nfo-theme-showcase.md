# Underground NFO collection

Six original ANSI/NFO-inspired appearance themes for MDBM. Large block-letter
mastheads, offset extrusion, specular bevels, shaded blocks and distressed
machinery bring the feel of scene release artwork into the terminal workspace.
All art is declarative cell data; the themes contain no raw ANSI sequences.

## Launch

```sh
python mdbm.py --theme rust-bastion examples/nfo-theme-showcase.md
```

Replace `rust-bastion` with any of the IDs below. The existing theme picker is
also available through Ctrl+P. To set a startup preference, edit `theme` in
the existing `[appearance]` section of `mdbm.conf`.

For the full masthead, use a terminal around **140 columns by 52 rows** or
larger. The default artspace configuration plus each theme's geometry reserves
ten rows above the workspace. The host trims decoration when space is tight;
mastheads hide below 90 columns, and heavy outer ornament hides below 60.
Changing artspace margins can crop the lettering even on a wide terminal.

## Rust Bastion

**ID:** `rust-bastion` · **Masthead:** RUST

Rusted bunker hardware in near-black brown (`#15100e`), pitted copper
(`#93603b`) and orange (`#f2a45d`), with warm ivory text. Black casting shadows
sit behind metal extrusion and chipped cream highlights. Fragmented plate
textures, rivet strips and shaded beam borders surround the document.
Sparse dust shifts over an 18-second cycle.

## Void Cathedral

**ID:** `void-cathedral` · **Masthead:** VOID

Violet-black recesses (`#100d19`), amethyst lettering (`#c0a0f0`) and pale
lavender highlights (`#f0e0ff`). Angular vault modules and bracket tracery
suggest broken arches cut into stone. The lettering has a dark extrusion,
a purple face and bright carved edges. Dust drifts over 24 seconds.

## Acid Overdrive

**ID:** `acid-overdrive` · **Masthead:** ACID

Contaminated machinery in black olive (`#10150b`), acid lime (`#c7ed62`),
amber and oxidized green. Diagonal hazard hatching, coolant-duct modules,
scratched beam borders and densely shaded letter sides create an industrial
release-screen treatment. Sparse particles move over 16 seconds.

## Cryo Vault

**ID:** `cryo-vault` · **Masthead:** CRYO

Blue-black depth (`#0c1420`), cyan lettering (`#88d9f5`) and ice-white
specular edges (`#edfcff`). Displaced seams and fracture-shaped modules break
up the chrome-like frame. Blue extrusion and chipped highlights make the
masthead feel like a frozen slab. Frost dust drifts over 22 seconds.

## Cinder Relic

**ID:** `cinder-relic` · **Masthead:** CINDER

Scorched oxblood (`#1a0d0c`), burnt bronze (`#9c583f`) and warm orange
(`#e89065`). Broken masonry modules, ash fragments and deeply offset lettering
give this theme the heaviest carved-stone treatment in the collection.
Warm text and muted olive links stay distinct from its dense frame.
Sparse embers move over 20 seconds.

## Static Revenant

**ID:** `static-revenant` · **Masthead:** STATIC

Graphite (`#101115`), silver (`#d7dce8`) and white (`#ffffff`) with restrained
magenta accents. Torn scanlines, vented CRT modules and scratched shaded rails
frame a massive silver wordmark. Deep extrusion and small missing chips give
it an aged release-terminal character. Sparse noise drifts over 14 seconds;
the screen does not flicker or cycle its palette.

## Layer construction and behavior

Each standalone theme contains **22 decorative layers**: ground texture,
pitting, module shadows, raised modules, rivets, chips, five masthead passes,
a release strip, dust, a divider beam and two chrome passes per tile.

The masthead is painted back to front: dark shadow, offset metal extrusion,
coloured face, bright upper edges and surface wear. Repeating module shadows
and four-density beam glyphs add depth around the workspace. All six themes
use the single-cell block/shade vocabulary familiar from ANSI and NFO art.

Only sparse particles animate. Lettering, scars, shadows and major surfaces
stay fixed. Set `motion = "off"` or `motion = "reduced"` in `[appearance]`
for a static scene. The host also enforces accessibility motion preferences.

ASCII fallback uses `#` for block glyphs. Monochrome hides colour-dependent
extrusion and noise. High contrast suppresses mastheads and outer clutter;
the host supplies its protected focus, cursor, selection and dialog styles.
Decorative edit scratches reflect focus, dirty state and save failure; a
successful save briefly accents the dust. Theme art stays out of document text.

## Reading exercise

**Release notes:** readable words inside a battered frame.

> Heavy chrome belongs around the page, with clear space for the document.

| Surface | Treatment |
| --- | --- |
| Masthead | Raised block lettering with five depth passes |
| Borders | Shaded metal, cuts and surface damage |
| Text | Warm or cool high-contrast ink |
| Links | A separate secondary colour |

```python
release = "CELLWORKS"
themes = 6
print(f"{release}: {themes} NFO appearances")
```

Preview all six in `docs/screenshots/nfo/gallery.html`. Those images are
rendered from MDBM's actual cell compositor at 140 x 52, with a fixed document,
seed and animation time. They are stills, not native-terminal screenshots.
Terminal fonts can change the shape and density of block/shade characters.

Regenerate previews with `python tests/render_nfo_gallery.py` after installing
MDBM's dependencies and Pillow. No runtime-code changes are needed.
