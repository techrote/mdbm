# MDBM / Field Notes

A quiet page inside a living terminal. **Write below; read above.**

## The four tiles

| Tile | Purpose |
| --- | --- |
| Filesystem | Browse your rooted workspace |
| Contents | Follow the live heading tree |
| Edit | Work directly with Markdown source |
| View | Read the rendered document |

## First flight

- [x] Open this document in MDBM.
- [ ] Click the Edit tile and add a sentence.
- [ ] Try the Theme menu; Escape cancels a preview.
- [ ] Drag either divider, then press Escape to roll back.

> Controls stay readable even when the artspace gets noisy.

```python
from pathlib import Path

message = "Make room for words."
print(message)
```

### Source navigation

Click ordinary preview text to reveal its source block. Use the Contents
headings for a keyboard route. [Return to the top](#mdbm-field-notes).

Unicode source stays intact: café, é, 界. Ambiguous terminal emoji may be
represented by a safe replacement cell in this alpha.

## Safe by default

HTML stays literal: <script>this is not executed</script>.
An [external link](https://example.org) asks before opening the system browser.
~~Theme scripts~~ are not part of the theme language.

A small footnote keeps the margins honest.[^note]

[^note]: The installation uses the declared Markdown extension plugins; an explicitly diagnosed bootstrap renderer is available when they are absent.
