"""Install the built wheel outside source checkout, without Qt or Node runtime."""
import os
from pathlib import Path
import subprocess
import tempfile
import venv

wheel=next(Path('dist').glob('*.whl')).resolve()
with tempfile.TemporaryDirectory(prefix='emo-web-install-') as temporary:
    root=Path(temporary);venv.create(root/'venv',with_pip=True)
    python=root/'venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    subprocess.run([str(python),'-m','pip','install',str(wheel)],check=True,cwd=root)
    check='''import sys,tempfile
from pathlib import Path
import emo_web_panel
from emo_web_panel.app import create_app
from emo_web_panel.config import Config
assert Path(emo_web_panel.__file__).parent.joinpath('static/index.html').is_file()
with tempfile.TemporaryDirectory() as d:
 app=create_app(Config(Path(d),mode='mock'))
 assert any(getattr(route,'name','')=='frontend' for route in app.routes)
assert not any(name in sys.modules for name in ('PySide2','PyQt5','emo_master','cv2','torch'))
print('PASS independent wheel install, packaged UI, no Qt/Runtime/model imports')
'''
    subprocess.run([str(python),'-c',check],check=True,cwd=root)
    subprocess.run([str(python),'-m','emo_web_panel.main','--help'],check=True,cwd=root)
