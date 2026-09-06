#!/usr/bin/env python3
"""MDBM — a cell-composited Markdown workspace; sole runtime source file.

Python 3.11+. See docs/SECURITY_MODEL.md before changing trust boundaries.
Terminal I/O and physical screen diff: prompt_toolkit. Cells/hits/state: MDBM.
"""
from __future__ import annotations

# =============================================================================
# 1. Bootstrap, imports, constants and immutable value objects
# =============================================================================
import argparse
import asyncio
import bisect
import codecs
import collections
import concurrent.futures
import contextlib
import copy
import dataclasses
import enum
import functools
import hashlib
import importlib.metadata
import io
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import tomllib
import unicodedata
import urllib.parse
import uuid
import webbrowser
from array import array
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

try:
    import regex
    from wcwidth import wcswidth
    from markdown_it import MarkdownIt
    from pygments import lex
    from pygments.lexers import get_lexer_by_name, TextLexer
    from pygments.token import Token
    from prompt_toolkit.application import Application
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import UIControl, UIContent
    from prompt_toolkit.mouse_events import MouseButton, MouseEventType, MouseModifier
    from prompt_toolkit.output import ColorDepth
    from prompt_toolkit.styles import Style as PTStyle
except ImportError as exc:
    raise SystemExit(
        'MDBM dependency missing: ' + ascii(str(exc)) + '\n'
        'Install with: python -m pip install -r requirements.txt'
    ) from None

try:
    from mdit_py_plugins.footnote import footnote_plugin
    from mdit_py_plugins.tasklists import tasklists_plugin
    HAVE_MDIT_PLUGINS = True
except ImportError:
    # Offline bootstrap fallback. The declared release dependencies remain required.
    HAVE_MDIT_PLUGINS = False

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

VERSION = '1.0.0a2'
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR if (APP_DIR/'themes').is_dir() else Path(sys.prefix)/'share'/'mdbm'
MAX_THEME_BYTES = 1024 * 1024
MAX_THEME_OBJECTS = 4096
MAX_STRING_BYTES = 16384
MAX_ART_CELLS = 262144
MAX_DEPTH = 16
MAX_UNDO_BYTES = 32 * 1024 * 1024
MAX_UNDO_STEPS = 512
MAX_PREVIEW_ROWS = 300000
MAX_PARSE_TOKENS = 500000
NAME_RE = re.compile(r'^[a-z][a-z0-9_.-]{0,63}$')
COLOUR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
BIDI = frozenset('\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069')
GRAPHEMES = regex.compile(r'\X')


class MDBMError(Exception):
    """A sanitized user-facing failure, never a terminal escape channel."""


class ThemeError(MDBMError):
    pass


class PathError(MDBMError):
    pass


class ConflictError(MDBMError):
    pass


class Tile(str, enum.Enum):
    FILESYSTEM = 'filesystem'
    CONTENTS = 'contents'
    EDIT = 'edit'
    VIEW = 'view'


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def inset(self, n: int = 1) -> Rect:
        return Rect(self.x + n, self.y + n, max(0, self.w - 2*n), max(0, self.h - 2*n))

    def intersect(self, other: Rect) -> Rect:
        x, y = max(self.x, other.x), max(self.y, other.y)
        return Rect(x, y, max(0, min(self.right, other.right)-x),
                    max(0, min(self.bottom, other.bottom)-y))

    def clamp_point(self, x: int, y: int) -> tuple[int, int]:
        return (max(self.x, min(self.right-1, x)), max(self.y, min(self.bottom-1, y)))


@dataclass(frozen=True, slots=True)
class Ink:
    fg: str | None = None
    bg: str | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    dim: bool = False
    strike: bool = False
    mono_reverse: bool = False  # host-only: not accepted by theme style schema

    @functools.lru_cache(maxsize=8192)
    def ptk(self, depth: str = 'truecolour') -> str:
        parts: list[str] = []
        if depth != 'mono':
            if self.fg:
                parts.append('fg:' + quantize(self.fg, depth))
            if self.bg:
                parts.append('bg:' + quantize(self.bg, depth))
        for key in ('bold', 'italic', 'underline', 'dim', 'strike'):
            if getattr(self, key):
                parts.append(key)
        if depth=='mono' and self.mono_reverse: parts.append('reverse')
        return ' '.join(parts)


@dataclass(frozen=True, slots=True)
class Cell:
    glyph: str = ' '
    ink: Ink = Ink()
    plane: int = 0
    continuation: bool = False


@dataclass(frozen=True, slots=True)
class Hit:
    rect: Rect
    command: str
    payload: Any = None
    tile: Tile | None = None
    role: str = 'control'
    z: int = 0


@dataclass(frozen=True, slots=True)
class DisplayUnit:
    glyph: str
    start: int
    end: int
    width: int


@dataclass(frozen=True, slots=True)
class LayoutState:
    left_visible: bool = True
    right_visible: bool = True
    edit_visible: bool = True
    view_visible: bool = True
    left_tile: Tile = Tile.FILESYSTEM
    view_first: bool = True
    left_ratio: float = .30
    view_ratio: float = .50
    focus: Tile = Tile.EDIT

    def visible(self) -> tuple[Tile, ...]:
        tiles: list[Tile] = []
        if self.left_visible:
            tiles.append(self.left_tile)
        if self.right_visible:
            order = (Tile.VIEW, Tile.EDIT) if self.view_first else (Tile.EDIT, Tile.VIEW)
            tiles = ([self.left_tile] if self.left_visible else []) + [
                t for t in order if (self.view_visible if t == Tile.VIEW else self.edit_visible)]
        return tuple(tiles)


@dataclass(slots=True)
class TileMemory:
    scroll: int = 0
    scroll_x: int = 0
    row: int = 0
    filter: str = ''
    expanded: set[str] = field(default_factory=lambda: {'.'})


@dataclass(frozen=True, slots=True)
class Geometry:
    viewport: Rect
    workspace: Rect
    tiles: Mapping[Tile, Rect]
    contents: Mapping[Tile, Rect]
    dividers: Mapping[str, Rect]
    slots: Mapping[str, tuple[Rect, ...]]
    compact: bool
    margins: tuple[int, int, int, int]
    band: int


@dataclass(frozen=True, slots=True)
class Span:
    text: str
    role: str = 'text'
    link: str = ''
    bold: bool = False
    italic: bool = False
    strike: bool = False


# =============================================================================
# 2. Safe Unicode conversion, colours and bounded cell composition
# =============================================================================

def unsafe_codepoint(ch: str, allow_layout: bool = False) -> bool:
    n = ord(ch)
    if allow_layout and ch in '\n\t\r':
        return False
    return (n < 32 or 0x7f <= n <= 0x9f or 0xd800 <= n <= 0xdfff or ch in BIDI
            or (unicodedata.category(ch) == 'Cf' and ch not in '\u200c\u200d'))


@functools.lru_cache(maxsize=32768)
def glyph_width(glyph: str) -> int:
    return wcswidth(glyph)


def display_units(text: str, ascii_only: bool = False, tab_size: int = 4,
                  column: int = 0) -> Iterator[DisplayUnit]:
    """Source offsets survive replacement, tab expansion and grapheme grouping."""
    for match in GRAPHEMES.finditer(text):
        g = match.group()
        if g == '\t':
            width = tab_size - column % tab_size
            shown = ' ' * width
        elif (any(unsafe_codepoint(c) for c in g) or '\u200d' in g or '\u200c' in g
              or any(0x1f1e6 <= ord(c) <= 0x1f1ff for c in g)):
            # PTK's character transport cannot promise a consistent ZWJ/flag width.
            shown, width = ('?' if ascii_only else '�'), 1
        elif ascii_only and any(ord(c) > 126 for c in g):
            shown, width = '?', 1
        else:
            width = glyph_width(g)
            if width == 0:
                shown = ('?' if ascii_only else '◌' + g)
                width = max(1, glyph_width(shown))
            elif width < 0 or width > 2:
                shown, width = ('?' if ascii_only else '�'), 1
            else:
                shown = g
        yield DisplayUnit(shown, match.start(), match.end(), width)
        column += width


def display_text(value: Any, ascii_only: bool = False, limit: int = 16384) -> str:
    return ''.join(u.glyph for u in display_units(str(value)[:limit], ascii_only))


def clean_paste(text: str) -> str:
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return ''.join(c for c in text if not unsafe_codepoint(c, allow_layout=True))


def width_of(text: str, ascii_only: bool = False, tab_size: int = 4) -> int:
    return sum(u.width for u in display_units(text, ascii_only, tab_size))


def cell_to_offset(text: str, column: int, ascii_only: bool = False, tab_size: int = 4) -> int:
    x = 0
    for u in display_units(text, ascii_only, tab_size):
        if column < x + u.width:
            return u.start if column - x < u.width / 2 else u.end
        x += u.width
    return len(text)


ANSI16 = ('#000000', '#800000', '#008000', '#808000', '#000080', '#800080', '#008080',
          '#c0c0c0', '#808080', '#ff0000', '#00ff00', '#ffff00', '#0000ff', '#ff00ff',
          '#00ffff', '#ffffff')
ANSI256 = ANSI16 + tuple(f'#{r:02x}{g:02x}{b:02x}' for r in (0,95,135,175,215,255)
                        for g in (0,95,135,175,215,255) for b in (0,95,135,175,215,255)) + tuple(
                            f'#{v:02x}{v:02x}{v:02x}' for v in range(8,239,10))


def rgb(colour: str) -> tuple[int, int, int]:
    return tuple(int(colour[i:i+2], 16) for i in (1,3,5))  # type: ignore[return-value]


@functools.lru_cache(maxsize=4096)
def quantize(colour: str, depth: str) -> str:
    if depth not in ('16', '256'):
        return colour
    value = rgb(colour)
    choices = ANSI16 if depth == '16' else ANSI256
    return min(choices, key=lambda c: sum((a-b)**2 for a,b in zip(value, rgb(c))))


def noise32(x: int, y: int, seed: int = 0) -> int:
    v = ((x*0x1f123bb5) ^ (y*0x5f356495) ^ seed) & 0xffffffff
    v ^= v >> 16
    v = v * 0x7feb352d & 0xffffffff
    v ^= v >> 15
    v = v * 0x846ca68b & 0xffffffff
    return (v ^ (v >> 16)) & 0xffffffff


class Canvas:
    """Cell grid with a budgeted decorative plane and unoccludable host planes."""
    def __init__(self, width: int, height: int, ink: Ink = Ink(), budget: int = 200000):
        self.width, self.height = max(1,width), max(1,height)
        self.bounds = Rect(0,0,self.width,self.height)
        self.cells = [Cell(' ',ink)] * (self.width*self.height)
        self.operations = 0
        self.budget = budget
        self.budget_exhausted = False

    def get(self, x: int, y: int) -> Cell:
        if not self.bounds.contains(x,y):
            return Cell()
        return self.cells[y*self.width+x]

    def put(self, x: int, y: int, glyph: str, ink: Ink, plane: int = 0,
            mode: str = 'replace', opacity: float = 1.0, seed: int = 0) -> None:
        if not self.bounds.contains(x,y):
            return
        if plane == 0:
            if self.operations >= self.budget:
                self.budget_exhausted = True
                return
            self.operations += 1
        width = glyph_width(glyph)
        if width not in (1,2) or x+width > self.width:
            return
        i = y*self.width+x
        old = self.cells[i]
        if old.plane > plane or (width == 2 and self.cells[i+1].plane > plane):
            return
        if opacity < 1 and noise32(x,y,seed) / 0xffffffff >= opacity:
            return
        if mode in ('transparent-cell','underlay') and glyph == ' ':
            return
        if mode == 'underlay' and old.glyph != ' ':
            return
        if mode == 'mask':
            if glyph == ' ':
                return
            glyph, width = old.glyph or ' ', 1 if old.continuation else max(1,glyph_width(old.glyph))
            ink = old.ink
        elif mode == 'glyph-only':
            ink = old.ink
        elif mode == 'foreground-only':
            glyph, width = old.glyph or ' ', 1 if old.continuation else max(1,glyph_width(old.glyph))
            ink = replace(old.ink, fg=ink.fg)
        elif mode == 'background-only':
            glyph, width = old.glyph or ' ', 1 if old.continuation else max(1,glyph_width(old.glyph))
            ink = replace(old.ink, bg=ink.bg)
        elif mode == 'dither-blend' and (x + y) % 2:
            return
        # Never leave a dangling half of a previously drawn wide grapheme.
        if old.continuation and x > 0 and self.cells[i-1].plane <= plane:
            self.cells[i-1] = Cell(' ', self.cells[i-1].ink, plane)
        if not old.continuation and glyph_width(old.glyph) == 2 and x+1 < self.width:
            if self.cells[i+1].plane <= plane:
                self.cells[i+1] = Cell(' ',old.ink,plane)
        if width == 2 and not self.cells[i+1].continuation:
            nxt = self.cells[i+1]
            if glyph_width(nxt.glyph) == 2 and x+2 < self.width and self.cells[i+2].plane <= plane:
                self.cells[i+2] = Cell(' ',nxt.ink,plane)
        self.cells[i] = Cell(glyph,ink,plane)
        if width == 2:
            self.cells[i+1] = Cell('',ink,plane,True)

    def fill(self, rect: Rect, ink: Ink, glyph: str = ' ', plane: int = 10) -> None:
        r = rect.intersect(self.bounds)
        for y in range(r.y,r.bottom):
            for x in range(r.x,r.right):
                self.put(x,y,glyph,ink,plane)

    def text(self, x: int, y: int, text: str, ink: Ink, plane: int = 10,
             clip: Rect | None = None, ascii_only: bool = False) -> int:
        clip = self.bounds if clip is None else clip.intersect(self.bounds)
        start = x
        if not clip.y <= y < clip.bottom:
            return x
        for u in display_units(text,ascii_only):
            if x >= clip.right:
                break
            if x >= clip.x and x+u.width <= clip.right:
                if len(u.glyph) > 1 and u.glyph.isspace():
                    for dx in range(u.width):
                        self.put(x+dx,y,' ',ink,plane)
                else:
                    self.put(x,y,u.glyph,ink,plane)
            x += u.width
        return x - start

    def box(self, rect: Rect, ink: Ink, plane: int = 20, ascii_only: bool = False) -> None:
        if rect.w < 2 or rect.h < 2:
            return
        h,v,tl,tr,bl,br = ('-','|','+','+','+','+') if ascii_only else ('─','│','┌','┐','└','┘')
        for x in range(rect.x+1,rect.right-1):
            self.put(x,rect.y,h,ink,plane); self.put(x,rect.bottom-1,h,ink,plane)
        for y in range(rect.y+1,rect.bottom-1):
            self.put(rect.x,y,v,ink,plane); self.put(rect.right-1,y,v,ink,plane)
        for x,y,g in ((rect.x,rect.y,tl),(rect.right-1,rect.y,tr),
                      (rect.x,rect.bottom-1,bl),(rect.right-1,rect.bottom-1,br)):
            self.put(x,y,g,ink,plane)

    def line_fragments(self, y: int, depth: str) -> list[tuple[str,str]]:
        fragments: list[tuple[str,str]] = []
        style, chars = None, []
        for cell in self.cells[y*self.width:(y+1)*self.width]:
            if cell.continuation:
                continue
            value = cell.ink.ptk(depth)
            if value != style and chars:
                fragments.append((style or '', ''.join(chars))); chars = []
            style = value
            chars.append(cell.glyph)
        if chars:
            fragments.append((style or '', ''.join(chars)))
        return fragments

    def plain(self) -> str:
        return '\n'.join(''.join(c.glyph for c in self.cells[y*self.width:(y+1)*self.width]
                                 if not c.continuation) for y in range(self.height))

    def digest(self) -> str:
        h = hashlib.sha256()
        for c in self.cells:
            h.update((c.glyph+'\0'+c.ink.ptk()+'\0'+str(c.plane)+'\n').encode('utf-8'))
        return h.hexdigest()

    def changed(self, old: Canvas | None) -> int:
        if old is None or (old.width,old.height) != (self.width,self.height):
            return len(self.cells)
        return sum(a != b for a,b in zip(self.cells,old.cells))


# =============================================================================
# 3. Closed operational configuration and functional layout reducer
# =============================================================================
DEFAULT_CONFIG: dict[str, Any] = {
    'schema': 'mdbm-config/1.0',
    'application': {'root': '.', 'state_directory': '.mdbm', 'max_file_mib': 16},
    'layout': {'left_visible':True, 'right_visible':True, 'edit_visible':True,
               'view_visible':True, 'left_tile':'filesystem', 'view_first':True,
               'left_ratio':.30, 'view_ratio':.50, 'min_width':12, 'min_height':3},
    'artspace': {'outer':[4,4,4,4], 'maximum':[12,12,12,12], 'border_extra':0,
                 'divider_extra':0, 'spacer_extra':0, 'band_maximum':4},
    'input': {'profile':'standard', 'tab_size':4, 'double_click_ms':400,
              'submenu_delay_ms':250},
    'bindings': {},
    'appearance': {'theme':'neon-circuitry', 'theme_directories':['themes'],
                   'immersive':False, 'seed':-1, 'motion':'normal'},
    'capabilities': {'colour_depth':'auto', 'unicode':True, 'mouse':True,
                     'terminal_focus':True},
    'accessibility': {'high_contrast':False,'reduced_motion':False,'ascii_only':False},
    'scripts': {'allowlist':[], 'max_output_kib':256},
    'session': {'restore':True, 'recovery_seconds':5, 'save_seconds':5,
                'parse_debounce_ms':150, 'fps':12, 'paint_budget':200000},
}
RANGES = {
    ('application','max_file_mib'):(1,64),
    ('layout','left_ratio'):(.1,.9),('layout','view_ratio'):(.1,.9),
    ('layout','min_width'):(8,80),('layout','min_height'):(2,20),
    ('artspace','border_extra'):(0,8),('artspace','divider_extra'):(0,8),
    ('artspace','spacer_extra'):(0,8),('artspace','band_maximum'):(0,12),
    ('input','tab_size'):(1,8),('input','double_click_ms'):(150,700),
    ('input','submenu_delay_ms'):(100,800),('appearance','seed'):(-1,2**32-1),
    ('scripts','max_output_kib'):(16,1024),('session','recovery_seconds'):(1,300),
    ('session','save_seconds'):(1,300),('session','parse_debounce_ms'):(30,2000),
    ('session','fps'):(1,30),('session','paint_budget'):(10000,2000000),
}
CHOICES = {
    ('layout','left_tile'):{'filesystem','contents'},
    ('input','profile'):{'standard','vim'},
    ('appearance','motion'):{'off','reduced','normal','high'},
    ('capabilities','colour_depth'):{'auto','truecolour','256','16','mono'},
}


@dataclass(slots=True)
class Config:
    data: dict[str,Any]
    base: Path
    diagnostics: list[str] = field(default_factory=list)

    def get(self, section: str, key: str) -> Any:
        return self.data[section][key]

    def path(self, section: str, key: str) -> Path:
        p = Path(self.get(section,key)).expanduser()
        return p if p.is_absolute() else self.base/p

    @property
    def ascii(self) -> bool:
        return self.get('accessibility','ascii_only') or not self.get('capabilities','unicode')

    @property
    def motion(self) -> str:
        return 'reduced' if self.get('accessibility','reduced_motion') else self.get('appearance','motion')

    @property
    def depth(self) -> str:
        depth = self.get('capabilities','colour_depth')
        if depth != 'auto':
            return depth
        if 'NO_COLOR' in os.environ:
            return 'mono'
        if os.environ.get('COLORTERM','').lower() in ('truecolor','24bit') or os.environ.get('WT_SESSION'):
            return 'truecolour'
        return '256'


def load_config(path: Path | None = None, raw: Mapping[str,Any] | None = None) -> Config:
    path = path or APP_DIR/'mdbm.conf'
    data = copy.deepcopy(DEFAULT_CONFIG)
    out = Config(data,path.absolute().parent)
    if raw is None:
        try:
            with path.open('rb') as f:
                content = f.read(MAX_THEME_BYTES+1)
            if len(content) > MAX_THEME_BYTES:
                raise MDBMError('configuration exceeds 1 MiB')
            raw = tomllib.loads(content.decode('utf-8'))
        except FileNotFoundError:
            out.diagnostics.append('Configuration not found; safe defaults are active.')
            return out
        except (OSError,ValueError,RecursionError,MDBMError) as exc:
            out.diagnostics.append('Configuration ignored: '+display_text(exc))
            return out
    if raw.get('schema') != 'mdbm-config/1.0':
        out.diagnostics.append('Unsupported configuration schema; safe defaults are active.')
        return out
    for section, values in raw.items():
        if section == 'schema':
            continue
        if section not in data or not isinstance(values,dict):
            out.diagnostics.append(f'Unknown or invalid configuration section: {display_text(section)}')
            continue
        if section == 'bindings':
            if len(values) > 128:
                out.diagnostics.append('Too many bindings; custom bindings ignored.')
            else:
                for key,value in values.items():
                    if isinstance(value,str) and len(key) <= 64 and NAME_RE.fullmatch(value):
                        data[section][key] = value
                    else:
                        out.diagnostics.append('Invalid binding: '+display_text(key))
            continue
        for key,value in values.items():
            if key not in data[section]:
                out.diagnostics.append(f'Unknown config key: {section}.{display_text(key)}')
                continue
            default = DEFAULT_CONFIG[section][key]
            good = True
            if type(default) is bool:
                good = type(value) is bool
            elif type(default) is int:
                good = type(value) is int
            elif type(default) is float:
                good = type(value) in (int,float) and math.isfinite(value)
            elif isinstance(default,str):
                good = isinstance(value,str) and len(value) <= 4096 and not any(unsafe_codepoint(c) for c in value)
            elif isinstance(default,list):
                good = isinstance(value,list) and len(value) <= 128
                if good and key in ('outer','maximum'):
                    good = len(value)==4 and all(type(v) is int and 0<=v<=40 for v in value)
                elif good:
                    good = all(isinstance(v,str) and len(v)<=4096 and not any(unsafe_codepoint(c) for c in v) for v in value)
            if good and (section,key) in RANGES:
                lo,hi = RANGES[section,key]
                good = lo<=value<=hi
            if good and (section,key) in CHOICES:
                good = value in CHOICES[section,key]
            if good:
                data[section][key] = value
            else:
                out.diagnostics.append(f'Invalid {section}.{key}; using {default!r}.')
    return out


def initial_layout(config: Config) -> LayoutState:
    values = {k:config.data['layout'][k] for k in dataclasses.asdict(LayoutState()) if k != 'focus'}
    values['left_tile'] = Tile(values['left_tile'])
    return LayoutState(**values)


def reduce_layout(state: LayoutState, action: str, value: Any = None) -> LayoutState:
    if action == 'restore':
        state = replace(state,left_visible=True,right_visible=True,edit_visible=True,view_visible=True,
                        focus=Tile.EDIT)
    elif action == 'focus':
        tile = Tile(value)
        if tile in (Tile.FILESYSTEM,Tile.CONTENTS):
            state = replace(state,left_visible=True,left_tile=tile,focus=tile)
        else:
            state = replace(state,right_visible=True,focus=tile,
                            **{('edit_visible' if tile == Tile.EDIT else 'view_visible'):True})
    elif action == 'switch-left':
        tile = Tile.CONTENTS if state.left_tile==Tile.FILESYSTEM else Tile.FILESYSTEM
        state = replace(state,left_tile=tile,focus=tile if state.focus in (Tile.CONTENTS,Tile.FILESYSTEM) else state.focus)
    elif action == 'swap':
        state = replace(state,view_first=not state.view_first)
    elif action in ('left_visible','right_visible','edit_visible','view_visible'):
        state = replace(state,**{action: not getattr(state,action) if value is None else bool(value)})
    elif action == 'hide':
        tile = Tile(value)
        key = 'left_visible' if tile in (Tile.FILESYSTEM,Tile.CONTENTS) else f'{tile.value}_visible'
        state = replace(state,**{key:False})
    elif action in ('left_ratio','view_ratio'):
        state = replace(state,**{action:max(.1,min(.9,float(value)))})
    visible = state.visible()
    if visible and state.focus not in visible:
        state = replace(state,focus=visible[0])
    return state

# =============================================================================
# 4. Strict, data-only theme compiler and deterministic decorative programs
# =============================================================================
STATES = frozenset('focused active previous hover pressed selected disabled dirty caution destructive busy success warning error'.split())
CUES = frozenset('startup focus-change tile-show tile-hide tile-swap file-activate dirty-change save-success save-failure mode-change menu-open menu-close no-context-actions'.split()) | frozenset(f'user-{i}' for i in range(1,9))
SLOTS = frozenset({'outer','divider','spacer','behind-content'} | {f'tile.{t.value}.chrome' for t in Tile})
PATTERN_KINDS = frozenset('tile stripe checker hatch grid scanline dither scatter noise vignette bands dropout slice-displacement fragmentation colour-channel-offset rails ladders stepped-path brackets connectors'.split())
MODES = frozenset('replace underlay glyph-only foreground-only background-only mask transparent-cell dither-blend'.split())
THEME_ROOT_KEYS = frozenset('schema id name description extends seed seed_mode fps palette ramps styles geometry glyphs art motifs patterns masks layers animations rules variants fallbacks'.split())
STYLE_KEYS = frozenset('fg bg bold italic underline dim strike extends'.split())
LAYER_KEYS = frozenset('id slot art pattern motif style mode offset repeat opacity mask visible family transform'.split())
RULE_PROPERTIES = frozenset('style opacity density offset visible phase mask_threshold palette_phase'.split())
TRACK_PROPERTIES = frozenset('glyph_frame style palette_phase pattern_phase offset density mask_threshold visibility'.split())
# Spatial/shape animation can run at the theme frame cadence. Potentially flash-like
# whole-style/palette/visibility changes remain capped at two transitions per second.
FLASH_TRACK_PROPERTIES = frozenset({'style','palette_phase','visibility'})
BASE_PALETTE = {'paper':'#10151e','ink':'#d8e3ec','muted':'#718299','accent':'#6dcbd1',
                'frame':'#466374','code':'#dbbd8a','heading':'#a4dacf','link':'#82bef7',
                'warning':'#f1c26f','error':'#ff9b99'}
BASE_STYLES = {
    'background':{'fg':'@palette.ink','bg':'@palette.paper'},
    'text':{'fg':'@palette.ink','bg':'@palette.paper'},
    'muted':{'fg':'@palette.muted','bg':'@palette.paper'},
    'border':{'fg':'@palette.frame','bg':'@palette.paper'},
    'title':{'fg':'@palette.accent','bg':'@palette.paper','bold':True},
    'accent':{'fg':'@palette.accent','bg':'@palette.paper'},
    'code':{'fg':'@palette.code','bg':'@palette.paper'},
    'heading':{'fg':'@palette.heading','bg':'@palette.paper','bold':True},
    'link':{'fg':'@palette.link','bg':'@palette.paper','underline':True},
    'quote':{'fg':'@palette.muted','bg':'@palette.paper','italic':True},
    'table':{'fg':'@palette.ink','bg':'@palette.paper'},
    'diagnostic':{'fg':'@palette.warning','bg':'@palette.paper'},
    'tree':{'fg':'@palette.ink','bg':'@palette.paper'},
    'gutter':{'fg':'@palette.muted','bg':'@palette.paper'},
    'status':{'fg':'@palette.ink','bg':'@palette.paper'},
    'keyword':{'fg':'@palette.accent','bold':True},
    'string':{'fg':'@palette.heading'}, 'number':{'fg':'@palette.code'},
    'comment':{'fg':'@palette.muted','italic':True},
}
FALLBACKS = {'ascii':{'glyph':'.'},'monochrome':{},'narrow':{},'reduced_motion':{}}
MINIMAL_THEME: dict[str,Any] = {
    'schema':'md-editor-theme/1.1','id':'minimal','name':'Minimal / safe',
    'description':'Embedded safe presentation','palette':BASE_PALETTE,
    'styles':BASE_STYLES,'fallbacks':FALLBACKS,
}


def closed(value: Any, keys: Iterable[str], where: str) -> dict[str,Any]:
    if not isinstance(value,dict):
        raise ThemeError(f'{where} must be a table')
    unknown = set(value)-set(keys)
    if unknown:
        raise ThemeError(f'{where}: unknown fields {", ".join(sorted(unknown))}')
    return value


def finite_number(value: Any, lo: float, hi: float, where: str, integer: bool = False) -> float | int:
    if type(value) not in ((int,) if integer else (int,float)) or not math.isfinite(value) or not lo<=value<=hi:
        raise ThemeError(f'{where} must be {"an integer" if integer else "a number"} in [{lo}, {hi}]')
    return value


def pair(value: Any, lo: int, hi: int, where: str, length: int = 2) -> tuple[int,...]:
    if not isinstance(value,(list,tuple)) or len(value) != length:
        raise ThemeError(f'{where} must contain {length} integers')
    return tuple(int(finite_number(v,lo,hi,where,True)) for v in value)


def flag(value: Any, where: str) -> bool:
    if type(value) is not bool:
        raise ThemeError(f'{where} must be a boolean')
    return value


def ident(value: Any, where: str = 'identifier') -> str:
    if not isinstance(value,str) or not NAME_RE.fullmatch(value):
        raise ThemeError(f'Invalid {where}: {display_text(value)}')
    return value


def reference(value: Any, namespace: str) -> str:
    if not isinstance(value,str):
        raise ThemeError(f'{namespace} reference must be a string')
    prefix = '@'+namespace+'.'
    if value.startswith('@'):
        if not value.startswith(prefix):
            raise ThemeError(f'Expected {prefix}name')
        value = value[len(prefix):]
    return ident(value,namespace+' reference')


def freeze(value: Any) -> Any:
    if isinstance(value,dict):
        return MappingProxyType({k:freeze(v) for k,v in value.items()})
    if isinstance(value,list):
        return tuple(freeze(v) for v in value)
    return value


def merge_tables(base: dict[str,Any], child: dict[str,Any]) -> dict[str,Any]:
    out = copy.deepcopy(base)
    for key,value in child.items():
        if isinstance(value,dict) and isinstance(out.get(key),dict):
            out[key] = merge_tables(out[key],value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def validate_data_tree(data: Any, depth: int = 0, count: list[int] | None = None) -> None:
    count = [0] if count is None else count
    if depth > MAX_DEPTH:
        raise ThemeError('Theme nesting exceeds depth 16')
    if isinstance(data,dict):
        count[0] += len(data)
        if count[0] > MAX_THEME_OBJECTS:
            raise ThemeError('Theme exceeds 4096 named fields/objects')
        for k,v in data.items():
            validate_data_tree(k,depth+1,count); validate_data_tree(v,depth+1,count)
    elif isinstance(data,list):
        if len(data)>MAX_ART_CELLS:
            raise ThemeError('Theme array is too large')
        for v in data:
            validate_data_tree(v,depth+1,count)
    elif isinstance(data,str):
        if len(data.encode('utf-8',errors='surrogatepass'))>MAX_STRING_BYTES:
            raise ThemeError('Theme string exceeds 16 KiB')
        if any(unsafe_codepoint(c) or c in '\u200c\u200d' for c in data):
            raise ThemeError('Theme contains terminal controls, bidi controls or unsafe format characters')
    elif type(data) not in (bool,int,float):
        raise ThemeError('Theme values must be tables, arrays, strings, booleans or finite numbers')
    elif type(data) is float and not math.isfinite(data):
        raise ThemeError('Theme numbers must be finite')


def literal_glyph(value: Any) -> str:
    if not isinstance(value,str) or not GRAPHEMES.fullmatch(value) or glyph_width(value)!=1:
        raise ThemeError('Decorative glyph must be exactly one safe single-width grapheme')
    if any(unsafe_codepoint(c) or c in '\u200c\u200d' for c in value):
        raise ThemeError('Unsafe decorative glyph')
    return value


def legacy_normalize(raw: dict[str,Any]) -> dict[str,Any]:
    allowed = {'schema','id','name','description','palette','styles','geometry','textures',
               'panels','art','animations'}
    closed(raw,allowed,'legacy 1.0')
    out = copy.deepcopy(raw)
    out['schema'] = 'md-editor-theme/1.1'
    if 'textures' in out:
        out['patterns'] = out.pop('textures')
    if 'panels' in out:
        out['layers'] = out.pop('panels')
    out['fallbacks'] = copy.deepcopy(FALLBACKS)
    return out


@dataclass(frozen=True, slots=True)
class Theme:
    id: str
    name: str
    digest: str
    palette: Mapping[str,str]
    style_specs: Mapping[str,Mapping[str,Any]]
    geometry: Mapping[str,Any]
    glyphs: Mapping[str,Any]
    art: Mapping[str,tuple[tuple[str,...],...]]
    art_styles: Mapping[str,str]
    patterns: Mapping[str,Any]
    motifs: Mapping[str,Any]
    masks: Mapping[str,Any]
    layers: tuple[Mapping[str,Any],...]
    rules: tuple[Mapping[str,Any],...]
    animations: tuple[Mapping[str,Any],...]
    variants: tuple[Mapping[str,Any],...]
    fallbacks: Mapping[str,Any]
    seed: int = 0
    seed_mode: str = 'fixed'
    fps: int = 12


class ThemeCompiler:
    """Consumes only presentation data and an authorized in-memory catalog."""
    def __init__(self, catalog: Mapping[str,dict[str,Any]] | None = None):
        self.catalog = dict(catalog or {})
        self.catalog.setdefault('minimal',copy.deepcopy(MINIMAL_THEME))

    @staticmethod
    def read(path: Path) -> dict[str,Any]:
        try:
            with path.open('rb') as f:
                raw = f.read(MAX_THEME_BYTES+1)
            if len(raw)>MAX_THEME_BYTES:
                raise ThemeError('Theme exceeds 1 MiB')
            return tomllib.loads(raw.decode('utf-8'))
        except (OSError,UnicodeError,tomllib.TOMLDecodeError,RecursionError) as exc:
            raise ThemeError('Cannot read theme: '+display_text(exc)) from None

    def _inherit(self, data: dict[str,Any], trail: tuple[str,...] = ()) -> dict[str,Any]:
        validate_data_tree(data)
        if data.get('schema') == 'md-editor-theme/1.0':
            data = legacy_normalize(data)
        closed(data,THEME_ROOT_KEYS,'theme')
        if data.get('schema') != 'md-editor-theme/1.1':
            raise ThemeError('Unsupported theme schema')
        tid = ident(data.get('id'),'theme ID')
        if tid in trail:
            raise ThemeError('Theme inheritance cycle: '+' -> '.join(trail+(tid,)))
        if len(trail)>8:
            raise ThemeError('Theme inheritance exceeds eight ancestors')
        parent = data.get('extends')
        if parent is None:
            return copy.deepcopy(data)
        parent = reference(parent,'themes')
        if parent not in self.catalog:
            raise ThemeError('Unknown parent theme '+parent)
        base = self._inherit(self.catalog[parent],trail+(tid,))
        child = copy.deepcopy(data); child.pop('extends',None)
        return merge_tables(base,child)

    def compile(self, raw: dict[str,Any]) -> Theme:
        try:
            return self._compile(raw)
        except ThemeError:
            raise
        except (TypeError,AttributeError,KeyError,ValueError,RecursionError,OverflowError) as exc:
            raise ThemeError('Invalid theme data shape: '+display_text(exc)) from None

    def _compile(self, raw: dict[str,Any]) -> Theme:
        data = self._inherit(raw)
        validate_data_tree(data)
        name = data.get('name')
        if not isinstance(name,str) or not name.strip() or len(name)>128:
            raise ThemeError('Theme name must be a nonempty string of at most 128 characters')
        palette_raw = dict(BASE_PALETTE)
        for key,value in closed(data.get('palette',{}),data.get('palette',{}).keys() if isinstance(data.get('palette',{}),dict) else [],'palette').items():
            palette_raw[ident(key)] = value
        palette: dict[str,str] = {}

        def colour(value: Any, trail: tuple[str,...] = ()) -> str:
            if isinstance(value,str) and COLOUR_RE.fullmatch(value):
                return value.lower()
            key = reference(value,'palette')
            if key in trail or len(trail)>16:
                raise ThemeError('Palette reference cycle or excessive depth')
            if key not in palette_raw:
                raise ThemeError('Unknown palette entry '+key)
            result = colour(palette_raw[key],trail+(key,))
            palette[key] = result
            return result

        for key in palette_raw:
            colour('@palette.'+key)
        ramps = data.get('ramps',{})
        if not isinstance(ramps,dict):
            raise ThemeError('ramps must be a table')
        for key,item in ramps.items():
            ident(key); closed(item,{'colours','steps'},'ramp '+key)
            values = item.get('colours')
            if not isinstance(values,list) or not 2<=len(values)<=16:
                raise ThemeError('Ramp requires 2–16 colours')
            colours = [rgb(colour(v)) for v in values]
            steps = int(finite_number(item.get('steps',16),2,256,'ramp steps',True))
            for i in range(steps):
                pos = i*(len(colours)-1)/(steps-1)
                a = min(len(colours)-2,int(pos)); phase = pos-a
                c = tuple(round(x+(y-x)*phase) for x,y in zip(colours[a],colours[a+1]))
                palette[f'{key}.{i}'] = '#%02x%02x%02x'%c
                if len(palette)>MAX_THEME_OBJECTS: raise ThemeError('Expanded palette exceeds 4096 named objects')
        styles_raw = copy.deepcopy(BASE_STYLES)
        raw_styles = data.get('styles',{})
        if not isinstance(raw_styles,dict):
            raise ThemeError('styles must be a table')
        styles_raw.update(raw_styles)
        styles: dict[str,dict[str,Any]] = {}

        def resolve_style(key: str, trail: tuple[str,...] = ()) -> dict[str,Any]:
            if key in styles:
                return styles[key]
            if key in trail or len(trail)>16:
                raise ThemeError('Style inheritance cycle or excessive reference depth')
            if key not in styles_raw:
                raise ThemeError('Unknown style '+key)
            item = closed(styles_raw[key],STYLE_KEYS,'style '+key)
            result: dict[str,Any] = {}
            if 'extends' in item:
                result.update(resolve_style(reference(item['extends'],'styles'),trail+(key,)))
            for field_,value in item.items():
                if field_ == 'extends':
                    continue
                if field_ in ('fg','bg'):
                    if isinstance(value,str) and COLOUR_RE.fullmatch(value):
                        result[field_] = value.lower()
                    else:
                        ref = reference(value,'palette')
                        if ref not in palette:
                            raise ThemeError('Unknown palette colour '+ref)
                        result[field_] = '@palette.'+ref
                else:
                    result[field_] = flag(value,'style '+field_)
            styles[key] = result
            return result
        for key in styles_raw:
            ident(key); resolve_style(key)

        def style_ref(value: Any) -> str:
            key = reference(value,'styles')
            if key not in styles:
                raise ThemeError('Unknown style '+key)
            return key

        geometry = closed(data.get('geometry',{}),{'outer','border','divider','spacer'},'geometry')
        geometry = {'outer':pair(geometry.get('outer',[0,0,0,0]),0,32,'geometry.outer',4),
                    **{k:int(finite_number(geometry.get(k,0),0,8,'geometry.'+k,True))
                       for k in ('border','divider','spacer')}}
        glyphs: dict[str,Any] = {}
        for key,item in data.get('glyphs',{}).items():
            ident(key); closed(item,{'symbols','mirror','rotate90'},'glyph family')
            symbols = item.get('symbols',[])
            if not isinstance(symbols,list) or not 1<=len(symbols)<=256:
                raise ThemeError('Glyph family requires 1–256 symbols')
            symbols = [literal_glyph(g) for g in symbols]
            normalized: dict[str,Any] = {'symbols':symbols}
            for transform in ('mirror','rotate90'):
                if transform in item:
                    mapping = item[transform]
                    if not isinstance(mapping,dict) or set(mapping)!=set(symbols):
                        raise ThemeError('Transform maps must explicitly cover the glyph family')
                    normalized[transform] = {literal_glyph(k):literal_glyph(v) for k,v in mapping.items()}
            glyphs[key] = normalized
        art: dict[str,tuple[tuple[str,...],...]] = {}
        art_styles: dict[str,str] = {}
        decoded = 0
        for key,item in data.get('art',{}).items():
            ident(key); closed(item,{'rows','grid','rle','legend','style'},'art '+key)
            representations = [k for k in ('rows','grid','rle') if k in item]
            if len(representations)!=1:
                raise ThemeError('Art requires exactly one of rows, grid or rle')
            representation = representations[0]
            rows = item[representation]
            if not isinstance(rows,list) or not 1<=len(rows)<=256:
                raise ThemeError('Art height must be 1–256')
            legend = item.get('legend',{})
            if not isinstance(legend,dict) or len(legend)>256:
                raise ThemeError('Invalid art legend')
            legend = {ident(k):literal_glyph(v) for k,v in legend.items()}
            decoded_rows = []
            for row in rows:
                if not isinstance(row,str):
                    raise ThemeError('Art rows must be strings')
                if representation == 'rows':
                    cells = [literal_glyph(m.group()) for m in GRAPHEMES.finditer(row)]
                elif representation == 'grid':
                    tokens = row.split()
                    if any(t not in legend for t in tokens):
                        raise ThemeError('Unknown art token')
                    cells = [legend[t] for t in tokens]
                else:
                    cells = []
                    for run in row.split():
                        match = re.fullmatch(r'([1-9][0-9]{0,5}):([a-z][a-z0-9_.-]{0,63})',run)
                        if not match or match[2] not in legend:
                            raise ThemeError('Malformed RLE run or unknown token')
                        count = int(match[1])
                        if len(cells)+count>256:
                            raise ThemeError('RLE row exceeds 256 cells')
                        cells.extend([legend[match[2]]]*count)
                if not 1<=len(cells)<=256:
                    raise ThemeError('Art width must be 1–256')
                decoded += len(cells)
                if decoded>MAX_ART_CELLS:
                    raise ThemeError('Decoded art exceeds 262144 cells')
                decoded_rows.append(tuple(cells))
            row_width = max(map(len,decoded_rows))
            decoded += sum(row_width-len(row) for row in decoded_rows)
            if decoded>MAX_ART_CELLS: raise ThemeError('Padded art exceeds 262144 cells')
            art[key] = tuple(row+(' ',)*(row_width-len(row)) for row in decoded_rows)
            art_styles[key] = style_ref(item.get('style','border'))
        patterns: dict[str,Any] = {}
        for key,item in data.get('patterns',{}).items():
            ident(key); closed(item,{'kind','glyphs','style','period','density','phase','amplitude'},'pattern '+key)
            kind = item.get('kind','tile')
            if kind not in PATTERN_KINDS:
                raise ThemeError('Unknown pattern kind '+str(kind))
            symbols = item.get('glyphs',['.',' '])
            if not isinstance(symbols,list) or not 1<=len(symbols)<=32:
                raise ThemeError('Pattern needs 1–32 glyphs')
            patterns[key] = {'kind':kind, 'glyphs':tuple(literal_glyph(g) for g in symbols),
                             'style':style_ref(item.get('style','border')),
                             'period':pair(item.get('period',[4,4]),1,256,'pattern period'),
                             'density':finite_number(item.get('density',.5),0,1,'pattern density'),
                             'phase':finite_number(item.get('phase',0),-100000,100000,'pattern phase'),
                             'amplitude':finite_number(item.get('amplitude',3),0,256,'pattern amplitude')}
        def known(value: Any, namespace: str, mapping: Mapping[str,Any]) -> str:
            key = reference(value,namespace)
            if key not in mapping:
                raise ThemeError(f'Unknown {namespace} object {key}')
            return key
        motifs: dict[str,Any] = {}
        for key,item in data.get('motifs',{}).items():
            ident(key); closed(item,{'art','pattern','gap'},'motif '+key)
            if ('art' in item)==('pattern' in item):
                raise ThemeError('Motif requires exactly one art/pattern source')
            source = 'art' if 'art' in item else 'pattern'
            motifs[key] = {source:known(item[source],source if source=='art' else 'patterns',art if source=='art' else patterns),
                           'gap':pair(item.get('gap',[0,0]),0,256,'motif gap')}
        masks: dict[str,Any] = {}
        for key,item in data.get('masks',{}).items():
            ident(key); closed(item,{'pattern','threshold','invert'},'mask '+key)
            masks[key] = {'pattern':known(item.get('pattern'),'patterns',patterns),
                          'threshold':finite_number(item.get('threshold',.5),0,1,'mask threshold'),
                          'invert':flag(item.get('invert',False),'mask invert')}
        raw_layers = data.get('layers',[])
        if not isinstance(raw_layers,list) or len(raw_layers)>64:
            raise ThemeError('Scene must have at most 64 layers')
        layers: list[dict[str,Any]] = []
        layer_ids: set[str] = set()
        for item in raw_layers:
            closed(item,LAYER_KEYS,'layer')
            key = ident(item.get('id'),'layer ID')
            if key in layer_ids:
                raise ThemeError('Duplicate layer ID '+key)
            layer_ids.add(key)
            if item.get('slot') not in SLOTS:
                raise ThemeError('Layer targets an undeclared or functional slot')
            source = [k for k in ('art','pattern','motif') if k in item]
            if len(source)!=1:
                raise ThemeError('Layer needs exactly one art/pattern/motif')
            src = source[0]
            source_id = known(item[src],{'art':'art','pattern':'patterns','motif':'motifs'}[src],
                              {'art':art,'pattern':patterns,'motif':motifs}[src])
            mode = item.get('mode','transparent-cell')
            if mode not in MODES:
                raise ThemeError('Unsafe or unknown composition mode')
            transform = item.get('transform','none')
            if transform not in ('none','mirror','rotate90'):
                raise ThemeError('Unknown glyph transformation')
            family = known(item['family'],'glyphs',glyphs) if 'family' in item else ''
            if transform!='none' and (not family or transform not in glyphs[family]):
                raise ThemeError('Transform requires an explicit matching glyph-family map')
            layers.append({'id':key,'slot':item['slot'],src:source_id,
                           'style':style_ref(item['style']) if 'style' in item else '',
                           'mode':mode,'offset':pair(item.get('offset',[0,0]),-512,512,'layer offset'),
                           'repeat':flag(item.get('repeat',True),'layer repeat'),
                           'opacity':finite_number(item.get('opacity',1),0,1,'layer opacity'),
                           'mask':known(item['mask'],'masks',masks) if 'mask' in item else '',
                           'visible':flag(item.get('visible',True),'layer visibility'),
                           'family':family,'transform':transform})
        def target(value: Any) -> str:
            if not isinstance(value,str) or not value.startswith('layer.') or value[6:] not in layer_ids:
                raise ThemeError('Target must name a declared layer: layer.<id>')
            return value[6:]
        def property_value(prop: str, value: Any) -> Any:
            if prop=='style': return style_ref(value)
            if prop=='offset': return pair(value,-512,512,'animated offset')
            if prop in ('visible','visibility'): return flag(value,'visibility')
            if prop in ('opacity','density','mask_threshold'): return finite_number(value,0,1,prop)
            if prop in ('phase','pattern_phase'): return finite_number(value,-100000,100000,prop)
            if prop in ('palette_phase','glyph_frame'): return int(finite_number(value,0,255,prop,True))
            raise ThemeError('Unknown decorative property')
        rules: list[dict[str,Any]] = []
        raw_rules = data.get('rules',[])
        if not isinstance(raw_rules,list) or len(raw_rules)>256:
            raise ThemeError('At most 256 rules are allowed')
        for item in raw_rules:
            closed(item,{'target','states','cue','set'},'rule')
            states = item.get('states',[])
            if not isinstance(states,list) or any(s not in STATES for s in states):
                raise ThemeError('Invalid rule state')
            cue = item.get('cue','')
            if cue and cue not in CUES:
                raise ThemeError('Unknown host cue')
            props = closed(item.get('set',{}),RULE_PROPERTIES,'rule set')
            rules.append({'target':target(item.get('target')),'states':tuple(states),'cue':cue,
                          'set':{k:property_value(k,v) for k,v in props.items()}})
        animations: list[dict[str,Any]] = []
        animation_ids: set[str] = set()
        raw_animations = data.get('animations',[])
        if not isinstance(raw_animations,list) or len(raw_animations)>32:
            raise ThemeError('At most 32 animation tracks are allowed')
        for item in raw_animations:
            closed(item,{'id','target','property','values','duration','loop','cue'},'animation')
            aid = ident(item.get('id'),'animation ID')
            if aid in animation_ids:
                raise ThemeError('Duplicate animation ID')
            animation_ids.add(aid)
            prop = item.get('property')
            if prop not in TRACK_PROPERTIES:
                raise ThemeError('Unknown animation property')
            values = item.get('values',[])
            if not isinstance(values,list) or not 1<=len(values)<=256:
                raise ThemeError('Animation needs 1–256 frames')
            cue = item.get('cue','ambient')
            if cue!='ambient' and cue not in CUES:
                raise ThemeError('Unknown animation cue')
            loop = flag(item.get('loop',cue=='ambient'),'animation loop')
            if cue!='ambient' and loop:
                raise ThemeError('Event bursts must be finite')
            animations.append({'id':aid,'target':target(item.get('target')),'property':prop,
                               'values':tuple(property_value(prop,v) for v in values),
                               'duration':finite_number(item.get('duration',4),.5,3600,'animation duration'),
                               'loop':loop,'cue':cue})
        def palette_overlay(values: Any) -> dict[str,str]:
            if not isinstance(values,dict):
                raise ThemeError('Palette override must be a table')
            result = {}
            for k,v in values.items():
                if k not in palette or not isinstance(v,str) or not COLOUR_RE.fullmatch(v):
                    raise ThemeError('Palette override must map known entries to hex colours')
                result[k]=v.lower()
            return result
        def layer_list(value: Any) -> tuple[str,...]:
            if not isinstance(value,list) or any(v not in layer_ids for v in value):
                raise ThemeError('Fallback/variant must name declared decorative layers')
            return tuple(value)
        variants: list[dict[str,Any]] = []
        raw_variants = data.get('variants',[])
        if not isinstance(raw_variants,list) or len(raw_variants)>32:
            raise ThemeError('At most 32 responsive variants')
        for item in raw_variants:
            closed(item,{'when','palette','hide_layers','show_layers'},'variant')
            when = closed(item.get('when',{}),{'min_width','max_width','colour_depth','unicode','motion','high_contrast'},'variant conditions')
            for k,v in when.items():
                if k in ('min_width','max_width'): finite_number(v,1,10000,k,True)
                elif k in ('unicode','high_contrast'): flag(v,k)
                elif k=='colour_depth' and v not in ('truecolour','256','16','mono'): raise ThemeError('Invalid colour depth variant')
                elif k=='motion' and v not in ('off','reduced','normal','high'): raise ThemeError('Invalid motion variant')
            variants.append({'when':when,'palette':palette_overlay(item.get('palette',{})),
                             'hide_layers':layer_list(item.get('hide_layers',[])),
                             'show_layers':layer_list(item.get('show_layers',[]))})
        fallbacks = closed(data.get('fallbacks',{}),FALLBACKS.keys(),'fallbacks')
        if set(fallbacks)!=set(FALLBACKS):
            raise ThemeError('Portable themes require ascii, monochrome, narrow and reduced_motion fallbacks')
        normalized_fallbacks: dict[str,Any] = {}
        for key,item in fallbacks.items():
            closed(item,{'glyph','hide_layers','palette'},'fallback '+key)
            g = literal_glyph(item.get('glyph','.'))
            if key=='ascii' and not (' '<=g<='~'):
                raise ThemeError('ASCII fallback glyph must be printable ASCII')
            normalized_fallbacks[key] = {'glyph':g,'hide_layers':layer_list(item.get('hide_layers',[])),
                                         'palette':palette_overlay(item.get('palette',{}))}
        seed = int(finite_number(data.get('seed',7),0,2**32-1,'theme seed',True))
        seed_mode = data.get('seed_mode','fixed')
        if seed_mode not in ('fixed','session','phased'):
            raise ThemeError('Invalid seed mode')
        fps = int(finite_number(data.get('fps',12),1,30,'theme FPS',True))
        digest = hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
        return Theme(data['id'],name,digest,freeze(palette),freeze(styles),freeze(geometry),
                     freeze(glyphs),freeze(art),freeze(art_styles),freeze(patterns),freeze(motifs),
                     freeze(masks),freeze(layers),freeze(rules),freeze(animations),freeze(variants),
                     freeze(normalized_fallbacks),seed,seed_mode,fps)


class ThemeCatalog:
    def __init__(self, directories: Sequence[Path]):
        self.raw: dict[str,dict[str,Any]] = {'minimal':copy.deepcopy(MINIMAL_THEME)}
        self.compiled: dict[str,Theme] = {}
        self.diagnostics: list[str] = []
        for directory in directories:
            try:
                files = sorted(directory.glob('*.theme'))[:256]
            except OSError as exc:
                self.diagnostics.append(display_text(exc)); continue
            for path in files:
                try:
                    raw = ThemeCompiler.read(path)
                    tid = ident(raw.get('id'),'theme ID')
                    if tid in self.raw:
                        raise ThemeError('Duplicate stable theme ID '+tid)
                    self.raw[tid] = raw
                except (ThemeError,OSError) as exc:
                    self.diagnostics.append(display_text(path.name)+': '+display_text(exc))
        compiler = ThemeCompiler(self.raw)
        for tid,raw in self.raw.items():
            try:
                self.compiled[tid] = compiler.compile(raw)
            except (ThemeError,TypeError,AttributeError,KeyError,ValueError,RecursionError) as exc:
                self.diagnostics.append(tid+': '+display_text(exc))
        # Compiling embedded data is deterministic and does not depend on user files.
        self.compiled.setdefault('minimal',ThemeCompiler().compile(copy.deepcopy(MINIMAL_THEME)))

    def get(self, tid: str) -> Theme:
        return self.compiled.get(tid,self.compiled['minimal'])


@dataclass(frozen=True, slots=True)
class VisualContext:
    width: int
    height: int
    depth: str = 'truecolour'
    unicode: bool = True
    high_contrast: bool = False
    motion: str = 'normal'
    time: float = 0.0
    seed: int = -1
    session_seed: int = 17
    focus: str = 'edit'
    active: bool = True
    dirty: bool = False
    cue: str = 'startup'
    cue_time: float = 0.0
    immersive: bool = False


def pattern_sample(spec: Mapping[str,Any], x: int, y: int, w: int, h: int, seed: int,
                   phase: float = 0.0, density: float | None = None) -> tuple[str,float]:
    kind = spec['kind']; px,py = spec['period']; symbols = spec['glyphs']
    p = int(phase+spec['phase']); den = spec['density'] if density is None else density
    n = noise32(x,y,seed)/0xffffffff
    index = (x+y+p)%len(symbols)
    visible, value = True, n
    if kind=='tile':
        index=(x%px+(y%py)*px+p)%len(symbols); value=1.0
    elif kind=='stripe': visible=(x+p)%px<max(1,round(px*den)); value=1.0 if visible else 0.0
    elif kind=='checker': visible=((x//px+y//py+p)%2)==0; value=float(visible)
    elif kind=='hatch': visible=(x+y+p)%px==0; value=float(visible)
    elif kind=='grid': visible=x%px==0 or y%py==0; value=float(visible)
    elif kind=='scanline': visible=(y+p)%py==0; value=float(visible)
    elif kind=='dither': visible=((x%4)*4+y%4)/16<den; value=((x%4)*4+y%4)/16
    elif kind in ('scatter','noise'): visible=n<den; index=noise32(x,y,seed+1)%len(symbols)
    elif kind=='vignette':
        value=max(abs(2*x/max(1,w-1)-1),abs(2*y/max(1,h-1)-1)); visible=n<value*den
    elif kind=='bands': visible=(y//py+p)%len(symbols)!=0; index=(y//py+p)%len(symbols); value=float(visible)
    elif kind=='dropout': visible=noise32(x//px,y//py,seed+p)/0xffffffff>den
    elif kind=='slice-displacement':
        shift=int((noise32(0,y//py,seed+p)/0xffffffff-.5)*spec['amplitude']*2)
        visible=(x+shift)%px==0 or y%py==0; index=(x+shift+p)%len(symbols)
    elif kind=='fragmentation':
        visible=noise32(x//px,y//py,seed)/0xffffffff<den and (x+y+p)%3!=0
        index=noise32(x//px,y//py,seed+1)%len(symbols)
    elif kind=='colour-channel-offset': visible=(x+p)%px<2 or y%py==0; index=(x+p)%len(symbols)
    elif kind=='rails': visible=y%py in (0,py-1); index=0
    elif kind=='ladders': visible=x%px in (0,px-1) or y%py==0; index=(x%px not in (0,px-1))%len(symbols)
    elif kind=='stepped-path': visible=y%py==(x//px+p)%py or x%px==0 and y%py<((x//px+p)%py); index=(x//px)%len(symbols)
    elif kind=='brackets': visible=x%px in (0,px-1) and y%py not in (py//2,) or y%py in (0,py-1) and x%px<2
    elif kind=='connectors': visible=y%py==py//2 or x%px==px//2; index=(x%px==px//2)%len(symbols)
    return (symbols[index] if visible else ' ',value)


class ThemePainter:
    def __init__(self, theme: Theme):
        self.theme = theme
        self._style_cache: dict[tuple[Any,...],tuple[dict[str,Ink],set[str],set[str],str]] = {}

    def resolve(self, ctx: VisualContext) -> tuple[dict[str,Ink],set[str],set[str],str]:
        key = (ctx.width,ctx.depth,ctx.unicode,ctx.high_contrast,ctx.motion)
        if key in self._style_cache:
            return self._style_cache[key]
        palette = dict(self.theme.palette); hidden: set[str] = set(); shown: set[str] = set()
        for item in self.theme.variants:
            when = item['when']
            match = all((ctx.width>=v if k=='min_width' else ctx.width<=v if k=='max_width'
                         else ctx.depth==v if k=='colour_depth' else ctx.unicode==v if k=='unicode'
                         else ctx.motion==v if k=='motion' else ctx.high_contrast==v)
                        for k,v in when.items())
            if match:
                palette.update(item['palette']); hidden.update(item['hide_layers']); shown.update(item['show_layers'])
                hidden.difference_update(item['show_layers'])
        fallbacks = []
        if not ctx.unicode: fallbacks.append('ascii')
        if ctx.depth=='mono': fallbacks.append('monochrome')
        if ctx.width<60: fallbacks.append('narrow')
        if ctx.motion in ('off','reduced'): fallbacks.append('reduced_motion')
        for name in fallbacks:
            item = self.theme.fallbacks[name]; palette.update(item['palette']); hidden.update(item['hide_layers'])
        if ctx.high_contrast:
            palette.update({'paper':'#000000','ink':'#ffffff','muted':'#c0c0c0','frame':'#ffffff'})
        styles = {}
        for role,spec in self.theme.style_specs.items():
            values = dict(spec)
            for prop in ('fg','bg'):
                if prop in values and values[prop].startswith('@palette.'):
                    values[prop] = palette[values[prop][9:]]
            if ctx.high_contrast:
                values['dim'] = False
            styles[role] = Ink(**values)
        result = (styles,hidden,shown,self.theme.fallbacks['ascii']['glyph'])
        if len(self._style_cache)>32:
            self._style_cache.clear()
        self._style_cache[key]=result
        return result

    def layer_properties(self, layer: Mapping[str,Any], ctx: VisualContext) -> dict[str,Any]:
        props = dict(layer)
        tile = layer['slot'].split('.')[1] if layer['slot'].startswith('tile.') else ''
        states = {'active'}
        if tile==ctx.focus: states.add('focused')
        if ctx.dirty: states.add('dirty')
        for rule in self.theme.rules:
            if rule['target']!=layer['id'] or not set(rule['states']).issubset(states): continue
            if rule['cue'] and (rule['cue']!=ctx.cue or ctx.time-ctx.cue_time>1): continue
            props.update(rule['set'])
        if ctx.motion in ('off','reduced') or not ctx.active or tile and tile!=ctx.focus:
            return props
        for track in self.theme.animations:
            if track['target']!=layer['id']: continue
            elapsed = ctx.time
            if track['cue']!='ambient':
                if track['cue']!=ctx.cue: continue
                elapsed=max(0,ctx.time-ctx.cue_time)
            if not track['loop'] and elapsed>=track['duration']: continue
            # Spatial motion is allowed to use the theme's bounded frame cadence; only
            # potentially flash-like whole-style/palette/visibility tracks are forced
            # through the <=2 Hz transition gate. This keeps crawler/moth/aurora motion
            # fluid without weakening the host's anti-flash policy.
            sample_hz = 2.0 if track['property'] in FLASH_TRACK_PROPERTIES else float(min(30,self.theme.fps))
            if ctx.motion == 'high' and track['property'] not in FLASH_TRACK_PROPERTIES:
                sample_hz = float(min(30,max(self.theme.fps,18)))
            sampled=math.floor(elapsed*sample_hz)/sample_hz
            phase=(sampled%track['duration'])/track['duration']
            value=track['values'][min(len(track['values'])-1,int(phase*len(track['values'])))]
            prop={'pattern_phase':'phase','visibility':'visible'}.get(track['property'],track['property'])
            props[prop]=value
        return props

    def paint(self, canvas: Canvas, geometry: Geometry, ctx: VisualContext) -> dict[str,Ink]:
        styles,hidden,shown,ascii_glyph = self.resolve(ctx)
        seed = ctx.seed if ctx.seed>=0 else self.theme.seed
        if ctx.seed<0 and self.theme.seed_mode=='session': seed ^= ctx.session_seed
        if ctx.seed<0 and self.theme.seed_mode=='phased' and ctx.motion not in ('off','reduced'):
            seed ^= int(ctx.time*2)
        for layer in self.theme.layers:
            if canvas.operations>=canvas.budget:
                canvas.budget_exhausted=True; break
            props = self.layer_properties(layer,ctx)
            if layer['id'] in hidden or not (props['visible'] or layer['id'] in shown): continue
            slot = layer['slot']
            if slot=='behind-content' and not ctx.immersive: continue
            gap=(0,0)
            source=dict(props)
            if 'motif' in source:
                motif=self.theme.motifs[source['motif']]; source.update(motif); gap=motif['gap']
            if 'art' in source:
                art=self.theme.art[source['art']]
                aw,ah=len(art[0]),len(art)
                source_style=self.theme.art_styles[source['art']]
            else:
                pattern=self.theme.patterns[source['pattern']]
                aw,ah=pattern['period']; source_style=pattern['style']
            ink=styles.get(props.get('style') or source_style,styles['border'])
            palette_phase=props.get('palette_phase',0)
            if palette_phase:
                ramp=[c for k,c in self.theme.palette.items() if re.search(r'\.\d+$',k)]
                if ramp: ink=replace(ink,fg=ramp[palette_phase%len(ramp)])
            mask=self.theme.masks.get(props.get('mask',''))
            for rect in geometry.slots.get(slot,()):
                r=rect.intersect(canvas.bounds)
                for y in range(r.y,r.bottom):
                    if canvas.operations>=canvas.budget: break
                    for x in range(r.x,r.right):
                        if canvas.operations>=canvas.budget: break
                        canvas.operations+=1
                        lx,ly=x-rect.x-props['offset'][0],y-rect.y-props['offset'][1]
                        if not props['repeat'] and not (0<=lx<aw and 0<=ly<ah): continue
                        if 'art' in source:
                            tx,ty=lx%(aw+gap[0]),ly%(ah+gap[1])
                            if tx>=aw or ty>=ah: continue
                            if props['transform']=='mirror': tx=aw-1-tx
                            if props['transform']=='rotate90':
                                # Rotation is clipped to the source extent, not a functional layout mutation.
                                tx,ty=ty%aw,ah-1-(tx%ah)
                            glyph=art[ty][tx]
                        else:
                            glyph,_=pattern_sample(pattern,lx,ly,rect.w,rect.h,seed,
                                                   props.get('phase',0),props.get('density'))
                            if 'glyph_frame' in props and glyph!=' ':
                                glyph=pattern['glyphs'][props['glyph_frame']%len(pattern['glyphs'])]
                        if mask:
                            _,value=pattern_sample(self.theme.patterns[mask['pattern']],lx,ly,rect.w,rect.h,seed)
                            keep=value>=props.get('mask_threshold',mask['threshold'])
                            if mask['invert']: keep=not keep
                            if not keep: continue
                        if props['transform']!='none':
                            mapping=self.theme.glyphs[props['family']][props['transform']]
                            glyph=mapping.get(glyph,' ')
                        if not ctx.unicode and any(ord(c)>126 for c in glyph): glyph=ascii_glyph
                        canvas.operations-=1
                        canvas.put(x,y,glyph,ink,0,props['mode'],props['opacity'],seed)
        return styles

# =============================================================================
# 5. Geometry solver: operational minima first, decoration second, one-cell hits
# =============================================================================

def solve_geometry(width: int, height: int, state: LayoutState, config: Config,
                   theme: Theme) -> Geometry:
    width,height=max(1,int(width)),max(1,int(height))
    viewport=Rect(0,0,width,height)
    available=Rect(0,min(1,height),width,max(0,height-2))
    visible=state.visible()
    if not visible:
        return Geometry(viewport,available,{}, {}, {}, {'outer':(available,)},False,(0,0,0,0),0)
    columns=int(state.left_visible)+int(state.right_visible and (state.edit_visible or state.view_visible))
    rows=2 if state.right_visible and state.edit_visible and state.view_visible else 1
    minw,minh=config.get('layout','min_width'),config.get('layout','min_height')
    maxima=config.get('artspace','maximum')
    tm=[min(theme.geometry['outer'][i],maxima[i]) for i in range(4)]
    um=[min(config.get('artspace','outer')[i],max(0,maxima[i]-tm[i])) for i in range(4)]
    bmax=config.get('artspace','band_maximum')
    tb,td,ts=[min(theme.geometry[k],bmax) for k in ('border','divider','spacer')]
    ub,ud,us=[min(config.get('artspace',k+'_extra'),max(0,bmax-t))
              for k,t in zip(('border','divider','spacer'),(tb,td,ts))]

    def requirements() -> tuple[int,int]:
        b,d,s=tb+ub,td+ud,ts+us
        margins=[t+u for t,u in zip(tm,um)]
        needed_w=columns*(minw+2*(1+b))+(1+2*d if columns==2 else 0)+margins[1]+margins[3]+2*s
        needed_h=rows*(minh+2*(1+b))+(1+2*d if rows==2 else 0)+margins[0]+margins[2]+2*s
        return needed_w,needed_h

    # The loops are bounded by validated decoration maxima (<400 iterations).
    for which in ('user','theme'):
        for _ in range(400):
            nw,nh=requirements()
            need_w,need_h=nw>available.w,nh>available.h
            if not need_w and not need_h: break
            margins=um if which=='user' else tm
            candidates=([1,3] if need_w else [])+([0,2] if need_h else [])
            nonzero=[i for i in candidates if margins[i]>0]
            if nonzero:
                margins[max(nonzero,key=lambda i:margins[i])]-=1
            elif which=='user' and any((us,ub,ud)):
                if us: us-=1
                elif ub: ub-=1
                else: ud-=1
            elif which=='theme' and any((ts,tb,td)):
                if ts: ts-=1
                elif tb: tb-=1
                else: td-=1
            else: break
    nw,nh=requirements()
    compact=nw>available.w or nh>available.h
    if compact:
        tm=um=[0,0,0,0]; tb=td=ts=ub=ud=us=0
    margins=tuple(t+u for t,u in zip(tm,um))
    top,right,bottom,left=margins
    outer_workspace=Rect(left,available.y+top,max(0,width-left-right),max(0,available.h-top-bottom))
    spacer=ts+us
    workspace=outer_workspace.inset(spacer)
    border=tb+ub; divider_band=td+ud
    tiles: dict[Tile,Rect]={}; dividers: dict[str,Rect]={}
    if compact:
        tile=state.focus if state.focus in visible else visible[0]
        tiles[tile]=workspace
    else:
        hasleft=state.left_visible
        hasright=state.right_visible and (state.edit_visible or state.view_visible)
        left_rect=workspace; right_rect=workspace
        if hasleft and hasright:
            gap=1+2*divider_band
            content_width=max(0,workspace.w-gap)
            minimum=minw+2*(1+border)
            lw=max(minimum,min(content_width-minimum,round(content_width*state.left_ratio)))
            left_rect=Rect(workspace.x,workspace.y,lw,workspace.h)
            dx=left_rect.right+divider_band
            dividers['left_ratio']=Rect(dx,workspace.y,1,workspace.h)
            right_rect=Rect(left_rect.right+gap,workspace.y,content_width-lw,workspace.h)
        if hasleft: tiles[state.left_tile]=left_rect
        if hasright:
            if state.edit_visible and state.view_visible:
                gap=1+2*divider_band
                free=right_rect.h-gap; minimum=minh+2*(1+border)
                vh=max(minimum,min(free-minimum,round(free*state.view_ratio)))
                first,second=(Tile.VIEW,Tile.EDIT) if state.view_first else (Tile.EDIT,Tile.VIEW)
                first_h=vh if state.view_first else free-vh
                tiles[first]=Rect(right_rect.x,right_rect.y,right_rect.w,first_h)
                dy=right_rect.y+first_h+divider_band
                dividers['view_ratio']=Rect(right_rect.x,dy,right_rect.w,1)
                tiles[second]=Rect(right_rect.x,right_rect.y+first_h+gap,right_rect.w,free-first_h)
            else:
                tiles[Tile.EDIT if state.edit_visible else Tile.VIEW]=right_rect
    contents={tile:r.inset(1+border) for tile,r in tiles.items()}
    slots: dict[str,tuple[Rect,...]]={
        'outer':(
            Rect(0,available.y,width,top),Rect(0,available.bottom-bottom,width,bottom),
            Rect(0,available.y+top,left,max(0,available.h-top-bottom)),
            Rect(width-right,available.y+top,right,max(0,available.h-top-bottom))),
        'behind-content':tuple(contents.values()),
        'spacer':(Rect(outer_workspace.x,outer_workspace.y,outer_workspace.w,spacer),
                  Rect(outer_workspace.x,outer_workspace.bottom-spacer,outer_workspace.w,spacer),
                  Rect(outer_workspace.x,outer_workspace.y,spacer,outer_workspace.h),
                  Rect(outer_workspace.right-spacer,outer_workspace.y,spacer,outer_workspace.h)),
    }
    decor_dividers=[]
    for axis,r in dividers.items():
        if axis=='left_ratio':
            decor_dividers.extend((Rect(r.x-divider_band,r.y,divider_band,r.h),Rect(r.right,r.y,divider_band,r.h)))
        else:
            decor_dividers.extend((Rect(r.x,r.y-divider_band,r.w,divider_band),Rect(r.x,r.bottom,r.w,divider_band)))
    slots['divider']=tuple(decor_dividers)
    for tile,r in tiles.items():
        b=1+border
        slots[f'tile.{tile.value}.chrome']=(Rect(r.x,r.y,r.w,b),Rect(r.x,r.bottom-b,r.w,b),
                                           Rect(r.x,r.y+b,b,max(0,r.h-2*b)),
                                           Rect(r.right-b,r.y+b,b,max(0,r.h-2*b)))
    return Geometry(viewport,workspace,MappingProxyType(tiles),MappingProxyType(contents),
                    MappingProxyType(dividers),MappingProxyType(slots),compact,margins,border)


# =============================================================================
# 6. Authoritative Unicode-aware document and bounded delta undo
# =============================================================================
@dataclass(frozen=True, slots=True)
class EditDelta:
    start: int
    deleted: str
    inserted: str
    before_cursor: int
    before_anchor: int | None
    after_cursor: int
    after_anchor: int | None

    @property
    def size(self) -> int:
        return (len(self.deleted)+len(self.inserted))*4+128


@dataclass(frozen=True, slots=True)
class Fingerprint:
    sha256: str
    size: int
    mtime_ns: int
    inode: int
    device: int


@dataclass(frozen=True, slots=True)
class LoadedFile:
    text: str
    path: Path
    fingerprint: Fingerprint
    newline: str = '\n'
    bom: bool = False
    mixed_newlines: bool = False


class DocumentModel:
    def __init__(self, text: str = '', path: Path | None = None):
        self.text=text; self.path=path; self.cursor=0; self.anchor: int | None=None
        self.generation=0; self.last_edit=time.monotonic(); self.goal_column: int | None=None
        self.newline='\n'; self.bom=False; self.fingerprint: Fingerprint | None=None
        self._line_starts=array('I',[0]); self._reindex()
        self._baseline=self._hash(); self._hash_generation=0; self._current_hash=self._baseline
        self.undo_stack: collections.deque[EditDelta]=collections.deque()
        self.redo_stack: collections.deque[EditDelta]=collections.deque()
        self.undo_bytes=0; self.max_chars=16*1024*1024
        self.diagnostics: list[str]=[]

    def _hash(self) -> str:
        return hashlib.sha256(self.text.encode('utf-8')).hexdigest()

    @property
    def digest(self) -> str:
        if self._hash_generation!=self.generation:
            self._current_hash=self._hash(); self._hash_generation=self.generation
        return self._current_hash

    @property
    def dirty(self) -> bool:
        return self.digest!=self._baseline

    def mark_saved(self, fingerprint: Fingerprint | None = None) -> None:
        self._baseline=self.digest
        if fingerprint is not None: self.fingerprint=fingerprint

    @classmethod
    def loaded(cls, loaded: LoadedFile) -> DocumentModel:
        doc=cls(loaded.text,loaded.path); doc.fingerprint=loaded.fingerprint
        doc.newline=loaded.newline; doc.bom=loaded.bom
        if loaded.mixed_newlines:
            doc.diagnostics.append('Mixed line endings: saving normalizes them to the dominant convention.')
        unsafe=sum(1 for ch in loaded.text if unsafe_codepoint(ch,True))
        if unsafe:
            doc.diagnostics.append(f'{unsafe} unsafe source control characters are visibly replaced, not deleted.')
        return doc

    def _reindex(self) -> None:
        self._line_starts=array('I',[0]); self._line_starts.extend(m.end() for m in re.finditer('\n',self.text))

    @property
    def line_count(self) -> int:
        return len(self._line_starts)

    def row_of(self, position: int | None = None) -> int:
        return bisect.bisect_right(self._line_starts,self.cursor if position is None else position)-1

    def line_start(self, row: int) -> int:
        return self._line_starts[max(0,min(self.line_count-1,row))]

    def line(self, row: int) -> str:
        if row<0 or row>=self.line_count: return ''
        start=self._line_starts[row]
        end=self._line_starts[row+1]-1 if row+1<self.line_count else len(self.text)
        return self.text[start:end]

    @property
    def selection(self) -> tuple[int,int] | None:
        if self.anchor is None or self.anchor==self.cursor: return None
        return (min(self.anchor,self.cursor),max(self.anchor,self.cursor))

    def selected_text(self) -> str:
        selection=self.selection
        return self.text[selection[0]:selection[1]] if selection else ''

    def _replace(self, start: int, end: int, inserted: str) -> None:
        if len(self.text)-(end-start)+len(inserted)>self.max_chars:
            raise MDBMError('Document edit exceeds the configured size budget.')
        self.text=self.text[:start]+inserted+self.text[end:]
        # An O(lines) compact integer index, not a list of copied source lines.
        lo=bisect.bisect_right(self._line_starts,start)
        hi=bisect.bisect_right(self._line_starts,end)
        shift=len(inserted)-(end-start)
        starts=self._line_starts[:lo]
        starts.extend(start+m.end() for m in re.finditer('\n',inserted))
        starts.extend(v+shift for v in self._line_starts[hi:])
        self._line_starts=starts
        self.generation+=1; self.last_edit=time.monotonic(); self.goal_column=None

    def replace_range(self, start: int, end: int, inserted: str,
                      cursor: int | None = None, anchor: int | None = None) -> None:
        start,end=max(0,min(len(self.text),start)),max(0,min(len(self.text),end))
        if end<start: start,end=end,start
        after=start+len(inserted) if cursor is None else cursor
        delta=EditDelta(start,self.text[start:end],inserted,self.cursor,self.anchor,after,anchor)
        if delta.deleted==delta.inserted:
            self.cursor=max(0,min(len(self.text),after)); self.anchor=anchor; return
        self._replace(start,end,inserted)
        self.cursor=max(0,min(len(self.text),after)); self.anchor=anchor
        self.undo_stack.append(delta); self.undo_bytes+=delta.size; self.redo_stack.clear()
        while self.undo_stack and (self.undo_bytes>MAX_UNDO_BYTES or len(self.undo_stack)>MAX_UNDO_STEPS):
            self.undo_bytes-=self.undo_stack.popleft().size

    def insert(self, text: str, sanitize: bool = True) -> None:
        text=clean_paste(text) if sanitize else text
        start,end=self.selection or (self.cursor,self.cursor)
        self.replace_range(start,end,text)

    def undo(self) -> bool:
        if not self.undo_stack: return False
        d=self.undo_stack.pop(); self.undo_bytes-=d.size
        self._replace(d.start,d.start+len(d.inserted),d.deleted)
        self.cursor=d.before_cursor; self.anchor=d.before_anchor; self.redo_stack.append(d)
        return True

    def redo(self) -> bool:
        if not self.redo_stack: return False
        d=self.redo_stack.pop()
        self._replace(d.start,d.start+len(d.deleted),d.inserted)
        self.cursor=d.after_cursor; self.anchor=d.after_anchor; self.undo_stack.append(d); self.undo_bytes+=d.size
        return True

    def boundary(self, direction: int) -> int:
        row=self.row_of(); start=self.line_start(row); line=self.line(row); column=self.cursor-start
        if direction<0:
            if column==0: return max(0,self.cursor-1)
            previous=0
            for m in GRAPHEMES.finditer(line):
                if m.end()>=column: return start+m.start()
                previous=m.end()
            return start+previous
        if column>=len(line): return min(len(self.text),self.cursor+1)
        for m in GRAPHEMES.finditer(line):
            if m.end()>column: return start+m.end()
        return min(len(self.text),self.cursor+1)

    def move_to(self, position: int, selecting: bool = False) -> None:
        position=max(0,min(len(self.text),int(position)))
        row=self.row_of(position); start=self.line_start(row); offset=position-start
        if offset:
            for match in GRAPHEMES.finditer(self.line(row)):
                if match.start()<offset<match.end():
                    position=start+match.start(); break
                if match.start()>=offset: break
        if selecting:
            if self.anchor is None: self.anchor=self.cursor
        else: self.anchor=None
        self.cursor=position

    def move(self, direction: str, selecting: bool = False, ascii_only: bool = False,
             tab_size: int = 4, page: int = 10) -> None:
        row=self.row_of(); start=self.line_start(row); line=self.line(row)
        if not selecting and self.selection and direction in ('left','right'):
            self.cursor=self.selection[0 if direction=='left' else 1]; self.anchor=None; return
        target=self.cursor
        if direction in ('left','right'):
            target=self.boundary(-1 if direction=='left' else 1); self.goal_column=None
        elif direction in ('up','down','pageup','pagedown'):
            goal=self.goal_column if self.goal_column is not None else width_of(line[:self.cursor-start],ascii_only,tab_size)
            delta={'up':-1,'down':1,'pageup':-page,'pagedown':page}[direction]
            rr=max(0,min(self.line_count-1,row+delta))
            target=self.line_start(rr)+cell_to_offset(self.line(rr),goal,ascii_only,tab_size)
            self.goal_column=goal
        elif direction=='home': target=start; self.goal_column=None
        elif direction=='end': target=start+len(line); self.goal_column=None
        elif direction=='top': target=0
        elif direction=='bottom': target=len(self.text)
        elif direction=='word-right':
            match=regex.search(r'\w+\b|[^\w\s]+',self.text[self.cursor:])
            target=self.cursor+match.end() if match else len(self.text)
        elif direction=='word-left':
            matches=list(regex.finditer(r'\w+|[^\w\s]+',self.text[:self.cursor]))
            target=matches[-1].start() if matches else 0
        self.move_to(target,selecting)

    def delete(self, backward: bool = False) -> None:
        if self.selection:
            self.insert(''); return
        other=self.boundary(-1 if backward else 1)
        self.replace_range(min(other,self.cursor),max(other,self.cursor),'')

    def select_word(self, position: int) -> None:
        row=self.row_of(position); start=self.line_start(row); offset=position-start
        line=self.line(row)
        if offset==len(line) and offset: offset-=1
        for match in regex.finditer(r'\w+|[^\w\s]+|\s+',line):
            if match.start()<=offset<match.end():
                self.anchor=start+match.start(); self.cursor=start+match.end(); return
        self.anchor=position; self.cursor=position

    def select_line(self, position: int) -> None:
        row=self.row_of(position)
        self.anchor=self.line_start(row)
        self.cursor=self.line_start(row+1) if row+1<self.line_count else len(self.text)

    def find(self, needle: str, reverse: bool = False, start: int | None = None) -> bool:
        if not needle: return False
        pos=self.cursor if start is None else start
        if reverse:
            found=self.text.rfind(needle,0,max(0,pos-1))
            if found<0: found=self.text.rfind(needle)
        else:
            found=self.text.find(needle,pos)
            if found<0: found=self.text.find(needle)
        if found<0: return False
        self.anchor=found; self.cursor=found+len(needle); return True


# =============================================================================
# 7. Rooted filesystem operations, conflict-safe publication, private state
# =============================================================================
class SafeFiles:
    def __init__(self, root: Path, max_bytes: int = 16*1024*1024):
        self.root=root.expanduser().resolve(strict=True)
        if not self.root.is_dir(): raise PathError('Workspace root must be a directory.')
        self.max_bytes=max_bytes
        self.protected_paths: tuple[Path,...]=()

    def resolve(self, path: str | Path, allow_root: bool = False, allow_state: bool = False) -> Path:
        p=Path(path)
        if not p.is_absolute(): p=self.root/p
        p=Path(os.path.abspath(p))
        try: relative=p.relative_to(self.root)
        except ValueError: raise PathError('Path escapes the workspace root.') from None
        if not relative.parts and not allow_root: raise PathError('The workspace root is not a file target.')
        if not allow_state and (any(part.casefold()=='.mdbm' for part in relative.parts) or any(p==root or p.is_relative_to(root) for root in getattr(self,'protected_paths',()))):
            raise PathError('MDBM recovery/state paths are protected.')
        if any(unsafe_codepoint(ch) for ch in str(relative)):
            raise PathError('Control characters in paths are not accepted.')
        if os.name=='nt' and any(':' in part or part.rstrip(' .')!=part or part.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))} for part in relative.parts):
            raise PathError('Windows device names, streams and ambiguous trailing characters are not permitted.')
        current=self.root
        for part in relative.parts:
            current=current/part
            if current.is_symlink() or getattr(current,'is_junction',lambda:False)():
                raise PathError('Symbolic links and junctions are not accepted for workspace operations.')
        if not p.resolve(strict=False).is_relative_to(self.root):
            raise PathError('Resolved path escapes the workspace root.')
        return p

    def raw_read(self, path: Path) -> tuple[bytes,Fingerprint]:
        path=self.resolve(path)
        flags=os.O_RDONLY | getattr(os,'O_BINARY',0) | getattr(os,'O_NOFOLLOW',0)
        try:
            fd=os.open(path,flags)
            with os.fdopen(fd,'rb') as f:
                st=os.fstat(f.fileno())
                if not stat.S_ISREG(st.st_mode): raise PathError('Only regular files can be opened.')
                if st.st_size>self.max_bytes: raise PathError('File exceeds the configured size limit.')
                raw=f.read(self.max_bytes+1)
                after=os.fstat(f.fileno())
            if len(raw)>self.max_bytes: raise PathError('File exceeds the configured size limit.')
            if (st.st_mtime_ns,st.st_size)!=(after.st_mtime_ns,after.st_size):
                raise ConflictError('File changed while it was being read; try again.')
            self.resolve(path)
            return raw,Fingerprint(hashlib.sha256(raw).hexdigest(),len(raw),after.st_mtime_ns,after.st_ino,after.st_dev)
        except OSError as exc:
            raise PathError(display_text(exc)) from None

    def fingerprint(self, path: Path) -> Fingerprint | None:
        p=self.resolve(path)
        if not p.exists(): return None
        return self.raw_read(p)[1]

    def load(self, path: str | Path) -> LoadedFile:
        p=self.resolve(path); raw,fp=self.raw_read(p)
        try:
            bom=raw.startswith(codecs.BOM_UTF8)
            text=raw.decode('utf-8-sig' if bom else 'utf-8')
        except UnicodeDecodeError:
            raise PathError('This is not a valid UTF-8 text file; no changes were made.') from None
        crlf=text.count('\r\n'); cr=text.count('\r')-crlf; lf=text.count('\n')-crlf
        dominant=max((crlf,'\r\n'),(lf,'\n'),(cr,'\r'),key=lambda v:v[0])[1] if any((crlf,lf,cr)) else '\n'
        normalized=text.replace('\r\n','\n').replace('\r','\n')
        return LoadedFile(normalized,p,fp,dominant,bom,sum(v>0 for v in (crlf,lf,cr))>1)

    def save(self, path: str | Path, text: str, expected: Fingerprint | None,
             newline: str = '\n', bom: bool = False) -> Fingerprint:
        p=self.resolve(path)
        if not p.parent.is_dir(): raise PathError('Destination directory does not exist.')
        raw=(codecs.BOM_UTF8 if bom else b'')+text.replace('\n',newline).encode('utf-8')
        if len(raw)>self.max_bytes: raise PathError('Encoded file exceeds the size budget.')
        current=self.fingerprint(p)
        if current!=expected:
            raise ConflictError('The destination changed on disk. Review it before overwriting.')
        fd=-1; tmp: Path | None=None
        try:
            mode=stat.S_IMODE(p.stat().st_mode) if current else 0o600
            fd,name=tempfile.mkstemp(prefix='.mdbm-save-',dir=p.parent); tmp=Path(name)
            with os.fdopen(fd,'wb') as f:
                fd=-1; f.write(raw); f.flush(); os.fsync(f.fileno())
                if hasattr(os,'fchmod'): os.fchmod(f.fileno(),mode)
            self.resolve(p)
            if self.fingerprint(p)!=expected:
                raise ConflictError('The destination changed during save; original preserved.')
            if expected is None:
                # Hard-link publication is atomic and refuses an existing destination.
                try: os.link(tmp,p)
                except FileExistsError: raise ConflictError('Destination appeared during save; original preserved.') from None
                except OSError as exc:
                    raise PathError('No-clobber atomic creation is unavailable here: '+display_text(exc)) from None
                tmp.unlink(); tmp=None
            else:
                os.replace(tmp,p); tmp=None
            self._sync_dir(p.parent)
            result=self.fingerprint(p)
            if result is None: raise PathError('Saved file unexpectedly disappeared.')
            return result
        except OSError as exc:
            raise PathError('Save failed: '+display_text(exc)) from None
        finally:
            if fd>=0: os.close(fd)
            if tmp is not None:
                with contextlib.suppress(OSError): tmp.unlink()

    @staticmethod
    def _sync_dir(path: Path) -> None:
        if os.name!='posix': return
        with contextlib.suppress(OSError):
            fd=os.open(path,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
            try: os.fsync(fd)
            finally: os.close(fd)

    def mkdir(self, path: str | Path) -> Path:
        p=self.resolve(path)
        try: p.mkdir(mode=0o700)
        except OSError as exc: raise PathError(display_text(exc)) from None
        return p

    def move(self, source: str | Path, destination: str | Path) -> Path:
        src,dst=self.resolve(source),self.resolve(destination)
        if src==dst: return src
        if dst.exists() or dst.is_symlink(): raise ConflictError('Destination already exists; move does not overwrite.')
        if not dst.parent.is_dir(): raise PathError('Destination directory does not exist.')
        if src.is_dir():
            if dst.is_relative_to(src): raise PathError('Cannot move a directory into itself.')
            try: os.rename(src,dst)
            except OSError as exc: raise PathError(display_text(exc)) from None
        else:
            raw,fp=self.raw_read(src)
            # Exact bytes preserved; no newline normalization during a move.
            self._copy_exact_exclusive(dst,raw)
            if self.fingerprint(src)!=fp:
                raise ConflictError('Source changed during move; both files retained.')
            self.resolve(src)
            try: src.unlink()
            except OSError as exc: raise PathError('Destination copied, but source could not be removed: '+display_text(exc)) from None
        return dst

    def _copy_exact_exclusive(self, dst: Path, raw: bytes) -> None:
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
        try:
            fd=os.open(self.resolve(dst),flags,0o600)
            with os.fdopen(fd,'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
        except FileExistsError: raise ConflictError('Destination already exists.') from None
        except OSError as exc: raise PathError(display_text(exc)) from None

    def trash(self, path: str | Path) -> None:
        p=self.resolve(path)
        if send2trash is None: raise PathError('Send2Trash is unavailable. Nothing was deleted.')
        try: send2trash(str(p))
        except Exception as exc: raise PathError('Trash failed; no permanent-delete fallback: '+display_text(exc)) from None

    def entries(self, path: Path) -> list[tuple[Path,bool,bool]]:
        p=self.resolve(path,allow_root=True)
        try:
            with os.scandir(p) as it:
                result=[]
                for item in it:
                    if item.name in ('.mdbm','.git','__pycache__','.pytest_cache','.venv') or item.name.startswith('.mdbm-save-'):
                        continue
                    symlink=item.is_symlink() or getattr(Path(item.path),'is_junction',lambda:False)()
                    result.append((Path(item.path),item.is_dir(follow_symlinks=False),symlink))
                    if len(result)>=20000: break
            return sorted(result,key=lambda v:(not v[1],v[0].name.casefold()))
        except OSError as exc: raise PathError(display_text(exc)) from None


def fingerprint_from_json(value: Any) -> Fingerprint | None:
    if value is None: return None
    if not isinstance(value,dict) or set(value)!={'sha256','size','mtime_ns','inode','device'}: raise MDBMError('Invalid recovery fingerprint')
    if not isinstance(value['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['sha256']): raise MDBMError('Invalid recovery digest')
    if any(type(value[k]) is not int or value[k]<0 for k in ('size','mtime_ns','inode','device')): raise MDBMError('Invalid recovery metadata')
    return Fingerprint(**value)


class StateStore:
    """Atomic private JSON, with a conservative advisory single-writer lock."""
    def __init__(self, directory: Path):
        self.directory=directory.absolute(); self.token=uuid.uuid4().hex
        self.enabled=True; self.owns_lock=False; self.diagnostics: list[str]=[]
        try:
            if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (self.directory,*self.directory.parents)):
                raise MDBMError('State directory must not be a symlink or junction.')
            self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
            self.directory=self.directory.resolve()
            lock=self.directory/'lock.json'
            if lock.exists() and not lock.is_symlink():
                existing=self.read('lock.json',4096)
                pid=existing.get('pid') if isinstance(existing,dict) else None
                alive=True
                if type(pid) is int and 1<=pid<=2**31-1:
                    try: os.kill(pid,0)
                    except ProcessLookupError: alive=False
                    except (PermissionError,OSError): pass
                if not alive:
                    with contextlib.suppress(OSError): lock.unlink()
            try:
                fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
                with os.fdopen(fd,'w',encoding='utf-8') as f:
                    json.dump({'pid':os.getpid(),'token':self.token},f)
                self.owns_lock=True
            except FileExistsError:
                self.diagnostics.append('Another instance owns session state; this instance uses an isolated recovery file.')
        except (OSError,MDBMError) as exc:
            self.enabled=False; self.diagnostics.append('Persistence unavailable: '+display_text(exc))

    @property
    def recovery_name(self) -> str:
        return 'recovery.json' if self.owns_lock else f'recovery-{self.token}.json'

    def _path(self, name: str) -> Path:
        if not re.fullmatch(r'[a-z0-9-]+\.json',name): raise MDBMError('Invalid internal state name')
        p=self.directory/name
        if p.is_symlink(): raise MDBMError('State files must not be symbolic links')
        return p

    def read(self, name: str, max_bytes: int = 1024*1024) -> Any:
        if not self.enabled: return None
        try:
            p=self._path(name)
            with p.open('rb') as f: raw=f.read(max_bytes+1)
            if len(raw)>max_bytes: raise MDBMError('State file exceeds its size limit')
            return json.loads(raw)
        except FileNotFoundError: return None
        except (OSError,ValueError,MDBMError,RecursionError) as exc:
            self.diagnostics.append('Ignoring '+name+': '+display_text(exc)); return None

    def write(self, name: str, value: Any) -> None:
        if not self.enabled: return
        p=self._path(name); tmp=None
        try:
            encoded=json.dumps(value,ensure_ascii=True,separators=(',',':')).encode('utf-8')
            fd,tmpname=tempfile.mkstemp(prefix='.state-',dir=self.directory); tmp=Path(tmpname)
            with os.fdopen(fd,'wb') as f: f.write(encoded); f.flush(); os.fsync(f.fileno())
            self._path(name); os.replace(tmp,p); tmp=None; SafeFiles._sync_dir(self.directory)
        except OSError as exc: raise MDBMError('Cannot persist state: '+display_text(exc)) from None
        finally:
            if tmp is not None:
                with contextlib.suppress(OSError): tmp.unlink()

    def recovery(self, doc: DocumentModel, root: Path) -> None:
        if not doc.dirty: return
        self.write(self.recovery_name,{'schema':'mdbm-recovery/1.0','root':str(root),
            'path':str(doc.path) if doc.path else None,'text':doc.text,'digest':doc.digest,
            'baseline':doc._baseline,'fingerprint':dataclasses.asdict(doc.fingerprint) if doc.fingerprint else None,
            'cursor':doc.cursor,'anchor':doc.anchor,'newline':doc.newline,'bom':doc.bom,
            'timestamp':time.time()})

    def clear_recovery(self) -> None:
        if not self.enabled: return
        with contextlib.suppress(OSError,MDBMError): self._path(self.recovery_name).unlink(missing_ok=True)

    def recovery_candidates(self, max_bytes: int) -> list[tuple[str,dict[str,Any]]]:
        if not self.enabled: return []
        result=[]
        for p in list(self.directory.glob('recovery*.json'))[:64]:
            data=self.read(p.name,max_bytes*8+4096)
            if isinstance(data,dict) and data.get('schema')=='mdbm-recovery/1.0':
                result.append((p.name,data))
        return sorted(result,key=lambda item:item[1].get('timestamp',0) if type(item[1].get('timestamp',0)) in (int,float) else 0,reverse=True)

    def close(self) -> None:
        if self.enabled and self.owns_lock:
            existing=self.read('lock.json',4096)
            if isinstance(existing,dict) and existing.get('token')==self.token:
                with contextlib.suppress(OSError): self._path('lock.json').unlink()
            self.owns_lock=False

# =============================================================================
# 8. Markdown tokens, semantic spans, heading index and bidirectional source map
# =============================================================================
@dataclass(frozen=True, slots=True)
class Heading:
    title: str
    level: int
    source_line: int
    slug: str


@dataclass(frozen=True, slots=True)
class PreviewBlock:
    kind: str
    start: int
    end: int
    spans: tuple[Span,...]
    anchor: str = ''
    cells: tuple[tuple[Span,...],...] = ()
    table_id: int = -1


@dataclass(frozen=True, slots=True)
class ParsedMarkdown:
    generation: int
    blocks: tuple[PreviewBlock,...]
    headings: tuple[Heading,...]
    diagnostics: tuple[str,...] = ()


@dataclass(frozen=True, slots=True)
class PreviewRow:
    spans: tuple[Span,...]
    start: int
    end: int
    block: int
    anchor: str = ''


@dataclass(frozen=True, slots=True)
class PreviewLayout:
    generation: int
    width: int
    rows: tuple[PreviewRow,...]
    source_lines: tuple[int,...]
    source_rows: tuple[int,...]
    anchors: Mapping[str,int]

    def source_to_row(self, line: int) -> int:
        i=max(0,bisect.bisect_right(self.source_lines,line)-1)
        return self.source_rows[i] if self.source_rows else 0


BARE_URL = re.compile(r'https?://[^\s<>]+')
FOOTNOTE_TEXT = re.compile(r'\[\^([^\]\n]+)\]')


def heading_slug(text: str) -> str:
    text=unicodedata.normalize('NFKC',text).casefold()
    return regex.sub(r'[^\p{L}\p{N}_\-]+','-',text).strip('-') or 'heading'


def append_span(spans: list[Span], span: Span) -> None:
    if not span.text: return
    if spans and len(spans[-1].text)+len(span.text)<=4096 and replace(spans[-1],text='')==replace(span,text=''):
        spans[-1]=replace(spans[-1],text=spans[-1].text+span.text)
    else: spans.append(span)


class MarkdownEngine:
    @staticmethod
    def parser() -> MarkdownIt:
        md=MarkdownIt('commonmark',{'html':False,'maxNesting':32}).enable(['table','strikethrough'])
        if HAVE_MDIT_PLUGINS:
            md.use(footnote_plugin).use(tasklists_plugin,enabled=True)
        return md

    @staticmethod
    def inline(children: Sequence[Any], default_role: str = 'text', fallback_notes: bool = False) -> tuple[Span,...]:
        spans: list[Span]=[]; bold=italic=strike=0; links: list[str]=[]
        for token in children:
            typ=token.type
            if typ=='strong_open': bold+=1; continue
            if typ=='strong_close': bold=max(0,bold-1); continue
            if typ=='em_open': italic+=1; continue
            if typ=='em_close': italic=max(0,italic-1); continue
            if typ=='s_open': strike+=1; continue
            if typ=='s_close': strike=max(0,strike-1); continue
            if typ=='link_open': links.append(token.attrGet('href') or ''); continue
            if typ=='link_close':
                if links: links.pop()
                continue
            role='link' if links else default_role; link=links[-1] if links else ''
            value=token.content
            if typ=='code_inline': role='code'
            elif typ=='softbreak': value=' '
            elif typ=='hardbreak': value='\n'
            elif typ=='image': value='[image: '+(token.content or 'no description')+']'; role='muted'; link=''
            elif typ=='footnote_ref':
                ident_=int((token.meta or {}).get('id',0)); value=f'[{ident_+1}]'; role='link'; link=f'#fn-{ident_+1}'
            elif typ=='footnote_anchor': continue
            elif typ=='html_inline' and value.startswith('<input'):
                value='[x] ' if 'checked' in value else '[ ] '
            elif typ not in ('text','code_inline','softbreak','hardbreak','image','html_inline'):
                if not value: continue
            base=Span(value,role,link,bool(bold),bool(italic),bool(strike))
            if typ=='text' and not links:
                matches=[]
                for m in BARE_URL.finditer(value):
                    url=m.group().rstrip('.,;:!?)')
                    if url: matches.append((m.start(),m.start()+len(url),url,url))
                if fallback_notes:
                    matches.extend((m.start(),m.end(),'['+m[1]+']','#fn-'+heading_slug(m[1])) for m in FOOTNOTE_TEXT.finditer(value))
                pos=0
                for start,end,label,href in sorted(matches):
                    if start<pos: continue
                    append_span(spans,replace(base,text=value[pos:start]))
                    append_span(spans,replace(base,text=label,role='link',link=href)); pos=end
                append_span(spans,replace(base,text=value[pos:]))
            else: append_span(spans,base)
        return tuple(spans)

    def parse(self, text: str, generation: int = 0) -> ParsedMarkdown:
        md=self.parser()
        tokens=md.parse(text)
        diagnostics=[]
        if len(tokens)>MAX_PARSE_TOKENS:
            tokens=tokens[:MAX_PARSE_TOKENS]; diagnostics.append('Preview token budget reached; source remains editable.')
        source_lines=text.splitlines()
        notes: dict[str,tuple[int,int,str]]={}
        note_re=re.compile(r'^ {0,3}\[\^([^\]]+)\]:\s*(.*)$')
        for i,line in enumerate(source_lines):
            m=note_re.match(line)
            if m:
                content=[m[2]]; end=i+1
                while end<len(source_lines) and (source_lines[end].startswith('    ') or source_lines[end].startswith('\t')):
                    content.append(source_lines[end].lstrip()); end+=1
                notes[m[1]]=(i,end,' '.join(content))
        note_lines={line for a,b,_ in notes.values() for line in range(a,b)}
        blocks: list[PreviewBlock]=[]; headings: list[Heading]=[]; slug_counts: dict[str,int]={}
        current_range=(0,1); heading_level=0; quote_depth=0
        lists: list[list[Any]]=[]; prefix=''; table_id=-1; in_table=False; table_header=False
        table_cells: list[tuple[Span,...]]=[]; table_row_start=0; table_row_end=1
        footnote_anchor=''; footnote_range: tuple[int,int] | None=None
        skip_definition=False
        for token in tokens:
            typ=token.type
            if token.map:
                current_range=(token.map[0],max(token.map[0]+1,token.map[1]))
            start,end=footnote_range or current_range
            if typ=='heading_open': heading_level=int(token.tag[1:])
            elif typ=='heading_close': heading_level=0
            elif typ=='blockquote_open': quote_depth+=1
            elif typ=='blockquote_close': quote_depth=max(0,quote_depth-1)
            elif typ in ('bullet_list_open','ordered_list_open'):
                lists.append([typ=='ordered_list_open',int(token.attrGet('start') or 1)])
            elif typ in ('bullet_list_close','ordered_list_close'):
                if lists: lists.pop()
                prefix=''
            elif typ=='list_item_open':
                if lists:
                    ordered,number=lists[-1]
                    prefix='  '*(len(lists)-1)+(f'{number}. ' if ordered else '• ')
                    lists[-1][1]+=1
            elif typ=='list_item_close': prefix=''
            elif typ=='table_open': table_id+=1; in_table=True
            elif typ=='table_close': in_table=False
            elif typ=='thead_open': table_header=True
            elif typ=='thead_close': table_header=False
            elif typ=='tr_open': table_cells=[]; table_row_start=start; table_row_end=end
            elif typ=='tr_close':
                blocks.append(PreviewBlock('table-header' if table_header else 'table',table_row_start,table_row_end,
                                           (),cells=tuple(table_cells),table_id=table_id))
            elif typ=='footnote_block_open':
                blocks.append(PreviewBlock('heading',max(0,len(source_lines)-1),len(source_lines),
                                           (Span('Footnotes','heading',bold=True),),'footnotes'))
            elif typ=='footnote_open':
                meta=token.meta or {}; nid=int(meta.get('id',0)); label=str(meta.get('label',''))
                footnote_anchor=f'fn-{nid+1}'
                if label in notes: footnote_range=notes[label][:2]
                elif nid<len(notes): footnote_range=list(notes.values())[nid][:2]
                else: footnote_range=(max(0,len(source_lines)-1),len(source_lines))
                prefix=f'[{nid+1}] '
            elif typ=='footnote_close': footnote_anchor=''; footnote_range=None; prefix=''
            elif typ=='inline':
                if not HAVE_MDIT_PLUGINS and start in note_lines:
                    continue
                role='heading' if heading_level else 'quote' if quote_depth else 'text'
                spans=self.inline(token.children or [],role,not HAVE_MDIT_PLUGINS)
                if in_table:
                    table_cells.append(spans); continue
                if not HAVE_MDIT_PLUGINS and prefix:
                    # CommonMark keeps the task marker as literal text in bootstrap mode.
                    pass
                leading=('│ '*quote_depth)+prefix
                if leading: spans=(Span(leading,'quote' if quote_depth else 'accent'),)+spans
                anchor=footnote_anchor
                kind='heading' if heading_level else 'quote' if quote_depth else 'list' if lists else 'paragraph'
                if heading_level:
                    title=''.join(s.text for s in spans).strip(); slug=heading_slug(title)
                    count=slug_counts.get(slug,0); slug_counts[slug]=count+1
                    anchor=slug if count==0 else f'{slug}-{count}'
                    headings.append(Heading(title,heading_level,start,anchor))
                blocks.append(PreviewBlock(kind,start,end,spans,anchor))
                prefix=''; footnote_anchor=''
            elif typ in ('fence','code_block'):
                language=(token.info.split() or ['text'])[0]
                try: lexer=get_lexer_by_name(language,stripnl=False,ensurenl=False)
                except Exception: lexer=TextLexer(stripnl=False,ensurenl=False)
                if len(token.content)>262144:
                    lexer=TextLexer(stripnl=False,ensurenl=False)
                if typ=='fence':
                    blocks.append(PreviewBlock('code-label',start,start+1,(Span('┌ '+language,'muted'),)))
                lines: list[list[Span]]=[[]]
                for tt,value in lex(token.content,lexer):
                    role='comment' if tt in Token.Comment else 'keyword' if tt in Token.Keyword else 'string' if tt in Token.Literal.String else 'number' if tt in Token.Literal.Number else 'code'
                    for j,part in enumerate(value.split('\n')):
                        if j: lines.append([])
                        if part: append_span(lines[-1],Span(part,role))
                if lines and not lines[-1]: lines.pop()
                for i,spans in enumerate(lines):
                    line_no=min(end-1,start+(1 if typ=='fence' else 0)+i)
                    blocks.append(PreviewBlock('code',line_no,line_no+1,tuple(spans) or (Span(' ','code'),)))
                if typ=='fence': blocks.append(PreviewBlock('code-label',max(start,end-1),end,(Span('└','muted'),)))
            elif typ=='hr': blocks.append(PreviewBlock('rule',start,end,(Span('─'*24,'border'),)))
            elif typ=='html_block': blocks.append(PreviewBlock('paragraph',start,end,(Span(token.content),)))
        if not HAVE_MDIT_PLUGINS and notes:
            blocks.append(PreviewBlock('heading',min(v[0] for v in notes.values()),len(source_lines),
                                       (Span('Footnotes','heading',bold=True),),'footnotes'))
            for label,(start,end,body) in notes.items():
                parsed=md.parseInline(body)
                spans=self.inline(parsed[0].children or [],fallback_notes=True) if parsed else (Span(body),)
                blocks.append(PreviewBlock('footnote',start,end,(Span('['+label+'] ','accent'),)+spans,'fn-'+heading_slug(label)))
        if not blocks: blocks.append(PreviewBlock('empty',0,1,(Span('Nothing to preview yet. Start writing in Edit.','muted'),)))
        return ParsedMarkdown(generation,tuple(blocks),tuple(headings),tuple(diagnostics))


def wrap_spans(spans: Sequence[Span], width: int, ascii_only: bool = False,
               preserve: bool = False, max_rows: int = MAX_PREVIEW_ROWS) -> list[tuple[Span,...]]:
    width=max(1,width); max_rows=max(0,max_rows)
    if not max_rows: return []
    rows: list[tuple[Span,...]]=[]; row: list[Span]=[]; used=0
    def flush() -> bool:
        nonlocal row,used
        rows.append(tuple(row)); row=[]; used=0
        return len(rows)>=max_rows
    for span in spans:
        pieces=re.split(r'(\n|[ \t]+)',span.text) if not preserve else [span.text]
        for piece in pieces:
            if not piece: continue
            if piece=='\n':
                if flush(): return rows
                continue
            space=piece.isspace() and '\n' not in piece
            pw=width_of(piece,ascii_only)
            if not preserve and not space and pw<=width and used and used+pw>width:
                if flush(): return rows
            if not preserve and space:
                if used and used<width:
                    n=min(pw,width-used); append_span(row,replace(span,text=' '*n)); used+=n
                continue
            chunk=[]
            for unit in display_units(piece,ascii_only):
                if unit.glyph=='�' and piece[unit.start:unit.end]=='\n':
                    if chunk: append_span(row,replace(span,text=''.join(chunk))); chunk=[]
                    if flush(): return rows
                    continue
                if used+unit.width>width:
                    if chunk: append_span(row,replace(span,text=''.join(chunk))); chunk=[]
                    if flush(): return rows
                if unit.width>width:
                    chunk.append('?'); used+=1
                else:
                    chunk.append(unit.glyph); used+=unit.width
            if chunk: append_span(row,replace(span,text=''.join(chunk)))
    if (row or not rows) and len(rows)<max_rows: rows.append(tuple(row))
    return rows


def make_preview_layout(parsed: ParsedMarkdown, width: int, ascii_only: bool = False) -> PreviewLayout:
    width=max(1,width)
    table_widths: dict[int,list[int]]={}
    for block in parsed.blocks:
        if block.cells:
            values=table_widths.setdefault(block.table_id,[1]*len(block.cells))
            for i,cell in enumerate(block.cells[:len(values)]):
                values[i]=max(values[i],min(40,width_of(''.join(s.text for s in cell),ascii_only)))
    for key,values in table_widths.items():
        budget=max(len(values),width-3*len(values)-1)
        while sum(values)>budget and max(values)>1:
            i=max(range(len(values)),key=lambda j:values[j]); values[i]-=1
    rows: list[PreviewRow]=[]; source: dict[int,int]={}; anchors={}
    previous_kind=''
    for bi,block in enumerate(parsed.blocks):
        if len(rows)>=MAX_PREVIEW_ROWS:
            break
        if rows and block.kind in ('heading','paragraph','quote','footnote') and previous_kind not in ('list','code-label'):
            rows.append(PreviewRow((),block.start,block.end,bi))
        source.setdefault(block.start,len(rows))
        if block.anchor: anchors[block.anchor]=len(rows)
        if block.cells and width>=4*len(block.cells)+1:
            widths=table_widths[block.table_id]
            cell_rows=[wrap_spans(cell,widths[i],ascii_only,max_rows=max(0,MAX_PREVIEW_ROWS-len(rows))) for i,cell in enumerate(block.cells)]
            for ri in range(max(map(len,cell_rows))):
                spans=[Span('| ' if ascii_only else '│ ','border')]
                for ci,lines in enumerate(cell_rows):
                    items=lines[ri] if ri<len(lines) else ()
                    spans.extend(replace(s,bold=True) if block.kind=='table-header' else s for s in items)
                    occupied=sum(width_of(s.text,ascii_only) for s in items)
                    spans.append(Span(' '*(widths[ci]-occupied)+((' |' if ascii_only else ' │') if ci==len(cell_rows)-1 else (' | ' if ascii_only else ' │ ')),'border'))
                rows.append(PreviewRow(tuple(spans),block.start,block.end,bi,block.anchor if ri==0 else ''))
            if block.kind=='table-header' and len(rows)<MAX_PREVIEW_ROWS:
                rows.append(PreviewRow((Span(('-' if ascii_only else '─')*min(width,sum(widths)+3*len(widths)+1),'border'),),block.start,block.end,bi))
        else:
            spans=block.spans
            if block.cells:
                joined=[]
                for ci,cell in enumerate(block.cells):
                    if ci: joined.append(Span(' | ','border'))
                    joined.extend(cell)
                spans=tuple(joined)
            if block.kind=='rule': spans=(Span(('-' if ascii_only else '─')*width,'border'),)
            if block.kind=='code': spans=(Span('  ','code'),)+spans
            for ri,items in enumerate(wrap_spans(spans,width,ascii_only,block.kind=='code',max(0,MAX_PREVIEW_ROWS-len(rows)))):
                rows.append(PreviewRow(items,block.start,block.end,bi,block.anchor if ri==0 else ''))
        previous_kind=block.kind
    if len(rows)>=MAX_PREVIEW_ROWS and rows:
        final=rows[-1]
        warning=wrap_spans((Span('Preview limit; source retained.','diagnostic'),),width,ascii_only,max_rows=1)[0]
        rows[-1]=replace(final,spans=warning)
    sorted_sources=sorted(source)
    return PreviewLayout(parsed.generation,width,tuple(rows),tuple(sorted_sources),
                          tuple(source[s] for s in sorted_sources),MappingProxyType(anchors))


def parse_and_layout(text: str, generation: int, width: int, ascii_only: bool) -> tuple[ParsedMarkdown,PreviewLayout]:
    parsed=MarkdownEngine().parse(text,generation)
    return parsed,make_preview_layout(parsed,width,ascii_only)


# =============================================================================
# 9. Safe link classification and explicitly allowlisted script execution
# =============================================================================
@dataclass(frozen=True, slots=True)
class LinkTarget:
    kind: str
    value: str
    fragment: str = ''


def classify_link(url: str, files: SafeFiles, current: Path | None) -> LinkTarget:
    if not url or len(url)>4096 or any(unsafe_codepoint(c) or c.isspace() for c in url):
        raise PathError('Unsafe or empty link.')
    parsed=urllib.parse.urlsplit(url)
    if parsed.username or parsed.password: raise PathError('Links containing credentials are not opened.')
    scheme=parsed.scheme.lower()
    if scheme in ('http','https'):
        if not parsed.netloc or not parsed.hostname: raise PathError('External link is missing a host.')
        return LinkTarget('external',url)
    if scheme=='mailto':
        if not parsed.path or '\r' in urllib.parse.unquote(url) or '\n' in urllib.parse.unquote(url):
            raise PathError('Unsafe mailto link.')
        return LinkTarget('external',url)
    if scheme or parsed.netloc: raise PathError('This link scheme is not permitted.')
    decoded=urllib.parse.unquote(parsed.path)
    if any(unsafe_codepoint(c) for c in decoded): raise PathError('Encoded controls in links are not permitted.')
    fragment=urllib.parse.unquote(parsed.fragment)
    if any(unsafe_codepoint(c) for c in fragment): raise PathError('Unsafe link fragment.')
    if not decoded: return LinkTarget('heading',fragment)
    if parsed.query: raise PathError('Queries are not supported on local file links.')
    base=current.parent if current else files.root
    target=files.resolve(base/decoded)
    return LinkTarget('local',str(target),fragment)


def allowed_scripts(config: Config, directory: Path = APP_DIR) -> tuple[list[Path],list[str]]:
    paths=[]; diagnostics=[]
    for name in config.get('scripts','allowlist'):
        if (Path(name).name!=name or '/' in name or '\\' in name or not name.lower().endswith('.py')
            or name.casefold()=='mdbm.py' or ':' in name):
            diagnostics.append('Rejected script allowlist entry: '+display_text(name)); continue
        path=directory/name
        if path.is_symlink() or not path.is_file() or path.resolve()==Path(__file__).resolve():
            diagnostics.append('Script is missing, symlinked, or is MDBM itself: '+display_text(name)); continue
        paths.append(path)
    return paths,diagnostics


def parse_script_args(value: str) -> list[str]:
    try: args=json.loads(value)
    except (ValueError,RecursionError): raise MDBMError('Arguments must be a JSON array, for example ["--count", "4"].') from None
    if not isinstance(args,list) or len(args)>128 or not all(isinstance(v,str) and len(v)<=4096 and '\0' not in v for v in args):
        raise MDBMError('Use at most 128 string arguments, each no longer than 4096 characters.')
    return args


class ScriptRunner:
    def __init__(self, max_bytes: int = 256*1024):
        self.max_bytes=max_bytes; self.output=''; self.truncated=False; self.process: asyncio.subprocess.Process | None=None
        self.returncode: int | None=None; self.cancelled=False; self.running=False
        self.on_change: Callable[[],None]=lambda:None

    async def run(self, path: Path, args: list[str], authorized: Sequence[Path]) -> None:
        if path not in authorized or path.is_symlink() or path.name.casefold()=='mdbm.py':
            raise MDBMError('Script is not currently allowlisted.')
        self.running=True
        kwargs: dict[str,Any]={'stdout':asyncio.subprocess.PIPE,'stderr':asyncio.subprocess.STDOUT,'cwd':str(path.parent)}
        if os.name=='posix': kwargs['start_new_session']=True
        elif os.name=='nt': kwargs['creationflags']=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
        try:
            # create_subprocess_exec uses exec-style argv, never a shell.
            self.process=await asyncio.create_subprocess_exec(sys.executable,str(path),*args,**kwargs)
            if self.cancelled: await self.cancel()
            assert self.process.stdout is not None
            decoder=codecs.getincrementaldecoder('utf-8')('replace')
            while True:
                chunk=await self.process.stdout.read(4096)
                if not chunk: break
                text=decoder.decode(chunk)
                # Output is stored with line breaks; each displayed row is sanitized again.
                safe=''.join(c if c in '\n\t' or not unsafe_codepoint(c) else '�' for c in text)
                remaining=self.max_bytes-len(self.output.encode('utf-8'))
                if remaining>0:
                    self.output+=safe.encode('utf-8')[:remaining].decode('utf-8','ignore')
                if len(safe.encode('utf-8'))>remaining: self.truncated=True
                self.on_change()
            self.returncode=await self.process.wait()
        except asyncio.CancelledError:
            await self.cancel(); raise
        except OSError as exc:
            self.output+='\nLaunch failed: '+display_text(exc); self.returncode=-1
        finally:
            self.running=False; self.on_change()

    async def cancel(self) -> None:
        self.cancelled=True
        if not self.process or self.process.returncode is not None: return
        try:
            if os.name=='posix': os.killpg(self.process.pid,signal.SIGTERM)
            else: self.process.terminate()
            try: await asyncio.wait_for(self.process.wait(),1.0)
            except asyncio.TimeoutError:
                if os.name=='posix': os.killpg(self.process.pid,signal.SIGKILL)
                else: self.process.kill()
                await self.process.wait()
        except ProcessLookupError: pass

# =============================================================================
# 10. Command registry, overlays and controller-owned asynchronous preview worker
# =============================================================================
@dataclass(frozen=True, slots=True)
class Command:
    id: str
    label: str
    callback: Callable[[Any],None]
    keys: tuple[str,...] = ()
    category: str = 'Navigate'
    keyboard_path: str = ''
    visible: bool = True


class CommandRegistry:
    def __init__(self):
        self.commands: dict[str,Command]={}

    def add(self, id_: str, label: str, callback: Callable[[Any],None], keys: Sequence[str] = (),
            category: str = 'Navigate', keyboard: str = '', visible: bool = True) -> None:
        if id_ in self.commands: raise ValueError('Duplicate command '+id_)
        self.commands[id_]=Command(id_,label,callback,tuple(keys),category,
                                  keyboard or (' / '.join(keys) if keys else 'Ctrl+P → '+label),visible)

    def invoke(self, id_: str, payload: Any = None) -> None:
        if id_ not in self.commands: raise MDBMError('Unknown command: '+id_)
        self.commands[id_].callback(payload)

    def parity_errors(self, hits: Iterable[Hit]) -> list[str]:
        return [h.command for h in hits if h.command not in self.commands or not self.commands[h.command].keyboard_path]


@dataclass(frozen=True, slots=True)
class MenuItem:
    label: str
    command: str = ''
    payload: Any = None
    children: tuple[MenuItem,...] = ()
    enabled: bool = True
    destructive: bool = False


@dataclass(slots=True)
class Overlay:
    kind: str
    title: str
    items: list[MenuItem] = field(default_factory=list)
    index: int = 0
    hover: int = -1
    scroll: int = 0
    anchor: tuple[int,int] = (2,2)
    rect: Rect = Rect(0,0,1,1)
    editor: DocumentModel | None = None
    message: str = ''
    accept: Callable[[str],None] | None = None
    cancel: Callable[[],None] | None = None
    actions: list[tuple[str,Callable[[],None] | None]] = field(default_factory=list)
    runner: ScriptRunner | None = None
    tag: str = field(default_factory=lambda:uuid.uuid4().hex)
    field_scroll: int = 0
    input_y: int = 0
    list_y: int = 0


@dataclass(slots=True)
class Capture:
    kind: str
    button: str
    tile: Tile | None = None
    axis: str = ''
    original_layout: LayoutState | None = None
    bounds: Rect = Rect(0,0,1,1)
    original_cursor: int = 0
    original_anchor: int | None = None
    anchor: int = 0
    granularity: str = 'character'
    selection_end: int = 0
    pointer: tuple[int,int] = (0,0)


class PreviewWorker:
    """One daemon worker, one replaceable pending snapshot, one latest result."""
    def __init__(self):
        import threading
        self.condition=threading.Condition(); self.pending: tuple[str,int,int,bool] | None=None
        self.result: tuple[int,int,Any] | None=None; self.stopped=False; self.busy=False
        self.thread=threading.Thread(target=self._run,name='MDBM-preview',daemon=True); self.thread.start()

    def request(self, text: str, generation: int, width: int, ascii_only: bool) -> None:
        with self.condition:
            self.pending=(text,generation,width,ascii_only); self.condition.notify()

    def _run(self) -> None:
        while True:
            with self.condition:
                self.condition.wait_for(lambda:self.pending is not None or self.stopped)
                if self.stopped: return
                job=self.pending; self.pending=None; self.busy=True
            assert job is not None
            text,generation,width,ascii_only=job
            try: result=parse_and_layout(text,generation,width,ascii_only)
            except Exception as exc: result=display_text(exc)
            with self.condition:
                if not self.stopped: self.result=(generation,width,result)
                self.busy=False

    def poll(self) -> tuple[int,int,Any] | None:
        with self.condition:
            result=self.result; self.result=None; return result

    def close(self) -> None:
        with self.condition:
            self.stopped=True; self.pending=None; self.result=None; self.condition.notify()
        if self.thread is not __import__('threading').current_thread(): self.thread.join(timeout=.1)


WELCOME_TEXT = '''# MDBM — make room for words

A Markdown workspace with a terminal-native canvas, four persistent tiles,
and a protected interaction layer.

## Start here

Press **Ctrl+O** to open a UTF-8 Markdown file, or **Ctrl+N** for a new page.
Press **Ctrl+S** to save. **Ctrl+P** opens every command; **F10** opens the ribbon.

- Click in **Edit** to place the caret and start typing.
- Drag either divider with the left or right mouse button.
- Use the **[T]** border button to switch Filesystem and Contents.
- Use **[S]** to swap View and Edit; **[x]** hides a tile.
- **Ctrl+T** always restores the workspace.

## A small preview

| Layer | Owner | Promise |
| :--- | :--- | :--- |
| Content | You | Plain UTF-8 Markdown |
| Decoration | Theme | Data, never code |
| Interaction | MDBM | Always reachable |

> A little architecture around your writing — not in its way.

- [x] CommonMark, tables and task lists
- [x] Unicode-aware editing and source navigation
- [ ] Your next idea

```python
message = "Hello from the protected plane"
print(message)
```

Try **Theme → Choose theme**. Highlight previews a theme; Enter commits it;
Escape restores the previous one. The three reference themes are restrained
monochrome, neon circuitry and a dense glitch mosaic.

[Back to the beginning](#mdbm-make-room-for-words)
'''

HELP_TEXT = '''MDBM keyboard guide

Ctrl+N  New buffer             Ctrl+O  Open file
Ctrl+S  Save                   F4       Save as
Ctrl+Q  Guarded quit           Ctrl+P  Command palette
Ctrl+T  Restore all tiles      F10     Activate ribbon
Tab / Shift+Tab               Next / previous visible tile
Alt+1 / Alt+2 / Alt+3 / Alt+4  Filesystem / Contents / Edit / View
Alt+F / E / T / N / H / S / ? Ribbon menus (also use F10)
Alt+[ / Alt+]                 Narrow / widen left column
Alt+- / Alt+=                 Shrink / grow View

Edit: arrows, Home/End, Ctrl+Home/End, Shift navigation,
Ctrl+Left/Right, Backspace/Delete, Enter and bracketed paste.
Ctrl+A select all; Ctrl+C/X/V application clipboard; Ctrl+Z/Y undo/redo.
Ctrl+F find; F3 next; F7 previous; F6 / Ctrl+R replace;
Ctrl+G go to line and optional column. Insert a literal Tab with the command palette (Edit → Insert a tab character).
Ctrl+I is indistinguishable from Tab in standard terminals.

Filesystem: Up/Down row; Left collapse/parent; Right expand;
Enter open/toggle; F2 rename/move; Delete trash (confirmation); F5 refresh.
Contents: Up/Down row, Left/Right hierarchy, Enter reveal source.
View: Up/Down or Page keys scroll; Enter follows the selected preview link;
N / P cycle links; the command palette also exposes link navigation.

Menus: arrows select, Right opens submenu, Left/Escape goes back,
Enter activates. Outside clicks close only the top transient layer.
Theme chooser: arrows preview, Enter commits, Escape rolls back.
Dialogs: Enter accepts, Escape cancels; destructive choices default to Cancel.

Vim profile: Escape Normal; i/a/o Insert; v Visual; h/j/k/l, w/b, 0/$;
x delete; y copy; p paste; u undo; Ctrl+R redo; : command palette.
This is a focused Vim subset, not a complete Vim implementation.

The application clipboard is private to MDBM. Use terminal paste for OS
clipboard input. No automatic OSC52 clipboard export is performed.
'''


class Workspace:
    def __init__(self, config: Config | None = None, path: Path | None = None,
                 theme_id: str | None = None, safe_theme: bool = False,
                 restore: bool = True, persist: bool = True):
        self.config=config or load_config()
        self.diagnostics=list(self.config.diagnostics)
        self.store=StateStore(self.config.path('application','state_directory')) if persist else None
        saved=self.store.read('session.json') if self.store and restore and self.config.get('session','restore') else None
        if self.store: self.diagnostics.extend(self.store.diagnostics)
        if not isinstance(saved,dict) or saved.get('schema')!='mdbm-session/1.0': saved={}
        root=self.config.path('application','root')
        if path is not None:
            path=path.expanduser().absolute(); root=path if path.is_dir() else path.parent
        elif isinstance(saved.get('root'),str) and len(saved['root'])<=4096:
            candidate=Path(saved['root'])
            if candidate.is_dir() and not candidate.is_symlink(): root=candidate
        try: self.files=SafeFiles(root,self.config.get('application','max_file_mib')*1024*1024)
        except (OSError,PathError) as exc:
            self.diagnostics.append('Root fallback: '+display_text(exc)); self.files=SafeFiles(Path.cwd())
        self.files.protected_paths=(self.config.path('application','state_directory').absolute(),)
        directories=[]
        for value in self.config.get('appearance','theme_directories'):
            p=Path(value).expanduser(); p=p if p.is_absolute() else self.config.base/p
            if p not in directories: directories.append(p)
        if DATA_DIR/'themes' not in directories: directories.append(DATA_DIR/'themes')
        self.catalog=ThemeCatalog(directories); self.diagnostics.extend(self.catalog.diagnostics)
        chosen='minimal' if safe_theme else theme_id or saved.get('theme',self.config.get('appearance','theme'))
        if not isinstance(chosen,str) or chosen not in self.catalog.compiled:
            self.diagnostics.append('Selected theme was unavailable; embedded Minimal is active.'); chosen='minimal'
        self.theme=self.catalog.get(chosen); self.painter=ThemePainter(self.theme)
        self._committed_theme_id=self.theme.id
        self.layout=initial_layout(self.config)
        self.memories={tile:TileMemory() for tile in Tile}
        self.contents_collapsed: set[str]=set()
        self._restore_layout(saved)
        self.doc=DocumentModel(WELCOME_TEXT)
        active=path if path is not None and not path.is_dir() else None
        if path is None and isinstance(saved.get('active_file'),str):
            with contextlib.suppress(PathError): active=self.files.resolve(saved['active_file'])
        if active is not None:
            try:
                active=self.files.resolve(active)
                self.doc=DocumentModel.loaded(self.files.load(active)) if active.exists() else DocumentModel('',active)
            except (OSError,PathError,ConflictError) as exc: self.diagnostics.append('File not opened: '+display_text(exc))
        self.doc.max_chars=self.files.max_bytes
        if path is None or (active and str(active)==saved.get('active_file')):
            position=saved.get('cursor',0); anchor=saved.get('anchor')
            if type(position) is int: self.doc.cursor=max(0,min(len(self.doc.text),position))
            if type(anchor) is int: self.doc.anchor=max(0,min(len(self.doc.text),anchor))
        if not HAVE_MDIT_PLUGINS:
            self.diagnostics.append('mdit-py-plugins is not installed: bootstrap task/footnote rendering is active. Install requirements for the release parser.')
        try:
            if tuple(map(int,importlib.metadata.version('prompt-toolkit').split('.')[:3]))<(3,0,53):
                self.diagnostics.append('prompt-toolkit is older than the declared 3.0.53 release requirement; local bootstrap mode.')
        except (ValueError,importlib.metadata.PackageNotFoundError): pass
        self.registry=CommandRegistry(); self.overlays: list[Overlay]=[]
        self.capture: Capture | None=None; self.geometry: Geometry | None=None; self.hits: list[Hit]=[]
        self.app: Application | None=None; self.worker: PreviewWorker | None=None
        self.parsed=ParsedMarkdown(-1,(PreviewBlock('empty',0,1,(Span('Preparing Markdown preview…','muted'),)),),())
        self.preview=make_preview_layout(self.parsed,40,self.config.ascii)
        self.requested_parse: tuple[int,int] | None=None; self.preview_width=40
        self.notice='Ready · Ctrl+P for commands'; self.notice_error=False
        self.clipboard=''; self.search_term=''; self.search_replacement=''; self.vim_mode='normal' if self.config.get('input','profile')=='vim' else 'insert'
        self.last_click: tuple[float,int,int,str,int]=(0,-1,-1,'',0)
        self.hover_due: tuple[str,int,float] | None=None; self.hover_close_due: tuple[str,float] | None=None
        self.ribbon_active=False; self.ribbon_index=0; self.ribbon_regions: list[tuple[str,Rect]]=[]
        self.tree_cache: list[tuple[Path,int,bool,bool]] | None=None
        self.resize_commits=0; self.action_log: collections.deque[str]=collections.deque(maxlen=256)
        self.exit_requested=False; self.clean_exit=False
        self.terminal_focused=True; self.clock=0.; self.last_tick=time.monotonic()
        self.cue='startup'; self.cue_time=0.; self.session_seed=int.from_bytes(os.urandom(4),'little')
        self.last_canvas: Canvas | None=None; self.last_damage=0; self.frame_ms: collections.deque[float]=collections.deque(maxlen=512)
        self.screen_cursor=Point(x=0,y=0); self.show_cursor=False
        self._dirty_known=self.doc.dirty; self._last_recovery_gen=-1; self._last_recovery_time=0.; self._last_session_time=0.
        self._session_signature=''; self._selected_link=0; self._view_links: list[str]=[]
        self.script_runner: ScriptRunner | None=None; self.script_task: asyncio.Task | None=None
        self._register_commands()
        if restore and self.store and self.config.get('session','restore'):
            self._offer_recovery(active)

    def _restore_layout(self, saved: Mapping[str,Any]) -> None:
        values=saved.get('layout')
        if isinstance(values,dict):
            candidate=dataclasses.asdict(self.layout)
            for key,default in candidate.items():
                value=values.get(key,default)
                if key in ('left_tile','focus'):
                    try: value=Tile(value)
                    except (ValueError,TypeError): continue
                    if key=='left_tile' and value not in (Tile.FILESYSTEM,Tile.CONTENTS): continue
                elif type(default) is bool:
                    if type(value) is not bool: continue
                elif type(default) is float:
                    if type(value) not in (int,float) or not math.isfinite(value) or not .1<=value<=.9: continue
                candidate[key]=value
            self.layout=reduce_layout(LayoutState(**candidate),'noop')
        memories=saved.get('memories',{})
        if isinstance(memories,dict):
            for tile in Tile:
                record=memories.get(tile.value,{})
                if not isinstance(record,dict): continue
                memory=self.memories[tile]
                for key in ('scroll','scroll_x','row'):
                    value=record.get(key,0)
                    if type(value) is int and 0<=value<=10000000: setattr(memory,key,value)
                if isinstance(record.get('filter'),str) and len(record['filter'])<=1024: memory.filter=record['filter']
                expanded=record.get('expanded',[])
                if isinstance(expanded,list) and len(expanded)<=4096:
                    memory.expanded={v for v in expanded if isinstance(v,str) and len(v)<=4096 and not Path(v).is_absolute() and '..' not in Path(v).parts}
                    memory.expanded.add('.')
        collapsed=saved.get('contents_collapsed',[])
        if isinstance(collapsed,list) and len(collapsed)<=4096:
            self.contents_collapsed={v for v in collapsed if isinstance(v,str) and len(v)<=256}

    def session_data(self) -> dict[str,Any]:
        return {'schema':'mdbm-session/1.0','root':str(self.files.root),
                'active_file':str(self.doc.path) if self.doc.path else None,'theme':self._committed_theme_id,
                'layout':{k:(v.value if isinstance(v,Tile) else v) for k,v in dataclasses.asdict(self.layout).items()},
                'cursor':self.doc.cursor,'anchor':self.doc.anchor,
                'memories':{t.value:{'scroll':m.scroll,'scroll_x':m.scroll_x,'row':m.row,'filter':m.filter,
                                     'expanded':sorted(m.expanded)[:4096]} for t,m in self.memories.items()},
                'contents_collapsed':sorted(self.contents_collapsed)[:4096]}

    def persist_session(self) -> None:
        if not self.store or not self.store.owns_lock: return
        record=self.session_data(); signature=json.dumps(record,sort_keys=True)
        if signature!=self._session_signature:
            self.store.write('session.json',record); self._session_signature=signature

    def _offer_recovery(self, active: Path | None) -> None:
        assert self.store is not None
        for name,record in self.store.recovery_candidates(self.files.max_bytes):
            if record.get('root')!=str(self.files.root): continue
            if active is not None and record.get('path')!=str(active): continue
            if not isinstance(record.get('text'),str) or len(record['text'])>self.files.max_bytes: continue
            def restore_record(r: dict[str,Any]=record,n: str=name) -> None:
                try:
                    self.restore_recovery(r)
                    # Successful adoption rehomes an isolated snapshot; never write source here.
                    self.store.recovery(self.doc,self.files.root)
                    if n!=self.store.recovery_name:
                        with contextlib.suppress(OSError,MDBMError): self.store._path(n).unlink()
                except (MDBMError,UnicodeError,ValueError) as exc: self.info('Recovery rejected',display_text(exc))
            def discard(n: str=name) -> None:
                with contextlib.suppress(OSError,MDBMError): self.store._path(n).unlink()
            self.confirm('Unsaved recovery found',
                         'Recover the unsaved text for '+display_text(record.get('path') or 'an untitled buffer')+'? The source file will not be overwritten.',
                         [('Restore',restore_record),('Discard snapshot',discard),('Later',None)],default=2)
            break

    def restore_recovery(self, record: Mapping[str,Any]) -> None:
        if record.get('schema')!='mdbm-recovery/1.0' or record.get('root')!=str(self.files.root):
            raise MDBMError('Recovery belongs to a different workspace.')
        text=record.get('text')
        if not isinstance(text,str) or len(text.encode('utf-8'))>self.files.max_bytes:
            raise MDBMError('Recovery source is invalid or over budget.')
        if hashlib.sha256(text.encode()).hexdigest()!=record.get('digest'):
            raise MDBMError('Recovery text failed its integrity check.')
        baseline=record.get('baseline')
        if not isinstance(baseline,str) or not re.fullmatch('[0-9a-f]{64}',baseline):
            raise MDBMError('Recovery baseline is invalid.')
        path=record.get('path')
        if path is not None and not isinstance(path,str): raise MDBMError('Recovery path is invalid.')
        doc=DocumentModel(text,self.files.resolve(path) if path else None)
        doc._baseline=baseline; doc.fingerprint=fingerprint_from_json(record.get('fingerprint'))
        doc.newline=record.get('newline','\n') if record.get('newline') in ('\n','\r\n','\r') else '\n'
        doc.bom=record.get('bom') is True
        for key in ('cursor','anchor'):
            value=record.get(key)
            if type(value) is int: setattr(doc,key,max(0,min(len(text),value)))
        self.set_document(doc,clear_recovery=False)
        self.status('Recovery restored in memory; Save still checks the old disk fingerprint.')

    def invalidate(self) -> None:
        if self.app is not None: self.app.invalidate()

    def status(self, message: str, error: bool = False) -> None:
        self.notice=display_text(message); self.notice_error=error; self.invalidate()

    def emit(self, cue: str) -> None:
        if cue in CUES: self.cue=cue; self.cue_time=self.clock

    def dispatch(self, command: str, payload: Any = None) -> None:
        before=self.doc.generation; old_doc=self.doc
        try:
            self.registry.invoke(command,payload)
            if command not in ('input.drag','overlay.hover'): self.action_log.append(command)
        except (MDBMError,OSError,ValueError) as exc:
            self.status(str(exc),True)
            if command.startswith('file.save'): self.emit('save-failure')
        if old_doc is not self.doc or self.doc.generation!=before:
            self.requested_parse=None
            if self.doc.dirty!=self._dirty_known: self.emit('dirty-change'); self._dirty_known=self.doc.dirty
            self.ensure_caret_visible()
        self.invalidate()

    def set_layout(self, action: str, value: Any = None) -> None:
        previous=self.layout
        self.layout=reduce_layout(self.layout,action,value)
        if self.layout.focus!=previous.focus: self.emit('focus-change')
        elif action=='swap': self.emit('tile-swap')
        elif action=='hide': self.emit('tile-hide')
        else: self.emit('tile-show')
        self.invalidate()

    def focus_next(self, direction: int = 1) -> None:
        visible=self.layout.visible()
        if not visible: self.set_layout('restore'); return
        index=visible.index(self.layout.focus) if self.layout.focus in visible else 0
        self.set_layout('focus',visible[(index+direction)%len(visible)])

    def set_document(self, doc: DocumentModel, clear_recovery: bool = True) -> None:
        doc.generation=self.doc.generation+1
        self.doc=doc; self.doc.max_chars=self.files.max_bytes
        for tile in (Tile.EDIT,Tile.VIEW,Tile.CONTENTS):
            self.memories[tile].scroll=0; self.memories[tile].scroll_x=0; self.memories[tile].row=0
        self.contents_collapsed.clear(); self._selected_link=-1; self._view_links=[]
        self.requested_parse=None; self._last_recovery_gen=-1; self._dirty_known=doc.dirty
        if self.store and clear_recovery: self.store.clear_recovery()
        self.emit('file-activate'); self.invalidate()

    def open_path(self, path: str | Path, fragment: str = '') -> None:
        target=self.files.resolve(path)
        if target.is_dir():
            self.change_root(target); return
        def perform() -> None:
            loaded=self.files.load(target)
            self.set_document(DocumentModel.loaded(loaded)); self.set_layout('focus',Tile.EDIT)
            if fragment:
                parsed,preview=parse_and_layout(self.doc.text,self.doc.generation,self.preview_width,self.config.ascii)
                self.accept_preview(self.doc.generation,self.preview_width,(parsed,preview))
                self.jump_anchor(fragment)
            self.status('Opened '+str(target.relative_to(self.files.root)))
        self.guard_dirty(perform)

    def guard_dirty(self, action: Callable[[],None]) -> None:
        if not self.doc.dirty: action(); return
        self.confirm('Unsaved changes','Save changes before continuing?',
                     [('Save',lambda:self.save(after=action)),('Discard',action),('Cancel',None)],default=2)

    def new_document(self) -> None:
        self.guard_dirty(lambda:(self.set_document(DocumentModel()),self.set_layout('focus',Tile.EDIT),self.status('New unsaved document')))

    def change_root(self, path: Path) -> None:
        new_files=SafeFiles(path,self.files.max_bytes)
        new_files.protected_paths=(self.config.path('application','state_directory').absolute(),)
        def perform() -> None:
            self.files=new_files; self.tree_cache=None
            self.memories[Tile.FILESYSTEM]=TileMemory(); self.set_document(DocumentModel())
            self.set_layout('focus',Tile.FILESYSTEM); self.status('Workspace root changed.')
        self.guard_dirty(perform)

    def save(self, as_new: bool = False, after: Callable[[],None] | None = None) -> None:
        if as_new or self.doc.path is None:
            initial=str(self.doc.path.relative_to(self.files.root)) if self.doc.path else 'untitled.md'
            def accept(value: str) -> None:
                target=self.files.resolve(value)
                self.perform_save(target,self.doc.fingerprint if target==self.doc.path else None,after)
            self.request_text('Save As',initial,accept,'Path relative to the workspace. Existing files require confirmation.')
        else: self.perform_save(self.doc.path,self.doc.fingerprint,after)

    def perform_save(self, path: Path, expected: Fingerprint | None, after: Callable[[],None] | None = None) -> None:
        try:
            fingerprint=self.files.save(path,self.doc.text,expected,self.doc.newline,self.doc.bom)
        except ConflictError as exc:
            observed=self.files.fingerprint(path)
            self.emit('save-failure')
            self.confirm('File conflict',str(exc)+'\nDestination: '+display_text(path),
                [('Overwrite',lambda:self.perform_save(path,observed,after)),
                 ('Save As',lambda:self.save(as_new=True,after=after)),('Cancel',None)],default=2)
            return
        self.doc.path=path; self.doc.mark_saved(fingerprint)
        if self.store: self.store.clear_recovery()
        self.tree_cache=None; self.emit('save-success'); self.status('Saved '+str(path.relative_to(self.files.root)))
        if after: after()

    def request_quit(self) -> None:
        while self.overlays: self.close_overlay()
        if self.script_runner and self.script_runner.running:
            self.confirm('Script still running','Cancel the running script before exiting?',
                         [('Cancel script',lambda:self.cancel_script_then_quit()),('Stay',None)],default=1)
            return
        self.guard_dirty(self._exit)

    def _exit(self) -> None:
        self.clean_exit=True; self.exit_requested=True
        if self.store: self.store.clear_recovery()
        if self.app is not None and self.app.is_running: self.app.exit()

    def cancel_script_then_quit(self) -> None:
        async def finish() -> None:
            if self.script_runner: await self.script_runner.cancel()
            self.guard_dirty(self._exit)
        self.create_task(finish())

    def create_task(self, coro: Any) -> asyncio.Task | None:
        if self.app is not None and self.app.is_running:
            return self.app.create_background_task(coro)
        try: return asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close(); self.status('This command requires the interactive event loop.',True); return None

    def tree_rows(self) -> list[tuple[Path,int,bool,bool]]:
        if self.tree_cache is not None: return self.tree_cache
        rows=[]; expanded=self.memories[Tile.FILESYSTEM].expanded
        def visit(path: Path, depth: int) -> None:
            if depth>32 or len(rows)>=20000: return
            try: entries=self.files.entries(path)
            except PathError as exc: self.diagnostics.append(str(exc)); return
            for p,isdir,symlink in entries:
                rows.append((p,depth,isdir,symlink))
                if len(rows)>=20000: return
                relative=str(p.relative_to(self.files.root))
                if isdir and not symlink and relative in expanded: visit(p,depth+1)
        visit(self.files.root,0); self.tree_cache=rows
        return rows

    def selected_path(self) -> Path | None:
        rows=self.tree_rows(); memory=self.memories[Tile.FILESYSTEM]
        if not rows: return None
        memory.row=max(0,min(len(rows)-1,memory.row)); return rows[memory.row][0]

    def tree_activate(self, row: int | None = None, toggle_only: bool = False) -> None:
        rows=self.tree_rows()
        if not rows: return
        memory=self.memories[Tile.FILESYSTEM]
        if row is not None: memory.row=max(0,min(len(rows)-1,row))
        p,depth,isdir,symlink=rows[max(0,min(len(rows)-1,memory.row))]
        if symlink: self.status('Symlink/junction targets are not opened.',True); return
        if isdir:
            key=str(p.relative_to(self.files.root))
            if key in memory.expanded: memory.expanded.remove(key)
            else: memory.expanded.add(key)
            self.tree_cache=None
        elif not toggle_only: self.open_path(p)

    def contents_rows(self) -> list[Heading]:
        if self.parsed.generation!=self.doc.generation: return []
        needle=self.memories[Tile.CONTENTS].filter.casefold(); rows=[]; hidden_level=7
        for h in self.parsed.headings:
            if needle:
                if needle in h.title.casefold(): rows.append(h)
                continue
            if h.level<=hidden_level: hidden_level=7
            if hidden_level<7: continue
            rows.append(h)
            if h.slug in self.contents_collapsed: hidden_level=h.level
        return rows

    def go_source(self, line: int, preserve_selection: bool = False, focus: bool = True) -> None:
        line=max(0,min(self.doc.line_count-1,line))
        if not preserve_selection or not self.doc.selection:
            self.doc.move_to(self.doc.line_start(line))
        self.memories[Tile.EDIT].scroll=max(0,line-2)
        self.memories[Tile.VIEW].scroll=self.preview.source_to_row(line)
        if focus: self.set_layout('focus',Tile.EDIT)
        self.ensure_caret_visible() if not preserve_selection or not self.doc.selection else None
        self.invalidate()

    def jump_anchor(self, slug: str) -> None:
        slug=slug.lstrip('#')
        if slug in self.preview.anchors:
            row=self.preview.anchors[slug]; self.memories[Tile.VIEW].scroll=row
            self.go_source(self.preview.rows[row].start,focus=False)
            self.set_layout('focus',Tile.VIEW)
        else: self.status('Heading target not found: '+slug,True)

    def activate_link(self, url: str) -> None:
        if self.preview.generation!=self.doc.generation:
            self.status('Preview is updating; link activation is temporarily held.'); return
        target=classify_link(url,self.files,self.doc.path)
        if target.kind=='heading': self.jump_anchor(target.value)
        elif target.kind=='local': self.open_path(target.value,target.fragment)
        else:
            self.confirm('Open external link?',target.value+'\nThe system browser will handle this address.',
                         [('Open',lambda:webbrowser.open(target.value,new=2)),('Cancel',None)],default=1)

    def ensure_caret_visible(self) -> None:
        memory=self.memories[Tile.EDIT]
        rect=self.geometry.contents.get(Tile.EDIT) if self.geometry else None
        height=rect.h if rect else 10; width=rect.w if rect else 40
        row=self.doc.row_of()
        if row<memory.scroll: memory.scroll=row
        elif row>=memory.scroll+max(1,height): memory.scroll=max(0,row-max(1,height)+1)
        line=self.doc.line(row); offset=self.doc.cursor-self.doc.line_start(row)
        col=width_of(line[:offset],self.config.ascii,self.config.get('input','tab_size'))
        gutter=min(len(str(self.doc.line_count))+2,max(0,width//3))
        free=max(1,width-gutter)
        if col<memory.scroll_x: memory.scroll_x=col
        elif col>=memory.scroll_x+free: memory.scroll_x=max(0,col-free+1)
        memory.scroll=max(0,min(memory.scroll,self.doc.line_count-1))
        self.memories[Tile.VIEW].scroll=self.preview.source_to_row(row) if self.preview.generation==self.doc.generation else self.memories[Tile.VIEW].scroll

    def accept_preview(self, generation: int, width: int, result: Any) -> bool:
        if generation!=self.doc.generation or width!=self.preview_width: return False
        if isinstance(result,str): self.status('Preview failed: '+result,True); return False
        self.parsed,self.preview=result
        self._view_links=[]; self._selected_link=-1
        self.memories[Tile.VIEW].scroll=min(self.memories[Tile.VIEW].scroll,max(0,len(self.preview.rows)-1))
        self.invalidate(); return True

    def parse_sync(self, width: int | None = None) -> None:
        self.preview_width=max(1,width or self.preview_width)
        self.accept_preview(self.doc.generation,self.preview_width,
                            parse_and_layout(self.doc.text,self.doc.generation,self.preview_width,self.config.ascii))

    # -------------------------------------------------------------------------
    # Command definitions and shared keyboard/menu/pointer actions
    # -------------------------------------------------------------------------
    def _register_commands(self) -> None:
        r=self.registry
        r.add('file.new','New document',lambda p:self.new_document(),['c-n'],'File')
        r.add('file.open','Open file…',lambda p:self.request_text('Open file','',self.open_path,'UTF-8 path relative to this workspace.'),['c-o'],'File')
        r.add('file.save','Save',lambda p:self.save(),['c-s'],'File')
        r.add('file.save_as','Save As…',lambda p:self.save(True),['f4'],'File')
        r.add('file.root','Change workspace root…',lambda p:self.request_text('Workspace root',str(self.files.root),lambda v:self.change_root(Path(v).expanduser())),[],'File')
        r.add('file.mkdir','New directory…',lambda p:self.request_text('New directory','new-folder',lambda v:(self.files.mkdir(v),self.refresh_tree())),[],'File')
        r.add('file.rename','Rename / move selected item…',lambda p:self.rename_selected(),['f2'],'File')
        r.add('file.trash','Trash selected item…',lambda p:self.trash_selected(),[],'File')
        r.add('file.quit','Quit',lambda p:self.request_quit(),['c-q'],'File')
        r.add('edit.undo','Undo',lambda p:self.doc.undo(),['c-z'],'Edit')
        r.add('edit.redo','Redo',lambda p:self.doc.redo(),['c-y'],'Edit')
        r.add('edit.copy','Copy selection',lambda p:self.copy_selection(),['c-c'],'Edit')
        r.add('edit.cut','Cut selection',lambda p:self.copy_selection(True),['c-x'],'Edit')
        r.add('edit.paste','Paste application clipboard',lambda p:self.doc.insert(self.clipboard),['c-v'],'Edit')
        r.add('edit.select_all','Select all',lambda p:self.select_all(),['c-a'],'Edit')
        r.add('edit.find','Find…',lambda p:self.request_text('Find',self.search_term,self.begin_find),['c-f'],'Edit')
        r.add('edit.find_next','Find next',lambda p:self.find_next(),['f3'],'Edit')
        r.add('edit.find_previous','Find previous',lambda p:self.find_next(True),['f7'],'Edit')
        r.add('edit.replace','Replace…',lambda p:self.replace_dialog(),['f6','c-r'],'Edit')
        r.add('edit.goto','Go to line / column…',lambda p:self.request_text('Go to line[:column]',str(self.doc.row_of()+1),self.goto_text),['c-g'],'Navigate')
        r.add('edit.insert_tab','Insert a tab character',lambda p:self.doc.insert('\t'),[],'Edit')
        r.add('edit.profile','Toggle Standard / Vim profile',lambda p:self.toggle_profile(),[],'Edit')
        r.add('tile.next','Next tile',lambda p:self.focus_next(1),['tab'],'Tile')
        r.add('tile.previous','Previous tile',lambda p:self.focus_next(-1),['s-tab'],'Tile')
        r.add('tile.restore','Restore all tiles',lambda p:self.set_layout('restore'),['c-t'],'Tile')
        r.add('tile.switch_left','Switch Filesystem / Contents',lambda p:self.set_layout('switch-left'),['escape l'],'Tile')
        r.add('tile.swap','Swap View and Edit',lambda p:self.set_layout('swap'),['escape w'],'Tile')
        r.add('tile.hide','Hide focused tile',lambda p:self.set_layout('hide',Tile(p) if p else self.layout.focus),[],'Tile')
        r.add('tile.toggle_left','Show / hide left column',lambda p:self.set_layout('left_visible'),[],'Tile')
        r.add('tile.toggle_right','Show / hide right column',lambda p:self.set_layout('right_visible'),[],'Tile')
        r.add('tile.toggle_edit','Show / hide Edit',lambda p:self.set_layout('edit_visible'),[],'Tile')
        r.add('tile.toggle_view','Show / hide View',lambda p:self.set_layout('view_visible'),[],'Tile')
        for index,tile in enumerate(Tile,1):
            r.add('tile.'+tile.value,'Focus '+tile.value.title(),lambda p,t=tile:self.set_layout('focus',t),[f'escape {index}'],'Tile')
        r.add('layout.narrow_left','Narrow left column',lambda p:self.adjust_split('left_ratio',-.03),['escape ['],'Tile')
        r.add('layout.widen_left','Widen left column',lambda p:self.adjust_split('left_ratio',.03),['escape ]'],'Tile')
        r.add('layout.shrink_view','Shrink View',lambda p:self.adjust_split('view_ratio',-.03),['escape -'],'Tile')
        r.add('layout.grow_view','Grow View',lambda p:self.adjust_split('view_ratio',.03),['escape ='],'Tile')
        r.add('navigate.refresh','Refresh filesystem',lambda p:self.refresh_tree(),['f5'],'Navigate')
        r.add('navigate.contents_filter','Filter Contents…',lambda p:self.request_text('Filter Contents',self.memories[Tile.CONTENTS].filter,self.filter_contents),[],'Navigate')
        r.add('navigate.next_link','Select next preview link',lambda p:self.select_link(1),[],'Navigate')
        r.add('navigate.previous_link','Select previous preview link',lambda p:self.select_link(-1),[],'Navigate')
        r.add('navigate.follow_link','Follow selected preview link',lambda p:self.follow_selected_link(),[],'Navigate')
        r.add('theme.choose','Choose theme…',lambda p:self.choose_theme(),[],'Theme')
        r.add('theme.minimal','Embedded safe theme',lambda p:self.set_theme('minimal'),[],'Theme')
        r.add('theme.motion','Cycle motion intensity',lambda p:self.cycle_motion(),[],'Theme')
        r.add('theme.immersive','Toggle behind-content art',lambda p:self.toggle_appearance('immersive'),[],'Theme')
        r.add('theme.high_contrast','Toggle high contrast',lambda p:self.toggle_accessibility('high_contrast'),[],'Theme')
        r.add('theme.ascii','Toggle ASCII-only display',lambda p:self.toggle_accessibility('ascii_only'),[],'Theme')
        r.add('script.choose','Choose allowlisted script…',lambda p:self.open_menu('Scripts'),[],'Scripts')
        r.add('script.cancel','Cancel running script',lambda p:self.cancel_script(),[],'Scripts')
        r.add('help.shortcuts','Keyboard and mouse guide',lambda p:self.info('MDBM guide',HELP_TEXT),['f1'],'Help')
        r.add('help.diagnostics','Diagnostics',lambda p:self.info('Diagnostics',self.diagnostics_text()),[],'Help')
        r.add('help.about','About MDBM',lambda p:self.info('About MDBM',f'MDBM {VERSION}\nSingle runtime source: mdbm.py\nThemes are data, never code.\nSee docs/IMPLEMENTATION_REPORT.md for tested scope.'),[],'Help')
        r.add('command.palette','Command palette',lambda p:self.open_palette(),['c-p'],'Help')
        r.add('menu.ribbon','Activate ribbon',lambda p:self.activate_ribbon(),['f10'],'Help')
        for name,key in (('File','f'),('Edit','e'),('Tile','t'),('Navigate','n'),('Theme','h'),('Scripts','s'),('Help','?')):
            r.add('menu.'+name.lower(),'Open '+name+' menu',lambda p,n=name:self.open_menu(n),['escape '+key],'Help',visible=False)
        directions={'left':'left','right':'right','up':'up','down':'down','home':'home','end':'end',
                    'pageup':'pageup','pagedown':'pagedown','top':'c-home','bottom':'c-end',
                    'word-left':'c-left','word-right':'c-right'}
        for direction,key in directions.items():
            r.add('navigate.'+direction.replace('-','_'),'Move '+direction,
                  lambda p,d=direction:self.navigate(d),[key],'Navigate',visible=False)
        for direction in ('left','right','up','down','home','end'):
            r.add('edit.select_'+direction,'Extend selection '+direction,
                  lambda p,d=direction:self.move_doc(d,True),['s-'+direction],'Edit',visible=False)
        r.add('edit.backspace','Backspace',lambda p:self.doc.delete(True),['backspace'],'Edit',visible=False)
        r.add('edit.delete','Delete',lambda p:self.delete_action(),['delete'],'Edit',visible=False)
        r.add('input.enter','Activate / newline',lambda p:self.enter_action(),['enter'],'Navigate',visible=False)
        r.add('input.escape','Dismiss / cancel one layer',lambda p:self.escape(),['escape'],'Help',visible=False)
        # Explicit input commands carry coordinate payloads. Keyboard equivalents
        # are actual navigation/menus, not fictitious unparameterized palette entries.
        r.add('edit.point','Place caret',lambda p:self.edit_point(p),[],keyboard='Arrows / Ctrl+G',visible=False)
        r.add('tree.point','Select filesystem row',lambda p:self.tree_point(p),[],keyboard='Filesystem Up/Down',visible=False)
        r.add('tree.toggle','Expand filesystem row',lambda p:self.tree_activate(p['row'],True),[],keyboard='Filesystem Left/Right',visible=False)
        r.add('contents.point','Reveal heading source',lambda p:self.contents_point(p),[],keyboard='Contents Up/Down, Enter',visible=False)
        r.add('contents.toggle','Expand heading',lambda p:self.contents_toggle(p['row']),[],keyboard='Contents Left/Right',visible=False)
        r.add('view.point','Reveal preview source',lambda p:self.view_point(p),[],keyboard='Ctrl+G / Contents Enter',visible=False)
        r.add('view.link','Activate preview link',lambda p:self.activate_link(p['url']),[],keyboard='Navigate → Follow selected preview link',visible=False)
        r.add('tile.border','Focus / tile context menu',lambda p:self.border_point(p),[],keyboard='Alt+1..4 / Tile menu',visible=False)
        r.add('tile.context','Tile context menu',lambda p:self.tile_context(Tile(p) if p else self.layout.focus),[],keyboard='Tile menu / Ctrl+P → Tile context menu')
        r.add('layout.capture','Begin split resize',lambda p:self.begin_resize(p),[],keyboard='Alt+[ / Alt+] / Alt+- / Alt+=',visible=False)
        r.add('input.drag','Captured pointer motion',lambda p:self.drag(p),[],keyboard='Shift+Arrows / split-size commands',visible=False)
        r.add('input.release','Commit captured pointer',lambda p:self.release(p),[],keyboard='Split-size commands commit immediately',visible=False)
        r.add('tile.scroll','Scroll tile',lambda p:self.scroll_tile(Tile(p['tile']),p['delta']),[],keyboard='PageUp/PageDown / Up/Down',visible=False)
        r.add('overlay.row','Activate overlay row',lambda p:self.overlay_row(p),[],keyboard='Overlay Up/Down, Enter',visible=False)
        r.add('overlay.accept','Accept dialog',lambda p:self.overlay_accept(),[],keyboard='Enter',visible=False)
        r.add('overlay.cancel','Cancel dialog',lambda p:self.escape(),[],keyboard='Escape',visible=False)
        r.add('overlay.action','Choose confirmation action',lambda p:self.overlay_action(p),[],keyboard='Left/Right, Enter',visible=False)
        r.add('overlay.field','Place dialog caret',lambda p:self.overlay_field(p),[],keyboard='Dialog Left/Right/Home/End',visible=False)
        r.add('script.run','Run allowlisted script',lambda p:self.script_arguments(Path(p)),[],keyboard='Scripts menu',visible=False)
        for i in range(1,9):
            r.add(f'cue.user-{i}',f'Visual cue {i}',lambda p,n=i:self.emit(f'user-{n}'),[],'Theme')

    def adjust_split(self, axis: str, amount: float) -> None:
        self.set_layout(axis,getattr(self.layout,axis)+amount); self.resize_commits+=1

    def copy_selection(self, cut: bool = False) -> None:
        text=self.doc.selected_text()
        if not text: self.status('No editor selection to copy.'); return
        self.clipboard=text
        if cut: self.doc.insert('')
        self.status(f'{"Cut" if cut else "Copied"} {len(text)} characters to the application clipboard.')

    def select_all(self) -> None:
        self.doc.anchor=0; self.doc.cursor=len(self.doc.text); self.ensure_caret_visible()

    def begin_find(self, value: str) -> None:
        self.search_term=value
        if not self.doc.find(value): self.status('Text not found.',True)
        else: self.set_layout('focus',Tile.EDIT); self.ensure_caret_visible()

    def find_next(self, reverse: bool = False) -> None:
        if not self.search_term: self.dispatch('edit.find'); return
        start=self.doc.selection[0] if reverse and self.doc.selection else self.doc.cursor
        if not self.doc.find(self.search_term,reverse,start): self.status('Text not found.',True)
        else: self.set_layout('focus',Tile.EDIT); self.ensure_caret_visible()

    def replace_dialog(self) -> None:
        def have_needle(needle: str) -> None:
            if not needle: self.status('Find text must not be empty.',True); return
            self.search_term=needle
            def have_replacement(value: str) -> None:
                self.search_replacement=value
                count=self.doc.text.count(needle)
                def one() -> None:
                    if not self.doc.selection or self.doc.selected_text()!=needle:
                        if not self.doc.find(needle): self.status('Text not found.'); return
                    self.doc.insert(value); self.requested_parse=None; self.ensure_caret_visible()
                def all_() -> None:
                    self.doc.replace_range(0,len(self.doc.text),self.doc.text.replace(needle,value),cursor=0)
                    self.requested_parse=None; self.ensure_caret_visible(); self.status(f'Replaced {count} occurrences.')
                self.confirm('Replace',f'{count} literal occurrence(s). Replacement can be undone.',
                             [('Replace next',one),('Replace all',all_),('Cancel',None)],default=2)
            self.request_text('Replace with',self.search_replacement,have_replacement)
        self.request_text('Find text to replace',self.search_term,have_needle)

    def goto_text(self, value: str) -> None:
        match=re.fullmatch(r'\s*([1-9][0-9]*)(?:\s*:\s*([1-9][0-9]*))?\s*',value)
        if not match: raise MDBMError('Use line or line:column, counting from 1.')
        row=max(0,min(self.doc.line_count-1,int(match[1])-1)); column=int(match[2] or 1)-1
        self.doc.move_to(self.doc.line_start(row)+cell_to_offset(self.doc.line(row),column,self.config.ascii,self.config.get('input','tab_size')))
        self.set_layout('focus',Tile.EDIT); self.ensure_caret_visible()

    def move_doc(self, direction: str, selecting: bool = False) -> None:
        self.set_layout('focus',Tile.EDIT)
        rect=self.geometry.contents.get(Tile.EDIT) if self.geometry else None
        self.doc.move(direction,selecting,self.config.ascii,self.config.get('input','tab_size'),max(1,rect.h-1) if rect else 10)
        self.ensure_caret_visible()

    def navigate(self, direction: str) -> None:
        tile=self.layout.focus
        if tile==Tile.EDIT: self.move_doc(direction,self.config.get('input','profile')=='vim' and self.vim_mode=='visual'); return
        if tile==Tile.VIEW:
            amount={'up':-1,'down':1,'pageup':-10,'pagedown':10,'home':-10**8,'top':-10**8,'end':10**8,'bottom':10**8}.get(direction)
            if amount is not None: self.scroll_tile(tile,amount)
            elif direction in ('left','right'): self.select_link(-1 if direction=='left' else 1)
            return
        rows=self.tree_rows() if tile==Tile.FILESYSTEM else self.contents_rows()
        memory=self.memories[tile]
        if not rows: return
        memory.row=max(0,min(len(rows)-1,memory.row))
        if direction in ('up','down','pageup','pagedown','home','end','top','bottom'):
            amount={'up':-1,'down':1,'pageup':-10,'pagedown':10,'home':-10**8,'top':-10**8,'end':10**8,'bottom':10**8}[direction]
            memory.row=max(0,min(len(rows)-1,memory.row+amount)); self.ensure_row_visible(tile,len(rows)); return
        if tile==Tile.FILESYSTEM:
            p,depth,isdir,symlink=rows[memory.row]; key=str(p.relative_to(self.files.root))
            if direction=='right' and isdir and key not in memory.expanded: self.tree_activate(toggle_only=True)
            elif direction=='left':
                if key in memory.expanded: memory.expanded.remove(key); self.tree_cache=None
                else:
                    for i in range(memory.row-1,-1,-1):
                        if rows[i][1]<depth: memory.row=i; break
        elif tile==Tile.CONTENTS:
            h=rows[memory.row]
            if direction=='right': self.contents_collapsed.discard(h.slug)
            elif direction=='left':
                if h.slug not in self.contents_collapsed and any(x.level>h.level for x in rows[memory.row+1:memory.row+2]):
                    self.contents_collapsed.add(h.slug)
                else:
                    for i in range(memory.row-1,-1,-1):
                        if rows[i].level<h.level: memory.row=i; break

    def ensure_row_visible(self, tile: Tile, count: int) -> None:
        memory=self.memories[tile]; rect=self.geometry.contents.get(tile) if self.geometry else None
        height=max(1,rect.h if rect else 8)
        if memory.row<memory.scroll: memory.scroll=memory.row
        elif memory.row>=memory.scroll+height: memory.scroll=memory.row-height+1
        memory.scroll=max(0,min(memory.scroll,max(0,count-height)))

    def enter_action(self) -> None:
        if self.layout.focus==Tile.EDIT: self.doc.insert('\n')
        elif self.layout.focus==Tile.FILESYSTEM: self.tree_activate()
        elif self.layout.focus==Tile.CONTENTS:
            rows=self.contents_rows()
            if rows: self.go_source(rows[min(len(rows)-1,self.memories[Tile.CONTENTS].row)].source_line)
        else: self.follow_selected_link()

    def delete_action(self) -> None:
        if self.layout.focus==Tile.FILESYSTEM: self.trash_selected()
        elif self.layout.focus==Tile.EDIT: self.doc.delete()

    def refresh_tree(self) -> None:
        self.tree_cache=None; self.invalidate()

    def filter_contents(self, value: str) -> None:
        memory=self.memories[Tile.CONTENTS]; memory.filter=value; memory.row=memory.scroll=0
        self.set_layout('focus',Tile.CONTENTS)

    def rename_selected(self) -> None:
        src=self.selected_path()
        if src is None: self.status('No filesystem item selected.'); return
        initial=str(src.relative_to(self.files.root))
        def perform(value: str) -> None:
            old_path=self.doc.path
            baseline=self.doc.fingerprint
            affected=bool(old_path and (old_path==src or old_path.is_relative_to(src)))
            before=self.files.fingerprint(old_path) if affected else None
            dst=self.files.move(src,value)
            if old_path and (old_path==src or old_path.is_relative_to(src)):
                relative=old_path.relative_to(src)
                self.doc.path=dst/relative if relative.parts else dst
                # A copied move changes inode/mtime. Rebase only when the source
                # still matched the loaded baseline; never bless an external edit.
                if before==baseline:
                    self.doc.fingerprint=self.files.fingerprint(self.doc.path)
            self.refresh_tree(); self.status('Moved to '+str(dst.relative_to(self.files.root)))
        self.request_text('Rename / move',initial,perform,'Destination relative to workspace; existing targets are not overwritten.')

    def trash_selected(self) -> None:
        path=self.selected_path()
        if path is None: self.status('No filesystem item selected.'); return
        affects=self.doc.path is not None and (self.doc.path==path or self.doc.path.is_relative_to(path))
        def actual() -> None:
            self.files.trash(path)
            if affects: self.set_document(DocumentModel())
            self.refresh_tree(); self.status('Moved to system trash.')
        def confirmed() -> None:
            self.guard_dirty(actual) if affects else actual()
        self.confirm('Move to trash?',display_text(path)+'\nNo permanent-delete fallback is used.',
                     [('Trash',confirmed),('Cancel',None)],default=1)

    def select_link(self, direction: int) -> None:
        links=[]; seen=set()
        for row in self.preview.rows:
            for span in row.spans:
                if span.link and span.link not in seen: links.append(span.link); seen.add(span.link)
        self._view_links=links
        if not links: self.status('No links in the current preview.'); return
        self._selected_link=(self._selected_link+direction)%len(links)
        selected=links[self._selected_link]
        for index,row in enumerate(self.preview.rows):
            if any(s.link==selected for s in row.spans): self.memories[Tile.VIEW].scroll=index; break
        self.set_layout('focus',Tile.VIEW); self.status('Selected link: '+selected)

    def follow_selected_link(self) -> None:
        if self.preview.generation!=self.doc.generation: self.status('Wait for the current preview before following a link.'); return
        if not self._view_links:
            self._selected_link=-1; self.select_link(1)
        if self._view_links: self.activate_link(self._view_links[self._selected_link%len(self._view_links)])

    def scroll_tile(self, tile: Tile, delta: int) -> None:
        memory=self.memories[tile]
        rect=self.geometry.contents.get(tile) if self.geometry else None
        height=max(1,rect.h if rect else 1)
        count=self.doc.line_count if tile==Tile.EDIT else len(self.preview.rows) if tile==Tile.VIEW else len(self.tree_rows()) if tile==Tile.FILESYSTEM else len(self.contents_rows())
        memory.scroll=max(0,min(max(0,count-height),memory.scroll+int(delta)))
        self.invalidate()

    def toggle_profile(self) -> None:
        profile='vim' if self.config.get('input','profile')=='standard' else 'standard'
        self.config.data['input']['profile']=profile; self.vim_mode='normal' if profile=='vim' else 'insert'
        self.emit('mode-change'); self.status('Keyboard profile: '+profile)

    def set_theme(self, tid: str, commit: bool = True) -> None:
        if tid not in self.catalog.compiled: raise ThemeError('Theme is unavailable or invalid.')
        self.theme=self.catalog.compiled[tid]; self.painter=ThemePainter(self.theme)
        if commit: self._committed_theme_id=tid
        self.invalidate()

    def cycle_motion(self) -> None:
        choices=('off','reduced','normal','high'); current=self.config.get('appearance','motion')
        self.config.data['appearance']['motion']=choices[(choices.index(current)+1)%4]
        self.status('Motion: '+self.config.motion)

    def toggle_appearance(self, key: str) -> None:
        self.config.data['appearance'][key]=not self.config.get('appearance',key); self.invalidate()

    def toggle_accessibility(self, key: str) -> None:
        self.config.data['accessibility'][key]=not self.config.get('accessibility',key)
        self.requested_parse=None; self.invalidate()

    def diagnostics_text(self) -> str:
        versions=[]
        for package in ('prompt-toolkit','markdown-it-py','mdit-py-plugins','Pygments','wcwidth','Send2Trash','regex'):
            try: versions.append(package+' '+importlib.metadata.version(package))
            except importlib.metadata.PackageNotFoundError: versions.append(package+' MISSING')
        timing=f'Last cell composition: {self.frame_ms[-1]:.2f} ms' if self.frame_ms else 'No frames measured yet.'
        return (f'MDBM {VERSION} / Python {sys.version.split()[0]}\n'+'\n'.join(versions)+
                f'\n\nTheme: {self.theme.id}\nRoot: {self.files.root}\nDepth: {self.config.depth}\n'+timing+
                f'\nLast logical damage: {self.last_damage} cells\n\n'+'\n'.join(self.diagnostics+self.doc.diagnostics or ['No diagnostics.']))

    # -------------------------------------------------------------------------
    # Modal surfaces, ribbon, delayed nested menus and theme transactions
    # -------------------------------------------------------------------------
    def push_overlay(self, overlay: Overlay) -> None:
        self.overlays.append(overlay); self.emit('menu-open'); self.invalidate()

    def close_overlay(self, cancel: bool = True) -> None:
        if not self.overlays: return
        overlay=self.overlays.pop()
        if cancel and overlay.cancel: overlay.cancel()
        self.hover_due=None; self.hover_close_due=None
        if not self.overlays: self.ribbon_active=False
        self.emit('menu-close'); self.invalidate()

    def close_menus(self) -> None:
        while self.overlays and self.overlays[-1].kind=='menu': self.close_overlay(False)
        self.ribbon_active=False

    def info(self, title: str, text: str) -> None:
        self.push_overlay(Overlay('info',title,message=text))

    def request_text(self, title: str, initial: str, accept: Callable[[str],None], message: str = '') -> None:
        doc=DocumentModel(initial); doc.cursor=len(initial); doc.anchor=0; doc.max_chars=16384
        self.push_overlay(Overlay('input',title,editor=doc,message=message,accept=accept))

    def confirm(self, title: str, message: str, actions: list[tuple[str,Callable[[],None] | None]], default: int = -1) -> None:
        self.push_overlay(Overlay('confirm',title,message=message,actions=actions,index=default if default>=0 else len(actions)-1))

    def menu_items(self, name: str) -> list[MenuItem]:
        def item(cid: str,label: str = '') -> MenuItem:
            command=self.registry.commands[cid]
            suffix='  '+command.keys[0] if command.keys else ''
            return MenuItem(label or command.label+suffix,cid)
        if name=='File':
            return [item(cid) for cid in ('file.new','file.open','file.save','file.save_as','file.mkdir','file.rename','file.trash','file.root','file.quit')]
        if name=='Edit':
            return [item(cid) for cid in ('edit.undo','edit.redo','edit.copy','edit.cut','edit.paste','edit.select_all','edit.find','edit.replace','edit.insert_tab','edit.profile')]
        if name=='Tile':
            return [MenuItem('Focus tile',children=tuple(item('tile.'+t.value) for t in Tile)),
                    item('tile.switch_left'),item('tile.swap'),item('tile.hide'),
                    MenuItem('Visibility',children=tuple(item(cid) for cid in ('tile.toggle_left','tile.toggle_right','tile.toggle_edit','tile.toggle_view'))),
                    MenuItem('Resize',children=tuple(item(cid) for cid in ('layout.narrow_left','layout.widen_left','layout.shrink_view','layout.grow_view'))),item('tile.restore')]
        if name=='Navigate':
            return [item(cid) for cid in ('edit.goto','edit.find_next','edit.find_previous','navigate.contents_filter','navigate.refresh','navigate.previous_link','navigate.next_link','navigate.follow_link')]
        if name=='Theme':
            return [item(cid) for cid in ('theme.choose','theme.minimal','theme.motion','theme.immersive','theme.high_contrast','theme.ascii')]
        if name=='Scripts':
            paths,diagnostics=allowed_scripts(self.config)
            for d in diagnostics:
                if d not in self.diagnostics: self.diagnostics.append(d)
            items=[MenuItem(p.name,'script.run',str(p)) for p in paths]
            if not items: items=[MenuItem('No allowlisted sibling scripts',enabled=False)]
            if self.script_runner and self.script_runner.running: items.append(item('script.cancel'))
            return items
        return [item(cid) for cid in ('help.shortcuts','help.diagnostics','help.about','command.palette')]

    def open_menu(self, name: str, anchor: tuple[int,int] | None = None) -> None:
        self.close_menus()
        names=['File','Edit','Tile','Navigate','Theme','Scripts','Help']
        if name in names: self.ribbon_index=names.index(name)
        self.ribbon_active=True
        if anchor is None:
            anchor=next(((r.x,r.bottom) for n,r in self.ribbon_regions if n==name),(1,1))
        self.push_overlay(Overlay('menu',name,items=self.menu_items(name),anchor=anchor))

    def activate_ribbon(self) -> None:
        if self.overlays and self.overlays[-1].kind=='menu': self.close_menus(); return
        self.ribbon_active=True; self.open_menu(['File','Edit','Tile','Navigate','Theme','Scripts','Help'][self.ribbon_index])

    def open_submenu(self, overlay: Overlay, index: int) -> None:
        if not 0<=index<len(overlay.items) or not overlay.items[index].children: return
        parent_index=self.overlays.index(overlay)
        while len(self.overlays)>parent_index+1: self.close_overlay(False)
        child=Overlay('menu',overlay.items[index].label,items=list(overlay.items[index].children),
                      anchor=(overlay.rect.right-1,overlay.list_y+index-overlay.scroll))
        self.push_overlay(child)

    def tile_context(self, tile: Tile, anchor: tuple[int,int] | None = None) -> None:
        self.close_menus()
        entries=[MenuItem('Focus '+tile.value.title(),'tile.'+tile.value),
                 MenuItem('Hide '+tile.value.title(),'tile.hide',tile.value),
                 MenuItem('Restore all tiles','tile.restore')]
        if tile in (Tile.FILESYSTEM,Tile.CONTENTS): entries.insert(1,MenuItem('Switch Filesystem / Contents','tile.switch_left'))
        else: entries.insert(1,MenuItem('Swap View and Edit','tile.swap'))
        if tile==Tile.EDIT:
            entries=[MenuItem('Cut','edit.cut'),MenuItem('Copy','edit.copy'),MenuItem('Paste','edit.paste'),MenuItem('Select all','edit.select_all')]+entries
        elif tile==Tile.FILESYSTEM:
            entries=[MenuItem('Open selected','input.enter'),MenuItem('Rename / move…','file.rename'),MenuItem('Trash…','file.trash')]+entries
        elif tile==Tile.CONTENTS: entries.insert(0,MenuItem('Filter headings…','navigate.contents_filter'))
        self.push_overlay(Overlay('menu',tile.value.title(),items=entries,anchor=anchor or (3,3)))

    def open_palette(self) -> None:
        # A global command must not leave a stale destructive intent underneath it.
        while self.overlays: self.close_overlay()
        if self.capture: self.cancel_capture()
        self.push_overlay(Overlay('palette','Command palette',editor=DocumentModel()))
        self.palette_items(self.overlays[-1])

    def palette_items(self, overlay: Overlay) -> None:
        needle=overlay.editor.text.casefold() if overlay.editor else ''
        tokens=needle.split()
        commands=[c for c in self.registry.commands.values() if c.visible and all(word in (c.category+' '+c.label+' '+c.id).casefold() for word in tokens)]
        overlay.items=[MenuItem(f'{c.category} / {c.label}','command.palette.item',c.id) for c in commands]
        overlay.index=max(0,min(len(overlay.items)-1,overlay.index)); overlay.scroll=0

    def choose_theme(self) -> None:
        prior=self.theme.id
        themes=sorted(self.catalog.compiled.values(),key=lambda t:t.name.casefold())
        items=[MenuItem(t.name,'theme.choose.item',t.id) for t in themes]
        index=next((i for i,t in enumerate(themes) if t.id==prior),0)
        self.push_overlay(Overlay('theme','Choose theme · arrows preview · Enter applies',items=items,index=index,
                                  cancel=lambda:self.set_theme(prior,False)))

    def preview_theme(self, overlay: Overlay) -> None:
        if overlay.items: self.set_theme(overlay.items[overlay.index].payload,False)

    def overlay_row(self, payload: Mapping[str,Any]) -> None:
        tag=payload.get('tag'); overlay=next((o for o in self.overlays if o.tag==tag),None)
        if overlay is None: return
        index=payload['row']
        if not 0<=index<len(overlay.items): return
        if overlay.kind=='theme' and overlay.index!=index:
            overlay.index=index; self.preview_theme(overlay); return
        overlay.index=index
        self.activate_overlay_item(overlay)

    def activate_overlay_item(self, overlay: Overlay) -> None:
        if not overlay.items or not 0<=overlay.index<len(overlay.items): return
        item=overlay.items[overlay.index]
        if not item.enabled: self.emit('no-context-actions'); return
        if item.children: self.open_submenu(overlay,overlay.index); return
        if overlay.kind=='theme':
            self.set_theme(item.payload); self.close_overlay(False); self.persist_session(); self.status('Theme applied: '+self.theme.name); return
        if overlay.kind=='palette':
            self.close_overlay(False); self.dispatch(item.payload); return
        self.close_menus(); self.dispatch(item.command,item.payload)

    def overlay_accept(self) -> None:
        if not self.overlays: return
        overlay=self.overlays[-1]
        if overlay.kind in ('menu','palette','theme'): self.activate_overlay_item(overlay)
        elif overlay.kind=='confirm': self.overlay_action({'row':overlay.index})
        elif overlay.kind=='input':
            value=overlay.editor.text if overlay.editor else ''
            callback=overlay.accept; self.close_overlay(False)
            if callback: callback(value)
        elif overlay.kind=='script' and overlay.runner and overlay.runner.running: self.cancel_script()
        else: self.close_overlay()

    def overlay_action(self, payload: Mapping[str,Any]) -> None:
        if not self.overlays: return
        overlay=self.overlays[-1]; index=int(payload['row'])
        if overlay.kind!='confirm' or not 0<=index<len(overlay.actions): return
        callback=overlay.actions[index][1]; self.close_overlay(False)
        if callback: callback()

    def overlay_field(self, payload: Mapping[str,Any]) -> None:
        if not self.overlays: return
        overlay=self.overlays[-1]
        if overlay.editor:
            column=max(0,payload['x']-(overlay.rect.x+1)+overlay.field_scroll)
            overlay.editor.move_to(cell_to_offset(overlay.editor.text,column,self.config.ascii))

    def escape(self) -> None:
        if self.capture:
            self.cancel_capture(); return
        if self.overlays:
            overlay=self.overlays[-1]
            if overlay.kind=='script' and overlay.runner and overlay.runner.running:
                self.cancel_script(); return
            self.close_overlay(); return
        if self.ribbon_active: self.ribbon_active=False; return
        if self.config.get('input','profile')=='vim':
            self.vim_mode='normal'; self.doc.anchor=None; self.emit('mode-change')
        elif self.doc.anchor is not None: self.doc.anchor=None

    def script_arguments(self, path: Path) -> None:
        authorized,_=allowed_scripts(self.config)
        if path not in authorized: raise MDBMError('Script is no longer allowlisted.')
        def arguments(value: str) -> None:
            args=parse_script_args(value)
            self.confirm('Run trusted script?',path.name+'\nArguments: '+json.dumps(args,ensure_ascii=True)+
                         '\nThis is trusted Python code, not a sandbox.',
                         [('Run',lambda:self.start_script(path,args)),('Cancel',None)],default=1)
        self.request_text('Script arguments (JSON array)','[]',arguments,'Example: ["--count", "4", "text with spaces"]')

    def start_script(self, path: Path, args: list[str]) -> None:
        if self.script_runner and self.script_runner.running: raise MDBMError('A script is already running.')
        authorized,_=allowed_scripts(self.config)
        self.script_runner=ScriptRunner(self.config.get('scripts','max_output_kib')*1024)
        self.script_runner.on_change=self.invalidate
        self.push_overlay(Overlay('script','Script: '+path.name,runner=self.script_runner))
        self.script_task=self.create_task(self.script_runner.run(path,args,authorized))

    def cancel_script(self) -> None:
        if self.script_runner and (self.script_runner.running or self.script_task):
            self.create_task(self.script_runner.cancel()); self.status('Script cancellation requested.')
        else: self.status('No running script.')

    # -------------------------------------------------------------------------
    # Normalized pointer input, full-cell targets and explicit capture
    # -------------------------------------------------------------------------
    def hit_test(self, x: int, y: int) -> Hit | None:
        for hit in reversed(self.hits):
            if hit.rect.contains(x,y): return hit
        return None

    def editor_position(self, x: int, y: int) -> int:
        if self.geometry is None or Tile.EDIT not in self.geometry.contents: return self.doc.cursor
        rect=self.geometry.contents[Tile.EDIT]; memory=self.memories[Tile.EDIT]
        gutter=min(len(str(self.doc.line_count))+2,max(0,rect.w//3))
        yy=max(rect.y,min(rect.bottom-1,y))
        row=max(0,min(self.doc.line_count-1,memory.scroll+yy-rect.y))
        column=max(0,x-rect.x-gutter+memory.scroll_x)
        return self.doc.line_start(row)+cell_to_offset(self.doc.line(row),column,self.config.ascii,self.config.get('input','tab_size'))

    def edit_point(self, payload: Mapping[str,Any]) -> None:
        position=self.editor_position(payload['x'],payload['y'])
        old_cursor,old_anchor=self.doc.cursor,self.doc.anchor
        if payload['button']=='right':
            selection=self.doc.selection
            if not selection or not selection[0]<=position<selection[1]: self.doc.move_to(position)
            self.tile_context(Tile.EDIT,(payload['x'],payload['y'])); return
        clicks=payload.get('clicks',1)
        if clicks>=3: self.doc.select_line(position); granularity='line'
        elif clicks==2: self.doc.select_word(position); granularity='word'
        else: self.doc.move_to(position,bool(payload.get('shift'))); granularity='character'
        selection=self.doc.selection
        anchor=selection[0] if selection else (self.doc.anchor if self.doc.anchor is not None else self.doc.cursor)
        self.capture=Capture('editor','left',Tile.EDIT,original_cursor=old_cursor,original_anchor=old_anchor,
                             anchor=anchor,granularity=granularity,pointer=(payload['x'],payload['y']))
        if selection: self.capture.selection_end=selection[1]
        self.ensure_caret_visible()

    def tree_point(self, payload: Mapping[str,Any]) -> None:
        memory=self.memories[Tile.FILESYSTEM]; memory.row=payload['row']
        if payload['button']=='right': self.tile_context(Tile.FILESYSTEM,(payload['x'],payload['y']))
        elif payload.get('clicks',1)>=2: self.tree_activate()

    def contents_toggle(self, row: int) -> None:
        rows=self.contents_rows()
        if not 0<=row<len(rows): return
        slug=rows[row].slug
        if slug in self.contents_collapsed: self.contents_collapsed.remove(slug)
        else: self.contents_collapsed.add(slug)

    def contents_point(self, payload: Mapping[str,Any]) -> None:
        rows=self.contents_rows(); row=payload['row']
        if not 0<=row<len(rows): return
        self.memories[Tile.CONTENTS].row=row
        if payload['button']=='right': self.tile_context(Tile.CONTENTS,(payload['x'],payload['y']))
        else: self.go_source(rows[row].source_line)

    def view_point(self, payload: Mapping[str,Any]) -> None:
        if payload['button']=='right': self.tile_context(Tile.VIEW,(payload['x'],payload['y'])); return
        if self.preview.generation!=self.doc.generation: self.status('Preview is updating; source navigation is temporarily held.'); return
        row=payload['row']
        if 0<=row<len(self.preview.rows):
            preserving=bool(self.doc.selection)
            self.go_source(self.preview.rows[row].start,preserve_selection=True)
            if preserving: self.status('Source block revealed; the existing editor selection is preserved.')

    def border_point(self, payload: Mapping[str,Any]) -> None:
        tile=Tile(payload['tile']); self.set_layout('focus',tile)
        if payload['button']=='right' or payload.get('clicks',1)>=2:
            self.tile_context(tile,(payload['x'],payload['y']))

    def begin_resize(self, payload: Mapping[str,Any]) -> None:
        if not self.geometry: return
        axis=payload['axis']
        if axis not in self.geometry.dividers: return
        self.capture=Capture('divider',payload['button'],axis=axis,original_layout=self.layout,
                             bounds=self.geometry.workspace,pointer=(payload['x'],payload['y']))
        self.status('Resizing · release to commit · Escape rolls back')

    def drag(self, payload: Mapping[str,Any]) -> None:
        capture=self.capture
        if not capture or not self.geometry: return
        x,y=payload['x'],payload['y']; capture.pointer=(x,y)
        if capture.kind=='divider':
            if capture.axis=='left_ratio':
                left=self.geometry.tiles.get(self.layout.left_tile)
                right=next((r for t,r in self.geometry.tiles.items() if t in (Tile.EDIT,Tile.VIEW)),None)
                if not left or not right: self.cancel_capture(); return
                free=left.w+right.w; band=max(0,(right.x-left.right-1)//2)
                ratio=(x-self.geometry.workspace.x-band)/max(1,free)
                minimum=(self.config.get('layout','min_width')+2*(1+self.geometry.band))/max(1,free)
            else:
                view=self.geometry.tiles.get(Tile.VIEW); edit=self.geometry.tiles.get(Tile.EDIT)
                if not view or not edit: self.cancel_capture(); return
                first,second=(view,edit) if view.y<edit.y else (edit,view)
                free=view.h+edit.h; band=max(0,(second.y-first.bottom-1)//2)
                top_ratio=(y-first.y-band)/max(1,free)
                ratio=top_ratio if self.layout.view_first else 1-top_ratio
                minimum=(self.config.get('layout','min_height')+2*(1+self.geometry.band))/max(1,free)
            minimum=min(.49,max(.1,minimum))
            self.layout=reduce_layout(self.layout,capture.axis,max(minimum,min(1-minimum,ratio)))
        else:
            position=self.editor_position(x,y)
            if capture.granularity=='character': self.doc.anchor=capture.anchor; self.doc.cursor=position
            else:
                self.doc.select_line(position) if capture.granularity=='line' else self.doc.select_word(position)
                selection=self.doc.selection or (position,position)
                if position>=capture.anchor: self.doc.anchor=capture.anchor; self.doc.cursor=selection[1]
                else: self.doc.anchor=capture.selection_end; self.doc.cursor=selection[0]
        self.invalidate()

    def release(self, payload: Mapping[str,Any]) -> None:
        if not self.capture: return
        capture=self.capture; self.drag(payload)
        if capture.kind=='divider': self.resize_commits+=1; self.status('Split size committed.')
        elif self.doc.anchor==self.doc.cursor: self.doc.anchor=None
        self.capture=None

    def cancel_capture(self) -> None:
        if self.capture:
            if self.capture.kind=='divider' and self.capture.original_layout:
                self.layout=self.capture.original_layout; self.status('Resize cancelled.')
            elif self.capture.kind=='editor':
                self.doc.cursor=self.capture.original_cursor; self.doc.anchor=self.capture.original_anchor
            self.capture=None; self.invalidate()

    def mouse(self, event_type: str, x: int, y: int, button: str = 'left', shift: bool = False) -> None:
        if event_type=='move' and self.capture:
            self.dispatch('input.drag',{'x':x,'y':y}); return
        if event_type=='up':
            if self.capture: self.dispatch('input.release',{'x':x,'y':y})
            return
        hit=self.hit_test(x,y)
        if event_type in ('scroll_up','scroll_down'):
            delta=-3 if event_type=='scroll_up' else 3
            if self.overlays:
                overlay=self.overlays[-1]
                overlay.scroll=max(0,overlay.scroll+delta)
                self.invalidate(); return
            if hit and hit.tile: self.dispatch('tile.scroll',{'tile':hit.tile.value,'delta':delta})
            return
        if event_type=='move':
            if self.overlays and self.overlays[-1].kind=='menu':
                overlay=next((o for o in reversed(self.overlays) if o.rect.contains(x,y)),None)
                if overlay:
                    index=overlay.scroll+y-overlay.list_y
                    if 0<=index<len(overlay.items):
                        overlay.hover=index
                        if overlay.items[index].children:
                            candidate=(overlay.tag,index)
                            if self.hover_due is None or self.hover_due[:2]!=candidate:
                                self.hover_due=(overlay.tag,index,time.monotonic()+self.config.get('input','submenu_delay_ms')/1000)
                            self.hover_close_due=None
                        else:
                            self.hover_due=None
                            if self.overlays.index(overlay)<len(self.overlays)-1:
                                if not self.hover_close_due or self.hover_close_due[0]!=overlay.tag:
                                    self.hover_close_due=(overlay.tag,time.monotonic()+.40)
                        self.invalidate()
                else: self.hover_due=None
            return
        if event_type!='down': return
        if self.overlays:
            top=self.overlays[-1]
            menus=top.kind=='menu'
            in_overlay=any(o.rect.contains(x,y) for o in self.overlays) if menus else top.rect.contains(x,y)
            ribbon_click=menus and hit and hit.command.startswith('menu.') and y==0
            if not in_overlay and not ribbon_click:
                self.close_overlay(); return  # Explicitly consume; never click through.
            if hit is None or (hit.role not in ('overlay','ribbon')):
                return
        if hit is None: return
        now=time.monotonic(); prev_t,px,py,prevbutton,count=self.last_click
        count=(count%3)+1 if now-prev_t<=self.config.get('input','double_click_ms')/1000 and abs(x-px)<=1 and y==py and button==prevbutton else 1
        self.last_click=(now,x,y,button,count)
        if hit.tile is not None and hit.role not in ('divider','overlay'):
            self.set_layout('focus',hit.tile)
        if hit.role in ('editor','row','preview','link','border','divider') or hit.command in ('overlay.row','overlay.field'):
            payload=dict(hit.payload) if isinstance(hit.payload,dict) else {}
            payload.update({'x':x,'y':y,'button':button,'clicks':count,'shift':shift})
            if hit.tile: payload['tile']=hit.tile.value
        else: payload=hit.payload
        self.dispatch(hit.command,payload)

    # -------------------------------------------------------------------------
    # Keyboard ownership: modals first, then operational commands, then text
    # -------------------------------------------------------------------------
    def build_bindings(self) -> KeyBindings:
        from prompt_toolkit.key_binding.key_bindings import _parse_key
        bindings=KeyBindings(); occupied: dict[tuple[Any,...],str]={}
        def add(sequence: str, command: str, custom: bool = False) -> None:
            try:
                keys=tuple(sequence.split()); normalized=tuple(_parse_key(k) for k in keys)
                if not keys: raise ValueError('Empty key sequence')
                if normalized in occupied:
                    if custom: self.diagnostics.append(f'Binding {sequence} conflicts with {occupied[normalized]}; ignored.')
                    return
                occupied[normalized]=command
                @bindings.add(*keys)
                def bound(event: Any, key: str=sequence, cid: str=command) -> None:
                    self.handle_key(key,cid)
            except ValueError as exc:
                self.diagnostics.append('Unreachable binding '+display_text(sequence)+': '+display_text(exc))
        for command in self.registry.commands.values():
            for key in command.keys: add(key,command.id)
        reserved={'c-p','c-t','c-q','escape','f10','f23','f24'}
        for sequence,cid in self.config.data['bindings'].items():
            if cid not in self.registry.commands:
                self.diagnostics.append('Binding names an unknown command: '+cid)
            elif sequence in reserved:
                self.diagnostics.append('Emergency binding is host-owned: '+sequence)
            else: add(sequence,cid,True)
        @bindings.add(Keys.Any)
        def any_key(event: Any) -> None:
            self.handle_text(event.data)
        @bindings.add(Keys.BracketedPaste)
        def paste(event: Any) -> None:
            self.handle_text(event.data,paste=True)
        # The backend maps only host-recognized focus reports to these keys.
        @bindings.add('f23')
        def focus_in(event: Any) -> None: self.terminal_focus(True)
        @bindings.add('f24')
        def focus_out(event: Any) -> None: self.terminal_focus(False)
        return bindings

    def terminal_focus(self, focused: bool) -> None:
        self.terminal_focused=focused
        if not focused:
            self.cancel_capture(); self.hover_due=None; self.hover_close_due=None
            self.last_click=(0,-1,-1,'',0); self._vim_pending=''
        self.invalidate()

    def handle_key(self, key: str, command: str) -> None:
        if command in ('file.quit','tile.restore','command.palette'):
            self.dispatch(command); return
        if self.capture and command!='input.escape': self.cancel_capture()
        if self.overlays:
            self.overlay_key(key,command); self.invalidate(); return
        if self.config.get('input','profile')=='vim' and self.layout.focus==Tile.EDIT:
            if key=='c-r': self.dispatch('edit.redo'); return
            if command=='input.enter' and self.vim_mode!='insert':
                self.move_doc('down',self.vim_mode=='visual'); self.move_doc('home',self.vim_mode=='visual'); return
        self.dispatch(command)

    def overlay_key(self, key: str, command: str) -> None:
        overlay=self.overlays[-1]
        if command=='input.escape': self.escape(); return
        if command=='input.enter': self.overlay_accept(); return
        if overlay.kind in ('menu','theme','palette'):
            if key in ('up','down','pageup','pagedown') and overlay.items:
                delta={'up':-1,'down':1,'pageup':-8,'pagedown':8}[key]
                overlay.index=(overlay.index+delta)%len(overlay.items)
                height=max(1,overlay.rect.bottom-2-overlay.list_y)
                if overlay.index<overlay.scroll: overlay.scroll=overlay.index
                elif overlay.index>=overlay.scroll+height: overlay.scroll=overlay.index-height+1
                if overlay.kind=='theme': self.preview_theme(overlay)
                return
            if overlay.kind=='menu':
                if key=='right':
                    if overlay.items and overlay.items[overlay.index].children: self.open_submenu(overlay,overlay.index)
                    elif len(self.overlays)==1:
                        names=['File','Edit','Tile','Navigate','Theme','Scripts','Help']; self.open_menu(names[(self.ribbon_index+1)%len(names)])
                elif key=='left':
                    if len(self.overlays)>1: self.close_overlay()
                    else:
                        names=['File','Edit','Tile','Navigate','Theme','Scripts','Help']; self.open_menu(names[(self.ribbon_index-1)%len(names)])
                elif command.startswith('menu.'): self.dispatch(command)
                return
        if overlay.kind=='confirm':
            if key in ('left','right','tab','s-tab'):
                overlay.index=(overlay.index+(-1 if key in ('left','s-tab') else 1))%len(overlay.actions)
            return
        if overlay.kind in ('info','script'):
            if overlay.kind=='script' and key=='c-c': self.cancel_script(); return
            amount={'up':-1,'down':1,'pageup':-10,'pagedown':10,'home':-10**8,'end':10**8}.get(key)
            if amount is not None: overlay.scroll=max(0,overlay.scroll+amount)
            return
        if overlay.editor:
            doc=overlay.editor
            if key in ('left','right','home','end'): doc.move(key)
            elif key in ('s-left','s-right','s-home','s-end'): doc.move(key[2:],True)
            elif command=='edit.backspace': doc.delete(True)
            elif command=='edit.delete': doc.delete()
            elif key=='c-a': doc.anchor=0; doc.cursor=len(doc.text)
            elif key=='c-c': self.clipboard=doc.selected_text()
            elif key=='c-x': self.clipboard=doc.selected_text(); doc.insert('')
            elif key=='c-v': doc.insert(self.clipboard.replace('\n',' '))
            elif key=='c-z': doc.undo()
            elif key=='c-y': doc.redo()
            if overlay.kind=='palette': overlay.index=0; self.palette_items(overlay)

    def handle_text(self, text: str, paste: bool = False) -> None:
        if not text: return
        if self.overlays:
            overlay=self.overlays[-1]
            if overlay.editor:
                overlay.editor.insert(clean_paste(text).replace('\r',' ').replace('\n',' ')[:16384])
                if overlay.kind=='palette': overlay.index=0; self.palette_items(overlay)
            elif not paste and overlay.kind=='menu':
                matches=[i for i,item in enumerate(overlay.items) if item.enabled and item.label.casefold().startswith(text.casefold())]
                if matches:
                    overlay.index=next((i for i in matches if i>overlay.index),matches[0]); self.activate_overlay_item(overlay)
            elif not paste and overlay.kind=='confirm':
                choices=[i for i,(label,_) in enumerate(overlay.actions) if label.casefold().startswith(text.casefold())]
                if len(choices)==1: self.overlay_action({'row':choices[0]})
            self.invalidate(); return
        if self.layout.focus!=Tile.EDIT:
            if not paste and self.layout.focus==Tile.VIEW and text in ('n','p'):
                self.dispatch('navigate.next_link' if text=='n' else 'navigate.previous_link')
            elif not paste and self.layout.focus==Tile.FILESYSTEM:
                rows=self.tree_rows(); memory=self.memories[Tile.FILESYSTEM]
                for offset in range(1,len(rows)+1):
                    i=(memory.row+offset)%len(rows)
                    if rows[i][0].name.casefold().startswith(text.casefold()): memory.row=i; self.ensure_row_visible(Tile.FILESYSTEM,len(rows)); break
            self.invalidate(); return
        before=self.doc.generation
        if self.config.get('input','profile')=='vim' and self.vim_mode!='insert' and not paste:
            self.vim_text(text)
        else: self.doc.insert(text)
        if self.doc.generation!=before:
            self.requested_parse=None
            if self._dirty_known!=self.doc.dirty: self.emit('dirty-change'); self._dirty_known=self.doc.dirty
        self.ensure_caret_visible(); self.invalidate()

    def vim_text(self, key: str) -> None:
        selecting=self.vim_mode=='visual'
        moves={'h':'left','j':'down','k':'up','l':'right','w':'word-right','b':'word-left','0':'home','$':'end','G':'bottom'}
        if key in moves: self.move_doc(moves[key],selecting)
        elif key=='i': self.vim_mode='insert'; self.doc.anchor=None
        elif key=='a': self.doc.move('right'); self.vim_mode='insert'
        elif key=='o': self.doc.move('end'); self.doc.insert('\n'); self.vim_mode='insert'
        elif key=='v':
            if selecting: self.vim_mode='normal'; self.doc.anchor=None
            else:
                self.vim_mode='visual'; self.doc.anchor=self.doc.cursor; self.doc.cursor=self.doc.boundary(1)
        elif key=='x': self.doc.delete(); self.vim_mode='normal'
        elif key=='u': self.doc.undo()
        elif key=='p': self.doc.insert(self.clipboard); self.vim_mode='normal'
        elif key=='y':
            if not self.doc.selection: self.doc.select_line(self.doc.cursor)
            self.copy_selection(); self.doc.anchor=None; self.vim_mode='normal'
        elif key==':': self.open_palette()
        elif key=='g':
            if getattr(self,'_vim_pending','')=='g': self.move_doc('top'); self._vim_pending=''
            else: self._vim_pending='g'; self.status('g · press g again for document start')
        self.emit('mode-change')

    # =========================================================================
    # 11. Host composition: content, protected controls, and final hit geometry
    # =========================================================================
    def visual_context(self, width: int, height: int) -> VisualContext:
        return VisualContext(width,height,self.config.depth,not self.config.ascii,
            self.config.get('accessibility','high_contrast'),self.config.motion,
            self.clock,self.config.get('appearance','seed'),self.session_seed,
            self.layout.focus.value,self.terminal_focused,self.doc.dirty,
            self.cue,self.cue_time,self.config.get('appearance','immersive'))

    def add_hit(self, rect: Rect, command: str, payload: Any = None,
                tile: Tile | None = None, role: str = 'control', z: int = 0) -> None:
        if self.geometry:
            rect=rect.intersect(self.geometry.viewport)
        if rect.w>0 and rect.h>0:
            self.hits.append(Hit(rect,command,payload,tile,'overlay' if z>=200 else role,z))

    def frame(self, width: int, height: int) -> Canvas:
        started=time.perf_counter()
        width,height=max(1,min(1000,width)),max(1,min(500,height))
        self.geometry=solve_geometry(width,height,self.layout,self.config,self.theme)
        self.hits=[]; self.screen_cursor=Point(x=0,y=0); self.show_cursor=False
        ctx=self.visual_context(width,height)
        styles,_,_,_=self.painter.resolve(ctx)
        canvas=Canvas(width,height,styles['background'],self.config.get('session','paint_budget'))
        try:
            self.painter.paint(canvas,self.geometry,ctx)
        except (ThemeError,ValueError,KeyError,TypeError,IndexError,ArithmeticError) as exc:
            self.diagnostics.append('Theme isolated after paint failure: '+display_text(exc))
            self.set_theme('minimal'); styles,_,_,_=self.painter.resolve(ctx)
            canvas=Canvas(width,height,styles['background'],self.config.get('session','paint_budget'))
        if canvas.budget_exhausted:
            self.notice='Theme paint budget reached; controls and content remain available.'
        for tile,rect in self.geometry.tiles.items():
            self.paint_tile(canvas,tile,rect,styles)
        for axis,rect in self.geometry.dividers.items():
            ink=HOST_FOCUS if self.capture and self.capture.kind=='divider' and self.capture.axis==axis else HOST_CHROME
            canvas.fill(rect,ink,'|' if axis=='left_ratio' else '-',100)
            self.add_hit(rect,'layout.capture',{'axis':axis},role='divider')
        if not self.geometry.tiles:
            work=self.geometry.workspace
            message='[ Restore tiles: Ctrl+T ]'
            x=max(work.x,work.x+(work.w-len(message))//2); y=max(1,work.y+work.h//2)
            canvas.text(x,y,message,HOST_FOCUS,100,canvas.bounds,self.config.ascii)
            self.add_hit(Rect(x,y,min(len(message),max(0,width-x)),1),'tile.restore')
        self.paint_ribbon(canvas)
        self.paint_status(canvas)
        for index,overlay in enumerate(self.overlays):
            self.paint_overlay(canvas,overlay,index)
        if self.overlays and self.overlays[-1].editor is None:
            self.show_cursor=False
        self.last_damage=canvas.changed(self.last_canvas); self.last_canvas=canvas
        self.frame_ms.append((time.perf_counter()-started)*1000)
        return canvas

    def paint_ribbon(self, canvas: Canvas) -> None:
        width=canvas.width
        canvas.fill(Rect(0,0,width,1),HOST_CHROME,plane=100)
        canvas.text(0,0,' MDBM ',HOST_FOCUS,100)
        self.add_hit(Rect(0,0,min(6,width),1),'command.palette')
        self.ribbon_regions=[]; x=6
        for index,name in enumerate(('File','Edit','Tile','Navigate','Theme','Scripts','Help')):
            label=' '+name+' '
            if x+len(label)>width-5: break
            region=Rect(x,0,len(label),1); self.ribbon_regions.append((name,region))
            ink=HOST_SELECTED if self.ribbon_active and self.ribbon_index==index else HOST_CHROME
            canvas.text(x,0,label,ink,100)
            self.add_hit(region,'menu.'+name.lower(),role='ribbon')
            x+=len(label)
        if width>=5:
            canvas.text(width-5,0,'[+T] ',HOST_FOCUS,100)
            self.add_hit(Rect(width-5,0,5,1),'tile.restore')

    def paint_status(self, canvas: Canvas) -> None:
        if canvas.height<2: return
        y=canvas.height-1
        canvas.fill(Rect(0,y,canvas.width,1),HOST_CHROME,plane=100)
        position=f'{self.doc.row_of()+1}:{width_of(self.doc.line(self.doc.row_of())[:self.doc.cursor-self.doc.line_start(self.doc.row_of())],self.config.ascii,self.config.get("input","tab_size"))+1}'
        mode=self.vim_mode.upper() if self.config.get('input','profile')=='vim' else 'EDIT'
        name=self.doc.path.name if self.doc.path else '[untitled]'
        right=f' {"*" if self.doc.dirty else "="} {name} {position} {mode} '
        left=' '+self.notice
        if canvas.width<50: right=f' {"*" if self.doc.dirty else "="} {position} '
        right=display_text(right,self.config.ascii)
        rw=min(canvas.width//2,width_of(right))
        canvas.text(0,y,left,HOST_ERROR if self.notice_error else HOST_CHROME,100,
                    Rect(0,y,max(0,canvas.width-rw),1),self.config.ascii)
        canvas.text(canvas.width-rw,y,right,HOST_FOCUS,100,
                    Rect(canvas.width-rw,y,rw,1),self.config.ascii)
        self.add_hit(Rect(0,y,max(1,canvas.width-rw),1),'help.diagnostics')
        self.add_hit(Rect(canvas.width-rw,y,rw,1),'edit.goto')

    def paint_tile(self, canvas: Canvas, tile: Tile, rect: Rect, styles: Mapping[str,Ink]) -> None:
        assert self.geometry is not None
        content=self.geometry.contents[tile]
        focus=tile==self.layout.focus
        border=HOST_FOCUS if focus else styles['border']
        canvas.box(rect,border,100 if focus else 20,self.config.ascii)
        for edge in (Rect(rect.x,rect.y,rect.w,1),Rect(rect.x,rect.bottom-1,rect.w,1),
                     Rect(rect.x,rect.y,1,rect.h),Rect(rect.right-1,rect.y,1,rect.h)):
            self.add_hit(edge,'tile.border',{'tile':tile.value},tile,'border')
        # Every cell of decoration surrounding a tile still focuses it. Structural
        # divider hits are appended later and retain their exact one-cell geometry.
        for band in self.geometry.slots.get('tile.'+tile.value+'.chrome',()):
            self.add_hit(band,'tile.border',{'tile':tile.value},tile,'border')
        if content.w and content.h:
            if not self.config.get('appearance','immersive'):
                canvas.fill(content,styles['text'],plane=10)
            else:
                # Clear inherited glyphs under real text, not entire background art.
                pass
            if tile==Tile.EDIT: self.paint_edit(canvas,content,styles)
            elif tile==Tile.FILESYSTEM: self.paint_tree(canvas,content,styles)
            elif tile==Tile.CONTENTS: self.paint_contents(canvas,content,styles)
            else: self.paint_view(canvas,content,styles)
        if rect.w>=4:
            buttons=[('[M]','tile.context',tile.value)]
            if rect.w>=14:
                buttons.insert(0,('[T]','tile.switch_left',None) if tile in (Tile.FILESYSTEM,Tile.CONTENTS) else ('[S]','tile.swap',None))
                buttons.append(('[x]','tile.hide',tile.value))
            total=sum(len(b[0]) for b in buttons)
            x=max(rect.x+1,rect.right-1-total)
            title=' '+tile.value.title()+(' *' if tile==Tile.EDIT and self.doc.dirty else '')+' '
            canvas.text(rect.x+1,rect.y,title,HOST_FOCUS if focus else HOST_CHROME,100,
                        Rect(rect.x+1,rect.y,max(0,x-rect.x-1),1),self.config.ascii)
            for label,command,payload in buttons:
                canvas.text(x,rect.y,label,HOST_CHROME,100,rect,self.config.ascii)
                self.add_hit(Rect(x,rect.y,len(label),1),command,payload,tile)
                x+=len(label)

    def paint_edit(self, canvas: Canvas, rect: Rect, styles: Mapping[str,Ink]) -> None:
        memory=self.memories[Tile.EDIT]
        memory.scroll=max(0,min(memory.scroll,max(0,self.doc.line_count-1)))
        gutter=min(len(str(self.doc.line_count))+2,max(0,rect.w//3))
        textrect=Rect(rect.x+gutter,rect.y,max(0,rect.w-gutter),rect.h)
        self.add_hit(rect,'edit.point',tile=Tile.EDIT,role='editor')
        selection=self.doc.selection; tabs=self.config.get('input','tab_size')
        for y in range(rect.y,rect.bottom):
            row=memory.scroll+y-rect.y
            if row>=self.doc.line_count: break
            label=str(row+1).rjust(max(0,gutter-1))+' '
            canvas.text(rect.x,y,label,styles['gutter'],10,Rect(rect.x,y,gutter,1),True)
            line=self.doc.line(row); start=self.doc.line_start(row); column=0
            for unit in display_units(line,self.config.ascii,tab_size=tabs):
                x=textrect.x+column-memory.scroll_x
                absolute=start+unit.start
                selected=bool(selection and absolute<selection[1] and start+unit.end>selection[0])
                ink=HOST_SELECTED if selected else styles['text']
                plane=100 if selected else 10
                if x>=textrect.right: break
                if x>=textrect.x and x+unit.width<=textrect.right:
                    if unit.glyph.isspace():
                        for delta in range(unit.width): canvas.put(x+delta,y,' ',ink,plane)
                    else: canvas.put(x,y,unit.glyph,ink,plane)
                column+=unit.width
            # A selected newline has a visible cell without selecting the next line.
            end=start+len(line)
            if selection and selection[0]<=end<selection[1] and end<len(self.doc.text):
                x=textrect.x+column-memory.scroll_x
                if textrect.contains(x,y): canvas.put(x,y,' ',HOST_SELECTED,100)
        cursor_row=self.doc.row_of()
        cy=rect.y+cursor_row-memory.scroll
        cx=textrect.x+width_of(self.doc.line(cursor_row)[:self.doc.cursor-self.doc.line_start(cursor_row)],self.config.ascii,tabs)-memory.scroll_x
        if textrect.contains(cx,cy) and self.layout.focus==Tile.EDIT and not self.overlays:
            self.screen_cursor=Point(x=cx,y=cy); self.show_cursor=True
            # Host caret contrast is independent of theme colours. The terminal's
            # physical cursor is also positioned here, including mono terminals.
            index=cy*canvas.width+cx; cell=canvas.cells[index]
            if not cell.continuation: canvas.put(cx,cy,cell.glyph or ' ',HOST_CARET,110)

    def paint_tree(self, canvas: Canvas, rect: Rect, styles: Mapping[str,Ink]) -> None:
        rows=self.tree_rows(); memory=self.memories[Tile.FILESYSTEM]
        memory.row=max(0,min(memory.row,max(0,len(rows)-1)))
        memory.scroll=max(0,min(memory.scroll,max(0,len(rows)-1)))
        if not rows:
            canvas.text(rect.x,rect.y,'(empty workspace)',styles['muted'],10,rect,self.config.ascii); return
        for offset in range(rect.h):
            i=memory.scroll+offset
            if i>=len(rows): break
            path,depth,isdir,unsafe=rows[i]; y=rect.y+offset
            selected=i==memory.row
            ink=HOST_SELECTED if selected else styles['tree']
            if selected: canvas.fill(Rect(rect.x,y,rect.w,1),ink,plane=100)
            relative=str(path.relative_to(self.files.root)) if path!=self.files.root else '.'
            expanded=relative in memory.expanded
            marker=('v' if expanded else '>') if isdir else '! ' if unsafe else ' '
            label='  '*min(depth,20)+marker+' '+path.name+('/' if isdir else '')
            canvas.text(rect.x,y,label,ink,100 if selected else 10,rect,self.config.ascii)
            self.add_hit(Rect(rect.x,y,rect.w,1),'tree.point',{'row':i},Tile.FILESYSTEM,'row')
            if isdir:
                self.add_hit(Rect(rect.x+min(depth,20)*2,y,1,1).intersect(rect),
                             'tree.toggle',{'row':i},Tile.FILESYSTEM,'row')

    def paint_contents(self, canvas: Canvas, rect: Rect, styles: Mapping[str,Ink]) -> None:
        rows=self.contents_rows(); memory=self.memories[Tile.CONTENTS]
        memory.row=max(0,min(memory.row,max(0,len(rows)-1)))
        memory.scroll=max(0,min(memory.scroll,max(0,len(rows)-1)))
        if not rows:
            text='(no matching headings)' if memory.filter else '(no headings yet)'
            canvas.text(rect.x,rect.y,text,styles['muted'],10,rect,self.config.ascii); return
        for offset in range(rect.h):
            i=memory.scroll+offset
            if i>=len(rows): break
            heading=rows[i]; y=rect.y+offset; selected=i==memory.row
            ink=HOST_SELECTED if selected else styles['heading']
            if selected: canvas.fill(Rect(rect.x,y,rect.w,1),ink,plane=100)
            indent=min(heading.level-1,5)*2
            marker='>' if heading.slug in self.contents_collapsed else '-'
            canvas.text(rect.x,y,' '*indent+marker+' '+heading.title,ink,100 if selected else 10,rect,self.config.ascii)
            self.add_hit(Rect(rect.x,y,rect.w,1),'contents.point',{'row':i},Tile.CONTENTS,'row')
            self.add_hit(Rect(rect.x+indent,y,1,1).intersect(rect),'contents.toggle',{'row':i},Tile.CONTENTS,'row')

    def paint_view(self, canvas: Canvas, rect: Rect, styles: Mapping[str,Ink]) -> None:
        self.preview_width=max(1,rect.w)
        memory=self.memories[Tile.VIEW]
        memory.scroll=max(0,min(memory.scroll,max(0,len(self.preview.rows)-1)))
        selected_url=self._view_links[self._selected_link] if self._view_links and self._selected_link<len(self._view_links) else ''
        for offset in range(rect.h):
            i=memory.scroll+offset
            if i>=len(self.preview.rows): break
            row=self.preview.rows[i]; y=rect.y+offset; x=rect.x
            self.add_hit(Rect(rect.x,y,rect.w,1),'view.point',{'row':i},Tile.VIEW,'preview')
            for span in row.spans:
                ink=styles.get(span.role,styles['text'])
                if span.bold or span.italic or span.strike:
                    ink=replace(ink,bold=ink.bold or span.bold,italic=ink.italic or span.italic,strike=ink.strike or span.strike)
                plane=10
                if span.link:
                    ink=replace(ink,underline=True)
                    if span.link==selected_url: ink=HOST_SELECTED; plane=100
                drawn=canvas.text(x,y,span.text,ink,plane,rect,self.config.ascii)
                if span.link and self.preview.generation==self.doc.generation:
                    self.add_hit(Rect(x,y,drawn,1).intersect(rect),'view.link',{'url':span.link},Tile.VIEW,'link')
                x+=drawn
                if x>=rect.right: break

    def paint_overlay(self, canvas: Canvas, overlay: Overlay, stack_index: int) -> None:
        plane=200+stack_index*10
        width,height=canvas.width,canvas.height
        available_h=max(1,height-2)
        if overlay.kind=='menu':
            w=min(width,max(8,min(58,max((width_of(i.label)+4 for i in overlay.items),default=16))))
            h=min(available_h,len(overlay.items)+2)
        elif overlay.kind in ('theme','palette'):
            w=min(width,76); h=min(available_h,max(6,min(22,len(overlay.items)+5)))
        elif overlay.kind=='confirm':
            w=min(width,76)
            lines=textwrap.wrap(display_text(overlay.message),max(1,w-4))
            h=min(available_h,max(6,len(lines)+len(overlay.actions)+4))
        elif overlay.kind=='input': w=min(width,88); h=min(available_h,9 if overlay.message else 7)
        else: w=min(width,100); h=min(available_h,30)
        if overlay.kind=='menu' and overlay.anchor:
            x,y=overlay.anchor
            if x+w>width:
                parent=self.overlays[stack_index-1] if stack_index else None
                x=parent.rect.x-w+1 if parent else width-w
            x=max(0,min(width-w,x)); y=max(1,min(max(1,height-1-h),y))
        else: x=max(0,(width-w)//2); y=max(1,(height-h)//2)
        overlay.rect=Rect(x,y,w,h)
        canvas.fill(overlay.rect,HOST_DIALOG,plane=plane)
        canvas.box(overlay.rect,HOST_FOCUS,plane, self.config.ascii)
        inner=overlay.rect.inset(1)
        if w>=5:
            title=' '+overlay.title+' '
            canvas.text(x+1,y,title,HOST_FOCUS,plane,Rect(x+1,y,max(0,w-5),1),self.config.ascii)
            canvas.text(x+w-4,y,'[x]',HOST_CHROME,plane)
            self.add_hit(Rect(x+w-4,y,3,1),'overlay.cancel',z=plane)
        overlay.list_y=inner.y
        if overlay.kind=='menu':
            self.paint_overlay_items(canvas,overlay,inner,plane)
        elif overlay.kind in ('palette','theme'):
            if overlay.editor:
                overlay.input_y=inner.y
                self.paint_field(canvas,overlay,Rect(inner.x,inner.y,inner.w,1),plane)
                overlay.list_y=inner.y+2
            else:
                canvas.text(inner.x,inner.y,'Preview: arrows · commit: Enter · cancel: Esc',HOST_DIALOG,plane,inner,self.config.ascii)
                overlay.list_y=inner.y+2
            self.paint_overlay_items(canvas,overlay,Rect(inner.x,overlay.list_y,inner.w,max(0,inner.bottom-overlay.list_y)),plane)
        elif overlay.kind=='input':
            lines=textwrap.wrap(display_text(overlay.message,self.config.ascii),max(1,inner.w))
            for offset,line in enumerate(lines[:max(0,inner.h-3)]):
                canvas.text(inner.x,inner.y+offset,line,HOST_DIALOG,plane,inner,self.config.ascii)
            overlay.input_y=min(inner.bottom-2,inner.y+min(len(lines),max(0,inner.h-3))+1)
            overlay.input_y=max(inner.y,overlay.input_y)
            self.paint_field(canvas,overlay,Rect(inner.x,overlay.input_y,inner.w,1),plane)
            if inner.h>=3:
                self.paint_button(canvas,'[ Accept: Enter ]',Rect(inner.x,inner.bottom-1,min(19,inner.w),1),'overlay.accept',plane)
                if inner.w>20: self.paint_button(canvas,'[ Cancel: Esc ]',Rect(inner.x+20,inner.bottom-1,min(16,inner.w-20),1),'overlay.cancel',plane)
        elif overlay.kind=='confirm':
            lines=textwrap.wrap(display_text(overlay.message,self.config.ascii),max(1,inner.w))
            count=min(len(lines),max(0,inner.h-len(overlay.actions)-1))
            for offset,line in enumerate(lines[:count]): canvas.text(inner.x,inner.y+offset,line,HOST_DIALOG,plane,inner,self.config.ascii)
            base=max(inner.y,inner.bottom-len(overlay.actions))
            for i,(label,_) in enumerate(overlay.actions):
                iy=base+i
                if iy>=inner.bottom: break
                ink=HOST_SELECTED if i==overlay.index else HOST_DIALOG
                r=Rect(inner.x,iy,inner.w,1); canvas.fill(r,ink,plane=plane)
                canvas.text(inner.x,iy,('> ' if i==overlay.index else '  ')+label,ink,plane,r,self.config.ascii)
                self.add_hit(r,'overlay.action',{'row':i},role='overlay.row',z=plane)
        else:
            message=overlay.runner.output if overlay.runner else overlay.message
            if overlay.runner:
                runner=overlay.runner
                result='Running' if runner.running else ('Cancelled' if runner.cancelled else 'Exit code: '+str(runner.returncode))
                message+='\n\n['+result+(' · output truncated' if runner.truncated else '')+']'
            lines=[]
            for raw in str(message).split('\n'):
                lines.extend(textwrap.wrap(display_text(raw,self.config.ascii),max(1,inner.w),replace_whitespace=False,drop_whitespace=False) or [''])
                if len(lines)>20000: break
            rh=max(0,inner.h-1)
            overlay.scroll=max(0,min(overlay.scroll,max(0,len(lines)-rh)))
            for offset,line in enumerate(lines[overlay.scroll:overlay.scroll+rh]):
                canvas.text(inner.x,inner.y+offset,line,HOST_DIALOG,plane,inner,self.config.ascii)
            label='[ Cancel process: Ctrl+C ]' if overlay.runner and overlay.runner.running else '[ Close: Escape ]'
            command='script.cancel' if overlay.runner and overlay.runner.running else 'overlay.cancel'
            if inner.h: self.paint_button(canvas,label,Rect(inner.x,inner.bottom-1,min(len(label),inner.w),1),command,plane)

    def paint_button(self, canvas: Canvas, text: str, rect: Rect, command: str, plane: int) -> None:
        canvas.fill(rect,HOST_FOCUS,plane=plane); canvas.text(rect.x,rect.y,text,HOST_FOCUS,plane,rect,self.config.ascii)
        self.add_hit(rect,command,z=plane)

    def paint_overlay_items(self, canvas: Canvas, overlay: Overlay, rect: Rect, plane: int) -> None:
        overlay.list_y=rect.y
        overlay.scroll=max(0,min(overlay.scroll,max(0,len(overlay.items)-max(1,rect.h))))
        if overlay.index<overlay.scroll: overlay.scroll=overlay.index
        if overlay.index>=overlay.scroll+rect.h and rect.h: overlay.scroll=overlay.index-rect.h+1
        for offset in range(rect.h):
            i=overlay.scroll+offset
            if i>=len(overlay.items): break
            item=overlay.items[i]; y=rect.y+offset
            ink=HOST_SELECTED if i==overlay.index else HOST_DIALOG
            if not item.enabled: ink=replace(ink,dim=True)
            if item.destructive: ink=replace(ink,fg='#ff9c9c')
            canvas.fill(Rect(rect.x,y,rect.w,1),ink,plane=plane)
            label=('> ' if i==overlay.index else '  ')+item.label
            canvas.text(rect.x,y,label,ink,plane,Rect(rect.x,y,max(0,rect.w-1),1),self.config.ascii)
            if item.children and rect.w: canvas.text(rect.right-1,y,'>',ink,plane)
            if i==overlay.hover and i!=overlay.index and rect.w:
                canvas.put(rect.x,y,':',replace(HOST_DIALOG,underline=True),plane)
            if item.enabled:
                self.add_hit(Rect(rect.x,y,rect.w,1),'overlay.row',{'row':i,'tag':overlay.tag},role='overlay.row',z=plane)

    def paint_field(self, canvas: Canvas, overlay: Overlay, rect: Rect, plane: int) -> None:
        if not overlay.editor or rect.w<1 or rect.h<1: return
        doc=overlay.editor; cursor=width_of(doc.text[:doc.cursor],self.config.ascii)
        if cursor<overlay.field_scroll: overlay.field_scroll=cursor
        elif cursor>=overlay.field_scroll+rect.w: overlay.field_scroll=cursor-rect.w+1
        canvas.fill(rect,HOST_FIELD,plane=plane); column=0
        for unit in display_units(doc.text,self.config.ascii):
            x=rect.x+column-overlay.field_scroll
            selected=bool(doc.selection and unit.start<doc.selection[1] and unit.end>doc.selection[0])
            ink=HOST_SELECTED if selected else HOST_FIELD
            if x>=rect.x and x+unit.width<=rect.right: canvas.put(x,rect.y,unit.glyph,ink,plane)
            column+=unit.width
        self.add_hit(rect,'overlay.field',role='overlay.field',z=plane)
        if overlay is self.overlays[-1]:
            cx=max(rect.x,min(rect.right-1,rect.x+cursor-overlay.field_scroll))
            self.screen_cursor=Point(x=cx,y=rect.y); self.show_cursor=True
            cell=canvas.cells[rect.y*canvas.width+cx]
            if not cell.continuation: canvas.put(cx,rect.y,cell.glyph or ' ',HOST_CARET,plane+1)

    # =========================================================================
    # 12. Bounded asynchronous work and portable prompt_toolkit terminal adapter
    # =========================================================================
    def tick_once(self, now: float | None = None) -> None:
        now=time.monotonic() if now is None else now
        elapsed=max(0,min(.25,now-self.last_tick)); self.last_tick=now
        if self.terminal_focused: self.clock+=elapsed
        if self.worker:
            result=self.worker.poll()
            if result:
                generation,width,payload=result
                if self.accept_preview(generation,width,payload): self.invalidate()
            desired=(self.doc.generation,self.preview_width)
            if desired!=self.requested_parse and now-self.doc.last_edit>=self.config.get('session','parse_debounce_ms')/1000:
                self.worker.request(self.doc.text,*desired,self.config.ascii); self.requested_parse=desired
        if self.capture and self.capture.kind=='editor' and self.geometry and Tile.EDIT in self.geometry.contents:
            rect=self.geometry.contents[Tile.EDIT]; x,y=self.capture.pointer
            delta=-1 if y<rect.y else 1 if y>=rect.bottom else 0
            if delta:
                memory=self.memories[Tile.EDIT]
                memory.scroll=max(0,min(max(0,self.doc.line_count-1),memory.scroll+delta))
                self.drag({'x':x,'y':y}); self.invalidate()
        if self.hover_due and now>=self.hover_due[2]:
            tag,index,_=self.hover_due; self.hover_due=None
            overlay=next((o for o in self.overlays if o.tag==tag),None)
            if overlay and overlay.hover==index and index<len(overlay.items) and overlay.items[index].children:
                self.open_submenu(overlay,index); self.invalidate()
        if self.hover_close_due and now>=self.hover_close_due[1]:
            tag,_=self.hover_close_due; self.hover_close_due=None
            for index,overlay in enumerate(self.overlays):
                if overlay.tag==tag:
                    del self.overlays[index+1:]; self.invalidate(); break
        if self.store:
            try:
                if self.doc.dirty and self.doc.generation!=self._last_recovery_gen and now-self._last_recovery_time>=self.config.get('session','recovery_seconds'):
                    self.store.recovery(self.doc,self.files.root)
                    self._last_recovery_gen=self.doc.generation; self._last_recovery_time=now
                if now-self._last_session_time>=self.config.get('session','save_seconds'):
                    self.persist_session(); self._last_session_time=now
            except (MDBMError,OSError) as exc:
                self._last_recovery_time=now; self._last_session_time=now
                self.status('State write failed: '+display_text(exc),True)
        if self.terminal_focused and self.config.motion not in ('off','reduced') and self.theme.animations:
            self.invalidate()

    async def ticker(self) -> None:
        while not self.exit_requested:
            self.tick_once()
            await asyncio.sleep(1/max(1,min(30,self.theme.fps,self.config.get('session','fps'))))

    def run(self, input_: Any = None, output: Any = None) -> None:
        control=WorkspaceControl(self)
        window=Window(content=control,wrap_lines=False,always_hide_cursor=False,
                      dont_extend_width=False,dont_extend_height=False)
        depth={'truecolour':ColorDepth.DEPTH_24_BIT,'256':ColorDepth.DEPTH_8_BIT,
               '16':ColorDepth.DEPTH_4_BIT,'mono':ColorDepth.DEPTH_1_BIT}[self.config.depth]
        self.app=Application(layout=Layout(window),key_bindings=self.build_bindings(),
            full_screen=True,mouse_support=self.config.get('capabilities','mouse'),
            color_depth=depth,style=PTStyle([]),min_redraw_interval=1/60,
            max_render_postpone_time=.03,input=input_,output=output)
        self.worker=PreviewWorker()
        def ready() -> None:
            self.last_tick=time.monotonic(); self.create_task(self.ticker())
        try:
            with terminal_focus_protocol(self.app,self.config.get('capabilities','terminal_focus')):
                self.app.run(pre_run=ready,set_exception_handler=False)
        finally:
            if self.worker: self.worker.close()
            if self.store:
                try:
                    if self.doc.dirty and not self.clean_exit: self.store.recovery(self.doc,self.files.root)
                    self.persist_session()
                except (MDBMError,OSError) as exc:
                    self.diagnostics.append('Shutdown recovery failed: '+display_text(exc))
                self.store.close()
            self.app=None


# Colours and styles for protected controls are host literals, never theme data.
HOST_CHROME=Ink('#dce3ec','#172132')
HOST_FOCUS=Ink('#ffffff','#245c77',bold=True)
HOST_SELECTED=Ink('#ffffff','#315998',bold=True,mono_reverse=True)
HOST_CARET=Ink('#101824','#ffffff',bold=True,mono_reverse=True)
HOST_ERROR=Ink('#ffffff','#7a2430',bold=True)
HOST_DIALOG=Ink('#f0f3f6','#182436')
HOST_FIELD=Ink('#ffffff','#0d1420')


class WorkspaceControl(UIControl):
    """PTY/Win32 backend translates events; it never owns application geometry."""
    def __init__(self, workspace: Workspace): self.workspace=workspace
    def is_focusable(self) -> bool: return True

    def create_content(self, width: int, height: int) -> UIContent:
        canvas=self.workspace.frame(width,height)
        lines=[canvas.line_fragments(y,self.workspace.config.depth) for y in range(canvas.height)]
        return UIContent(get_line=lambda y:lines[y] if 0<=y<len(lines) else [],
            line_count=canvas.height,cursor_position=self.workspace.screen_cursor,
            show_cursor=self.workspace.show_cursor)

    def mouse_handler(self, event: Any) -> None:
        types={MouseEventType.MOUSE_DOWN:'down',MouseEventType.MOUSE_UP:'up',
               MouseEventType.MOUSE_MOVE:'move',MouseEventType.SCROLL_UP:'scroll_up',
               MouseEventType.SCROLL_DOWN:'scroll_down'}
        buttons={MouseButton.LEFT:'left',MouseButton.RIGHT:'right',MouseButton.MIDDLE:'middle'}
        type_=types.get(event.event_type)
        if type_:
            self.workspace.mouse(type_,event.position.x,event.position.y,buttons.get(event.button,'left'),
                                 MouseModifier.SHIFT in event.modifiers)


@contextlib.contextmanager
def terminal_focus_protocol(app: Application, enabled: bool = True) -> Iterator[None]:
    """Host-owned VT focus reporting. Restore the input table even after crashes.

    prompt_toolkit exposes no portable focus-in/out binding; these two otherwise
    unused function keys are reserved internally. Win32 non-VT output simply
    skips the protocol. No theme or document bytes enter this channel.
    """
    if not enabled or not hasattr(app.output,'write_raw'):
        yield; return
    from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
    sentinel=object(); previous={k:ANSI_SEQUENCES.get(k,sentinel) for k in ('\x1b[I','\x1b[O')}
    ANSI_SEQUENCES['\x1b[I']=Keys.F23; ANSI_SEQUENCES['\x1b[O']=Keys.F24
    try:
        app.output.write_raw('\x1b[?1004h'); app.output.flush()
        yield
    finally:
        with contextlib.suppress(Exception): app.output.write_raw('\x1b[?1004l'); app.output.flush()
        for key,value in previous.items():
            if value is sentinel: ANSI_SEQUENCES.pop(key,None)
            else: ANSI_SEQUENCES[key]=value


# =============================================================================
# 13. CLI: validation/diagnostics are noninteractive and never write recovery state
# =============================================================================
def main(argv: Sequence[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description='MDBM: a cell-native Markdown terminal workspace')
    parser.add_argument('path',nargs='?',type=Path,help='Markdown file or workspace directory')
    parser.add_argument('--config',type=Path,default=APP_DIR/'mdbm.conf' if (APP_DIR/'mdbm.conf').is_file() else Path.cwd()/'mdbm.conf')
    parser.add_argument('--theme',metavar='ID',help='Stable theme ID')
    parser.add_argument('--safe-theme',action='store_true',help='Use the embedded minimal theme')
    parser.add_argument('--validate-theme',type=Path,metavar='PATH',help='Validate a theme without launching')
    parser.add_argument('--no-restore',action='store_true',help='Do not restore session or recovery snapshots')
    parser.add_argument('--diagnostics',action='store_true',help='Print capabilities, versions and validation diagnostics')
    parser.add_argument('--snapshot',metavar='WIDTHxHEIGHT',help='Deterministic plain-text render; does not write session state')
    parser.add_argument('--version',action='version',version='MDBM '+VERSION)
    args=parser.parse_args(argv)
    try:
        config=load_config(args.config)
        if args.validate_theme:
            directories=[config.base/Path(p) for p in config.get('appearance','theme_directories')]
            directories.extend([DATA_DIR/'themes',args.validate_theme.absolute().parent])
            catalog=ThemeCatalog(directories)
            raw=ThemeCompiler.read(args.validate_theme)
            theme=ThemeCompiler({**catalog.raw,raw.get('id','candidate'):raw}).compile(raw)
            print(f'VALID {theme.id} · md-editor-theme/1.1 · {len(theme.layers)} layers · SHA256 {theme.digest}')
            return 0
        headless=bool(args.diagnostics or args.snapshot)
        workspace=Workspace(config,args.path,args.theme,args.safe_theme,not args.no_restore,persist=not headless)
        if args.diagnostics:
            print(workspace.diagnostics_text()); return 0
        if args.snapshot:
            match=re.fullmatch(r'(\d{1,4})x(\d{1,3})',args.snapshot)
            if not match: raise MDBMError('--snapshot expects WIDTHxHEIGHT')
            width,height=map(int,match.groups())
            if not 8<=width<=400 or not 4<=height<=120: raise MDBMError('Snapshot bounds: 8..400 columns, 4..120 rows')
            workspace.clock=0; workspace.session_seed=17
            workspace.frame(width,height); workspace.parse_sync(workspace.preview_width)
            print(workspace.frame(width,height).plain()); return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            if workspace.store: workspace.store.close()
            raise MDBMError('Interactive mode requires a terminal. Use --snapshot or --diagnostics for headless output.')
        workspace.run(); return 0
    except KeyboardInterrupt:
        print('MDBM interrupted. Unsaved content was retained in recovery where writable.',file=sys.stderr)
        return 130
    except (MDBMError,OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError) as exc:
        print('MDBM: '+display_text(str(exc),not sys.stderr.isatty()),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
