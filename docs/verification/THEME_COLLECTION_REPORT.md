# Theme collection verification — 18 October 2026

The collection adds 21 themes across four galleries. The latest six are connected
sculpted environments using geometry-fitted art and per-cell foreground/background
styles. The six pre-existing file-based themes remain available.

## Automated results

- **523 passed** across core geometry/editing, interactions, Markdown, themes,
  frame fitting and composed-cell golden tests.
- **25 passed, 1 skipped** in the file-operation checks that completed outside
  the Windows sandbox. The sandbox initially blocked no-clobber hard-link
  creation; the same save test passed outside it. Symlink creation was unavailable.
- **21 frame tests passed again** after the style-reference cache optimization;
  these are a repeat of cases included in the 523 above, not additional cases.
- **Four POSIX PTY tests skipped** on Windows, as declared by the existing suite.
- **Two subprocess tests did not complete in this runner.** The full-suite
  attempt stalled at subprocess output handling; an outside-sandbox attempt was
  interrupted. No script-runner code was changed. This report does not claim an
  uninterrupted full-suite pass or native terminal acceptance.

Commands for the main completed runs:

```sh
python -m pytest tests/test_theme.py tests/test_theme_frames.py tests/test_goldens.py tests/test_core.py tests/test_interactions.py tests/test_markdown.py -q
python -m pytest tests/test_files.py -q -k 'not script_output and not script_cancellation'
python -m pytest tests/test_theme_frames.py tests/test_pty.py -q
```

Existing golden baselines were not regenerated. The new frame tests verify source
corner identity at multiple sizes, per-cell backgrounds, continuous divider
lighting, protected handles and content, compact clipping, malformed data,
unsupported fitting combinations and the shared decoded-art/style-cell limit.

## Scene matrix and visual review

Each of the six environments was composed in eight profiles at four sizes:
40×12, 80×24, 144×58 and 200×60. Profiles cover truecolour, ANSI-256, ANSI-16,
monochrome, ASCII, reduced motion, immersive and high contrast. Four additional
layouts per theme cover reordered right tiles, Contents, a hidden left column
and a hidden View tile. All **216 scene checks passed**.

The maximum observed decorative paint count was **6,704 of 200,000 operations**.
This is an operation-budget result, not an end-to-end latency measurement.
Machine-readable results are in
`docs/screenshots/solid-relief/validation.json` (relative to the repository root).

Actual-cell full-workspace previews were inspected at multiple scales. The review
sheet includes 50%, 25%, 12.5% and grayscale views. The outer mass, recessed tile
openings, corner turns and divider highlights remain coherent when the grain is
too small to read. Text, focus, selection and controls remain host-owned.

The environment used Python 3.12 on Windows with the declared runtime dependencies,
including prompt-toolkit 3.0.53 and mdit-py-plugins 0.6.1. Preview generation uses
Pillow and Consolas. Different native terminal fonts may alter glyph texture.
