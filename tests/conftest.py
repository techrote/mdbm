from pathlib import Path
import pytest
import mdbm as m

@pytest.fixture
def workspace(tmp_path):
    conf=m.load_config(tmp_path/'mdbm.conf',raw={
        'schema':'mdbm-config/1.0',
        'application':{'root':str(tmp_path),'state_directory':str(tmp_path/'.mdbm')},
        'appearance':{'theme':'minimal','seed':17,'motion':'off'},
        'capabilities':{'colour_depth':'truecolour'},
    })
    w=m.Workspace(conf,restore=False,persist=False)
    w.set_document(m.DocumentModel('# One\n\nalpha beta\n\n## Two\n\nlast line\n'))
    w.frame(120,40); w.parse_sync(w.preview_width); w.frame(120,40)
    return w
