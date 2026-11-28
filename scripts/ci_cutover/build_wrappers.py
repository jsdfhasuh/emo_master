"""Generate review-only skeletons in a chosen temp directory; never deploy."""
import argparse
import json
from pathlib import Path
import re
import sys
try:
    import yaml
except ImportError:
    raise SystemExit("PyYAML required for review generation") from None


def main():
    p=argparse.ArgumentParser()
    p.add_argument("inventory",type=Path)
    p.add_argument("out",type=Path)
    a=p.parse_args()
    if a.out.exists() or ".github" in a.out.resolve().parts:
        raise SystemExit("must create a fresh review-only output directory outside .github")
    raw=json.loads(a.inventory.read_text(encoding="utf-8"))
    if len(raw["routes"])!=12:
        raise SystemExit("unexpected route count")
    a.out.mkdir()
    for original,row in raw["routes"].items():
        if not re.fullmatch("[a-z0-9-]+[.]yml",original):
            raise SystemExit("unexpected workflow path")
        # Keep GitHub as the authority for trigger filters; don't reinterpret globs.
        triggers=row["triggers"]
        wd=triggers.get("workflow_dispatch")
        if wd is not None and isinstance(wd,dict):
            for spec in wd.get("inputs",{}).values():
                if spec.get("required") in ("true","false"):
                    spec["required"]=spec["required"]=="true"
        for event in triggers:
            if triggers[event]=="":
                triggers[event]=None
        config=dict(name=row["workflow_name"],on=triggers,permissions={"contents":"read"})
        if row.get("concurrency"):
            config["concurrency"]=row["concurrency"]
        config["jobs"]={"emit":{"if":"vars.EMO_CI_ROUTING_MODE == 'staged'",
                                "uses":"./.github/workflows/emit-ci-cutover-request.yml"}}
        data=yaml.safe_dump(config,sort_keys=False,allow_unicode=True,width=1000)
        (a.out/(original+".review")).write_text(
            "# Review only: replacing the active original requires approved rollout.\n"+data,encoding="utf-8")
    print("review-only workflow skeletons: 12")


if __name__=="__main__":
    main()
