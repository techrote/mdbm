import json
from pathlib import Path
import pytest
from render_support import THEMES, SIZES, PROFILES, scene, rendered_digest

BASELINES=json.loads((Path(__file__).parent/'golden'/'frames.json').read_text())
CASES=[(theme,profile,width,height) for theme in THEMES for profile in PROFILES for width,height in SIZES]

@pytest.mark.parametrize('theme,profile,width,height',CASES)
def test_composed_cell_golden(tmp_path,theme,profile,width,height):
    w,canvas=scene(tmp_path,theme,profile,width,height)
    key=f'{theme}/{profile}/{width}x{height}'
    assert rendered_digest(canvas,w.config.depth)==BASELINES['frames'][key]
    assert not w.registry.parity_errors(w.hits)
    assert all(0<=hit.rect.x<hit.rect.right<=width and 0<=hit.rect.y<hit.rect.bottom<=height for hit in w.hits)
    if profile=='ascii':assert all(all(ord(c)<128 for c in cell.glyph) for cell in canvas.cells)
