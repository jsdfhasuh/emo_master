from __future__ import annotations

import multiprocessing
import sys
from typing import Sequence


def main(arguments: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    commandArguments = list(sys.argv[1:] if arguments is None else arguments)
    if "--self-test" in commandArguments:
        from emo_master.apps.package_selftest import runSelfTestCommand

        return runSelfTestCommand(commandArguments)
    if commandArguments:
        raise SystemExit(f"unsupported arguments: {' '.join(commandArguments)}")

    from emo_master.apps.designer.main import runDesigner

    runDesigner()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
