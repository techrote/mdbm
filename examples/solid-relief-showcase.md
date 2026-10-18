# Sculpted environments

These six descendants of the sculpted ASCII trio now treat MDBM as one coherent
material enclosure. The outer housing, tile openings, corners and dividers share
an upper-left light source. There is no repeated row of sculptural emblems.

| Theme ID | Environment |
| --- | --- |
| `intaglio-bronze` | **Bronze Housing**: rounded cast collars, continuous machined grooves, copper grain and warm edge highlights. |
| `intaglio-gunmetal` | **Gunmetal Diepress**: stepped steel shoulders, deeply cut channels, knurled faces and narrow silver glints. |
| `reliquary-amethyst` | **Amethyst Vault**: broad purple facets turn around mitred corners into dark recessed openings. |
| `reliquary-basalt` | **Basalt Crypt**: heavy rounded stone surrounds, porous surfaces and deep chisel-cut inner lips. |
| `monolith-glacial` | **Glacial Chamber**: slanted crystalline planes, one oblique fracture and blue-black recesses. |
| `monolith-aurora` | **Aurora Grotto**: smoothly rounded aquamarine stone with flowing grooves that turn through the corners. |

## Structure and lighting

The generator shades continuous rectangular material surfaces, not a tiled strip
of objects. Surface normals, cast shadows, local occlusion and restrained specular
highlights are baked into four art atlases: the outer enclosure, tile surrounds,
a vertical mullion and a horizontal mullion. Material profiles differ between the
six designs. All light calculations use the same upper-left direction.

Each occupied cell has a foreground and a background selected from **32 paired
tones**. Backgrounds carry the large-scale value structure, while dense ASCII and
single-cell extended glyphs add machining, pores and fracture grain. Examples
include `Æ`, `Ø`, `¶`, `§`, `≡`, `≈`, `∷`, `Λ`, `Δ`, `╱` and `╲`.

The updated renderer fits each atlas to the actual solved geometry using
nine-slice sampling. Corners retain their orientation; edges extend continuously
between them. Tile edges sample one shared frame rather than restarting the
texture in each strip. Both sides of a divider sample one common surface across
the protected drag handle. Seven decorative layers replace the previous 62-layer
collection of repeated motifs and tone masks.

The art remains clipped to host-owned decorative cells. It cannot paint document
content, take over controls or change the tile layout. The existing focus outline,
selection, cursor, menus and one-cell divider handles stay protected.

## Use

Update the application and themes together: these six use the new `style_grid`,
`fit = "frame"` and oriented-divider support. The other theme collections retain
their existing format and behavior.

```sh
python mdbm.py --theme intaglio-bronze examples/solid-relief-showcase.md
```

Choose another ID from the table, or use the theme picker through Ctrl+P. A
144-column, 58-row terminal gives the spacious reference layout, but frames adapt
to resizing, reordered tiles, hidden panes and the Contents tile. The host trims
chrome when space is tight. Below 60 columns or in high-contrast mode the themes
suppress decoration. All six are static; motion preferences never introduce
flashing or moving lighting.

Truecolour provides the intended material gradients. ANSI-256 quantizes them;
ANSI-16 and monochrome reduce depth detail. ASCII mode substitutes extended glyphs
with `#` while retaining colour shading. Terminal fonts can change glyph texture.

## Visual and automated review

The full-workspace gallery is `docs/screenshots/solid-relief/gallery.html`.
`comparison.png` shows all six environments; `*-detail.png` includes an outer
corner, a tile corner and the neighboring divider. `squint-review.png` shows each
complete environment at 50%, 25% and 12.5%, with a grayscale value check.

At reduced size, the surrounding mass, inset panels, bright bevels and dark cuts
remain legible as a connected enclosure. Fine grain recedes instead of carrying
the entire illusion. Grayscale crops retain background values to check structure
independently of hue; they are not monochrome terminal simulations.

`validation.json` records checks across eight profiles, four terminal sizes and
four additional layouts per theme. The frame tests cover corner identity,
background styling, divider continuity, compact clipping, protected cells,
malformed style grids and resource limits. Existing golden baselines are unchanged.

## Regeneration

Run `python tests/build_solid_relief_themes.py` to intentionally regenerate these
six theme files, then `python tests/render_solid_relief_gallery.py` for previews.
These tools require MDBM dependencies, Pillow and a supported monospace font.
Edit the generator to preserve design changes: regeneration overwrites its six
outputs. The application itself never executes the generator or any theme code.
