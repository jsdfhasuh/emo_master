"""Supervised P4 validation; preserve every run and its exact source identity."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

from p1_validate import git, run
from p2_validate import identity as p2Identity

ROOT=Path(__file__).resolve().parents[1]


def identity():
    result=p2Identity()
    for path in (ROOT/'examples').rglob('*.json'):
        result['files'][path.relative_to(ROOT).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    result['digest']=hashlib.sha256(json.dumps(result['files'],sort_keys=True).encode()).hexdigest()
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--suite',choices=['ui','regression','ci','visual'],required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=float,default=300)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    commands={
        'ui':[[sys.executable,'-m','pytest','-q','tests/ui/page_designer','tests/ui/presentation','tests/core/presentation','-rs'],
              [sys.executable,'-m','ruff','check','src','tests'],
              [sys.executable,'-m','mypy','--config-file','mypy.ini','src']],
        'regression':[[sys.executable,'-m','pytest','-q','tests/designer','tests/runtime/presentation','-rs'],
                      [sys.executable,'scripts/gen_proto.py','--check']],
        'ci':[[sys.executable,'scripts/ci_check.py']],
        'visual':[[sys.executable,'scripts/p4_visual_check.py','--output',str(args.output/'screens')]],
    }
    report={'started_utc':datetime.now(timezone.utc).isoformat(),'head':git('rev-parse','HEAD'),
        'dirty':git('status','--short'),'python':sys.version,'os':platform.platform(),'executable':sys.executable,
        'versions':{name:importlib.metadata.version(name) for name in ['PySide2','grpcio','protobuf','numpy','opencv-python']},
        'code_before':identity(),'results':[]}
    for index,command in enumerate(commands[args.suite]):
        result=run(command,ROOT,args.output/f'{index+1}.log',args.timeout)
        report['results'].append(result)
        print(result,flush=True)
    report['code_after']=identity()
    report['code_stable']=report['code_before']==report['code_after']
    (args.output/'evidence.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return int(not report['code_stable'] or any(r['status']!='PASS' for r in report['results']))


if __name__=='__main__':
    raise SystemExit(main())
