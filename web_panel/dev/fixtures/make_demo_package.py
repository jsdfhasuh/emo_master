"""Draft renderer fixture; not an official Designer export or .emoproj."""
import json
import sys
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from emo_web_panel.mock_runtime import demo_panel
panel=demo_panel()
presentation={'pages':panel['pages']}
manifest={'format':'emo-web-panel-1','projectId':'demo-project','packageRevision':'draft-1','panelContractVersion':'draft-unverified','rendererRequirements':[],'presentationFile':'presentation.json','assets':[]}
path=Path(__file__).with_name('demo.emoweb')
with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
 z.writestr('manifest.json',json.dumps(manifest));z.writestr('presentation.json',json.dumps(presentation))
print(path)
