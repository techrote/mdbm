"""Stable scenes shared by golden tests and the optional visual gallery generator."""
import hashlib
import json
from pathlib import Path
import mdbm as m

SOURCE = '''# Field Notes

A terminal-native page with **clear controls** and *quiet margins*.

## Workspace

- Filesystem explores the root.
- Contents follows the headings.
- Edit holds the source.
- View shows the result.

## A small program

```python
message = "Make room for words."
print(message)
```

> The host protects the cursor, selection, focus and confirmations.

### Navigation

[Return to the top](#field-notes). Keep writing.
'''
THEMES = ('restrained-monochrome', 'neon-circuitry', 'dense-glitch-mosaic')
ANIMATED_THEMES = ('maintenance-ecosystem', 'relay-moth-swarm', 'diagnostic-aurora')
GALLERY_THEMES = THEMES + ANIMATED_THEMES
SIZES = ((40,12), (80,24), (120,40), (200,60))
PROFILES = {
    'truecolour': ('truecolour', False, False, False, 'normal'),
    'ansi256': ('256', False, False, False, 'normal'),
    'ansi16': ('16', False, False, False, 'normal'),
    'monochrome': ('mono', False, False, False, 'off'),
    'ascii': ('truecolour', True, False, False, 'normal'),
    'reduced': ('truecolour', False, False, False, 'reduced'),
    'immersive': ('truecolour', False, True, False, 'normal'),
    'high-contrast': ('truecolour', False, False, True, 'normal'),
}

def scene(root: Path, theme: str, profile: str, width: int, height: int):
    depth, ascii_, immersive, contrast, motion = PROFILES[profile]
    cfg=m.load_config(root/'mdbm.conf',raw={
        'schema':'mdbm-config/1.0',
        'application':{'root':str(root),'state_directory':str(root/'.mdbm')},
        'appearance':{'theme':theme,'seed':10841,'motion':motion,'immersive':immersive},
        'capabilities':{'colour_depth':depth},
        'accessibility':{'ascii_only':ascii_,'high_contrast':contrast},
    })
    w=m.Workspace(cfg,restore=False,persist=False)
    w.set_document(m.DocumentModel(SOURCE,root/'field-notes.md'))
    w.tree_cache=[(root/'notes',0,True,False),(root/'field-notes.md',0,False,False),(root/'README.md',0,False,False)]
    w.set_layout('focus',m.Tile.EDIT);w.clock=6.0;w.session_seed=10841
    w.frame(width,height);w.parse_sync(w.preview_width)
    canvas=w.frame(width,height)
    return w,canvas


def rendered_digest(canvas,depth):
    runs=[canvas.line_fragments(y,depth) for y in range(canvas.height)]
    return hashlib.sha256(json.dumps(runs,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
