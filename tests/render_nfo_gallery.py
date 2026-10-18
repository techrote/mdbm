"""Generate the actual-cell NFO theme gallery; requires Pillow and MDBM dependencies."""
from render_appearance_gallery import main

THEMES = (
    'rust-bastion', 'void-cathedral', 'acid-overdrive',
    'cryo-vault', 'cinder-relic', 'static-revenant',
)

if __name__ == '__main__':
    main(THEMES, 'nfo', 'MDBM / Underground NFO collection', 140, 52)
