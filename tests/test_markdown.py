import time
import pytest
import mdbm as m

TEXT='''# Same

Paragraph **bold** and *italic* ~~strike~~ [heading](#same).

## Same

| A | B |
| - | - |
| one | two |

- [x] Complete
- [ ] Pending

```python
x = "value"
print(x)
```

Literal <script>alert(1)</script> and https://example.org.

A note[^n].

[^n]: The footnote text.
'''


def test_markdown_extensions_and_maps():
    parsed=m.MarkdownEngine().parse(TEXT,42);layout=m.make_preview_layout(parsed,60)
    assert [h.slug for h in parsed.headings]==['same','same-1']
    display='\n'.join(''.join(s.text for s in row.spans) for row in layout.rows)
    assert '<script>alert(1)</script>' in display
    assert 'The footnote text.' in display and '[x]' in display and '[ ]' in display
    assert any(b.kind=='table-header' for b in parsed.blocks)
    assert any(s.strike for b in parsed.blocks for s in b.spans)
    assert any(s.role=='string' for b in parsed.blocks for s in b.spans)
    assert all(0<=r.start<r.end<=len(TEXT.splitlines())+1 for r in layout.rows)
    assert layout.source_to_row(parsed.headings[1].source_line)==layout.anchors['same-1']
    assert any(s.link=='https://example.org' for r in layout.rows for s in r.spans)


@pytest.mark.parametrize('width',[1,2,10,40,80])
def test_preview_cell_width(width):
    parsed=m.MarkdownEngine().parse(TEXT+'\n界界e\u0301界\n')
    layout=m.make_preview_layout(parsed,width)
    assert all(sum(m.width_of(s.text) for s in row.spans)<=width for row in layout.rows)


def test_stale_worker_results(workspace):
    w=workspace;worker=m.PreviewWorker()
    try:
        worker.request('# Old',1,30,False);worker.request('# New',2,30,False)
        final=None
        for _ in range(100):
            result=worker.poll()
            if result and result[0]==2:final=result;break
            time.sleep(.01)
        assert final is not None and final[2][0].headings[0].title=='New'
    finally:worker.close()


def test_single_block_preview_limit(monkeypatch):
    monkeypatch.setattr(m,'MAX_PREVIEW_ROWS',20)
    parsed=m.MarkdownEngine().parse('x'*10000)
    layout=m.make_preview_layout(parsed,5)
    assert len(layout.rows)==20
    assert all(sum(m.width_of(s.text) for s in row.spans)<=5 for row in layout.rows)
    assert ''.join(s.text for s in layout.rows[-1].spans)=='Previ'


def test_wrap_internal_limit():
    assert len(m.wrap_spans((m.Span('x'*10000),),1,max_rows=7))==7
    assert m.wrap_spans((m.Span('text'),),10,max_rows=0)==[]
