"""Exercise a built wheel outside the source tree, including relocated data.

Usage: python tests/package_smoke.py wheelhouse
Dependencies must already be installed in the invoking interpreter. The wheel
alone is installed into two fresh temporary directories; no global files change.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


VERIFY = r'''
import json
from pathlib import Path
import sys
import mdbm as m

module_path = Path(sys.argv[1]).resolve()
data_path = Path(sys.argv[2]).resolve()
expected_themes = set(json.loads(sys.argv[3]))
assert Path(m.__file__).resolve() == module_path, m.__file__
assert m.DATA_DIR.resolve() == data_path, (m.DATA_DIR, data_path)
catalog = m.ThemeCatalog([m.DATA_DIR/'themes'])
assert not catalog.diagnostics, catalog.diagnostics
assert expected_themes <= set(catalog.compiled), expected_themes - set(catalog.compiled)
workspace = m.Workspace(m.load_config(Path.cwd()/'mdbm.conf'),
                        theme_id='neon-circuitry', restore=False, persist=False)
assert workspace.theme.id == 'neon-circuitry'
workspace.frame(80, 24)
workspace.parse_sync(workspace.preview_width)
canvas = workspace.frame(80, 24)
assert len(canvas.plain().splitlines()) == 24
assert not (Path.cwd()/'.mdbm').exists()
print(f'Installed {m.VERSION}: {len(expected_themes)} themes compile; 80x24 snapshot renders.')
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel_directory', type=Path)
    args = parser.parse_args()
    wheels = list(args.wheel_directory.resolve().glob('mdbm_workspace-*.whl'))
    if len(wheels) != 1:
        parser.error('Supply a directory containing exactly one MDBM wheel.')
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        themes = sorted(Path(name).stem for name in archive.namelist()
                        if '/data/share/mdbm/themes/' in name and name.endswith('.theme'))
    if not themes:
        raise SystemExit('The wheel contains no reference themes.')
    with tempfile.TemporaryDirectory(prefix='mdbm-package-smoke-') as temporary:
        temporary = Path(temporary)
        for scheme in ('target', 'prefix'):
            install = temporary/scheme
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps',
                            '--no-compile', '--no-warn-script-location', '--ignore-installed',
                            '--'+scheme, str(install), str(wheel)], check=True)
            modules = list(install.rglob('mdbm.py'))
            if len(modules) != 1:
                raise RuntimeError(f'{scheme}: expected one installed module, got {modules}')
            module = modules[0]
            environment = os.environ.copy()
            environment['PYTHONPATH'] = str(module.parent)
            work = temporary/('work-'+scheme)
            work.mkdir()
            subprocess.run([sys.executable, '-c', VERIFY, str(module),
                            str(install/'share'/'mdbm'), json.dumps(themes)],
                           cwd=work, env=environment, check=True)
            launchers = list(install.rglob('mdbm.exe' if os.name == 'nt' else 'mdbm'))
            launchers = [path for path in launchers if path.is_file()]
            if len(launchers) != 1:
                raise RuntimeError(f'{scheme}: expected one console entry point, got {launchers}')
            subprocess.run([str(launchers[0]), '--version'],
                           cwd=work, env=environment, check=True)
            print(f'{scheme}: installed package and console entry point passed.', flush=True)


if __name__ == '__main__':
    main()
