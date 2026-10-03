import argparse
import json
from pathlib import Path

from emo_master.core.project.delivery_store import DeliveryStore
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.package_builder import buildPageTestPackage


def main(arguments=None):
    parser = argparse.ArgumentParser(description='P5-A test projects only; never starts detection')
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export')
    export.add_argument('--project', required=True, type=Path)
    export.add_argument('--output', required=True, type=Path)
    for name in ['import', 'activate', 'rollback', 'status']:
        command = commands.add_parser(name)
        command.add_argument('--store', required=True, type=Path)
        if name == 'import':
            command.add_argument('--package', required=True, type=Path)
        if name == 'activate':
            command.add_argument('--revision', required=True)
    args = parser.parse_args(arguments)
    if args.command == 'export':
        doc = ProjectDocument.model_validate_json((args.project/'project.json').read_text(encoding='utf-8'))
        print(buildPageTestPackage(doc, args.project, args.output))
    else:
        store = DeliveryStore(args.store)
        if args.command == 'import':
            print(store.importPackage(args.package))
        elif args.command == 'activate':
            store.activate(args.revision)
        elif args.command == 'rollback':
            print(store.rollback())
        print(json.dumps(store.state()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
