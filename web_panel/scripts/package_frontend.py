from pathlib import Path
import shutil
root=Path(__file__).resolve().parents[1]
source=root/'frontend'/'dist'
if not (source/'index.html').is_file():raise SystemExit('Build frontend first')
target=root/'backend'/'emo_web_panel'/'static'
if target.exists():shutil.rmtree(target)
shutil.copytree(source,target)
print('Packaged frontend static assets')
