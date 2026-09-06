"""Explicit baseline update. Review the static gallery after regenerating."""
import json,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from render_support import THEMES, SIZES, PROFILES, scene, rendered_digest

def main():
    frames={}
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        for theme in THEMES:
            for profile in PROFILES:
                for width,height in SIZES:
                    w,canvas=scene(root,theme,profile,width,height)
                    frames[f'{theme}/{profile}/{width}x{height}']=rendered_digest(canvas,w.config.depth)
    output=Path(__file__).parent/'golden'/'frames.json'
    output.write_text(json.dumps({'schema':'mdbm-golden/1.0','note':'Regression baselines from the implementation, not an independent visual oracle. PTK-style runs are quantized before hashing.','frames':frames},indent=2)+'\n')
    print(f'Wrote {len(frames)} golden cases.')

if __name__=='__main__':main()
