"""Render the actual multi-document overlays for documentation (requires Pillow)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mdbm as m
from render_support import SOURCE, scene


def save_canvas(canvas, destination):
    from PIL import Image, ImageDraw, ImageFont
    fonts=Path('/usr/share/fonts/truetype/dejavu')
    normal=ImageFont.truetype(str(fonts/'DejaVuSansMono.ttf'),16)
    bold=ImageFont.truetype(str(fonts/'DejaVuSansMono-Bold.ttf'),16)
    cw,ch=10,21
    image=Image.new('RGB',(canvas.width*cw,canvas.height*ch),'#0b0d12')
    draw=ImageDraw.Draw(image)
    for y in range(canvas.height):
        for x in range(canvas.width):
            cell=canvas.get(x,y)
            draw.rectangle((x*cw,y*ch,(x+1)*cw-1,(y+1)*ch-1),fill=cell.ink.bg or '#0b0d12')
    for y in range(canvas.height):
        for x in range(canvas.width):
            cell=canvas.get(x,y)
            if cell.continuation: continue
            draw.text((x*cw,y*ch-1),cell.glyph,font=bold if cell.ink.bold else normal,
                      fill=cell.ink.fg or '#dce1e8')
            if cell.ink.underline:
                draw.line((x*cw,y*ch+18,(x+1)*cw-1,y*ch+18),fill=cell.ink.fg or '#dce1e8')
    image.save(destination)


def main():
    output=Path(__file__).resolve().parents[1]/'docs'/'screenshots'/'workspace-tools'
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        workspace,_=scene(root,'neon-circuitry','truecolour',120,40)
        workspace.doc.insert('<!-- unsaved field notes -->\n')
        workspace._append_document(m.DocumentModel('# Design\n\nA preview belongs to its document.\n',root/'project'/'notes.md'))
        workspace._append_document(m.DocumentModel('# Review\n\nKeep preview work isolated across tabs.\n',root/'review'/'notes.md'))
        workspace.doc.insert('<!-- unsaved review -->\n')
        workspace._append_document(m.DocumentModel('# Draft\n\nTry the document picker and preview navigation.\n'))
        workspace.doc.insert('<!-- unsaved draft -->\n')
        workspace.frame(120,40); workspace.parse_sync(workspace.preview_width)
        workspace.choose_document()
        save_canvas(workspace.frame(120,40),output/'document-picker.png')
        workspace.escape(); workspace.search_documents('preview')
        save_canvas(workspace.frame(120,40),output/'open-document-search.png')
    print(output)


if __name__=='__main__':
    main()
