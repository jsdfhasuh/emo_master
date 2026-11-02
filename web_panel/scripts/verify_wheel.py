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
    subprocess.run([str(python),'-m','pip','install',str(next(Path('contracts/panel-contract-v1/python').glob('*.whl')).resolve()),str(wheel)],check=True,cwd=root)
    check='''import sys,tempfile
from pathlib import Path
from importlib.metadata import version
import emo_panel_protocol
from emo_panel_protocol.messages import BrowserMessage
from emo_panel_protocol.generated import runtime_pb2
import emo_web_panel
from emo_web_panel.app import create_app
from emo_web_panel.config import Config
package_root=Path(emo_web_panel.__file__).parent
assert package_root.joinpath('static/index.html').is_file()
assert version('emo-panel-protocol') == emo_panel_protocol.__version__ == '1.1.1'
expected={
 'ungrouped-running':('job_state','RUNNING'),
 'ungrouped-aborted':('job_state','ABORTED'),
 'ungrouped-job-changed':('job_changed',None),
 'ungrouped-start-accepted':('control_result','ACCEPTED'),
 'ungrouped-stop-accepted':('control_result','ACCEPTED'),
}
for name,(event,state) in expected.items():
 fixture=package_root/'contract_data'/'fixtures'/(name+'.json')
 message=BrowserMessage.model_validate_json(fixture.read_bytes()).root
 assert message.event == event and message.runGroupId == '', name
 if state is not None:
  assert getattr(message,'status',getattr(message,'outcome',None)) == state, name
stop=runtime_pb2.StopJobRequest(job_id='ordinary-job',
 expected_runtime_instance_id='fixture-runtime',expected_project_id='fixture-project',
 expected_run_group_id='')
assert stop.HasField('expected_run_group_id') and stop.expected_run_group_id == ''
wire=runtime_pb2.StopJobRequest.FromString(stop.SerializeToString())
assert wire.HasField('expected_run_group_id') and wire.expected_run_group_id == ''
with tempfile.TemporaryDirectory() as d:
 app=create_app(Config(Path(d),mode='mock'))
 assert any(getattr(route,'name','')=='frontend' for route in app.routes)
assert not any(name in sys.modules for name in ('PySide2','PyQt5','emo_master','cv2','torch'))
print('PASS independent wheel install, SDK1.1.1, five embedded ordinary-Job fixtures including ABORTED, exact empty Stop presence, packaged UI, no Qt/Runtime/model imports')
'''
    subprocess.run([str(python),'-c',check],check=True,cwd=root)
    subprocess.run([str(python),'-m','emo_web_panel.main','--help'],check=True,cwd=root)
