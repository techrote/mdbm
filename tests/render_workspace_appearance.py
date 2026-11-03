"""Render actual workspace/appearance controls; optional Pillow documentation tool."""
import sys
import tempfile
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mdbm as m
from render_support import scene
from render_workspace_tools import save_canvas


def main():
    output=Path(__file__).resolve().parents[1]/'docs'/'screenshots'/'workspace-appearance'
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        workspace,_=scene(root,'quiet-study','truecolour',120,40)
        workspace.config.data['artspace']['outer']=[2,2,2,2]
        workspace.apply_workspace('review')
        workspace.frame(120,40); workspace.parse_sync(workspace.preview_width)
        save_canvas(workspace.frame(120,40),output/'quiet-study-review.png')
        workspace.save_workspace('My review desk'); workspace.choose_workspace()
        save_canvas(workspace.frame(120,40),output/'workspace-presets.png')
        workspace.escape(); workspace.choose_appearance()
        save_canvas(workspace.frame(80,24),output/'appearance-controls.png')
        workspace.escape(); workspace.apply_workspace('review'); workspace.set_layout('focus',m.Tile.VIEW)
        workspace.frame(40,12); workspace.parse_sync(workspace.preview_width)
        save_canvas(workspace.frame(40,12),output/'compact-review.png')
        workspace.store=m.StateStore(root/'.mdbm')
        try:
            base=datetime(2026,11,3,9,tzinfo=timezone.utc).timestamp()
            for index in range(3):
                workspace.doc.insert('<!-- Saved working draft '+str(index+1)+' -->\n')
                name='recovery-'+f'{index+1:032x}'+'.json'
                workspace.store.write(name,workspace.recovery_data())
                os.utime(workspace.store.directory/name,(base+index*900,base+index*900))
            workspace.store.close()
            workspace.store=m.StateStore(root/'.mdbm')
            workspace.apply_workspace('review'); workspace.frame(120,40)
            workspace.parse_sync(workspace.preview_width)
            workspace.show_recovery_inventory()
            save_canvas(workspace.frame(120,40),output/'recovery-inventory.png')
        finally: workspace.store.close()
    print(output)


if __name__=='__main__':
    main()
