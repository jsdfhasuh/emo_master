import json,os,subprocess,sys,time
from pathlib import Path
root=Path.cwd(); dest=root/'manual_test_workspace/p0-round2/designer-isolated';dest.mkdir(exist_ok=True)
rows=[]
for path in sorted((root/'tests/designer').glob('test_*.py')):
 start=time.perf_counter()
 try:
  result=subprocess.run([sys.executable,'-m','pytest',str(path),'-q'],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',timeout=60)
  output=result.stdout; code=result.returncode
 except subprocess.TimeoutExpired as error:
  output=str(error);code='TIMEOUT'
 (dest/(path.stem+'.txt')).write_text(output,encoding='utf-8')
 rows.append(dict(file=str(path.relative_to(root)),exit=code,seconds=time.perf_counter()-start))
 (dest/'index.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
 print(path.name,code,flush=True)
