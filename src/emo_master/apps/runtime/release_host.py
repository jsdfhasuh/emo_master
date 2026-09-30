"""Explicit local test-release host. Viewer lifetime never owns this service."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import re
import threading

import grpc

from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.core.project.delivery_store import DeliveryStore, DirectoryOwner, atomicJson
from emo_master.core.project.test_delivery import readJson


class TestReleaseChannel(PresentationService):
    """Single validated release per host; new RPC Prepare cannot inject a draft."""
    supportsNormalCapture = False

    def prepare(self, project, resourceRoot, **kwargs):
        if self.prepared or kwargs.get('mode') != 'release' or not kwargs.get('releaseRevision'):
            raise ValueError('test host accepts only its explicitly activated release')
        return super().prepare(project, resourceRoot, **kwargs)

    def start(self, preparedId, **kwargs):
        with self.lock:
            if getattr(self, 'startedOnce', False):
                raise ValueError('one explicit test run per host; stop and reopen host for another run')
            # Even a failed/ambiguous Start must not be automatically repeated.
            if preparedId not in self.prepared:
                raise ValueError('unknown prepared release')
            self.startedOnce = True
            return super().start(preparedId, **kwargs)


class ReleaseHost:
    def __init__(self, storeRoot, dataRoot, *, port=0, siteValues=None):
        self.owners = ExitStack()
        self.runtime = self.presentation = self.server = None
        self.closed = False
        self.store = DeliveryStore(storeRoot)
        self.dataRoot = Path(dataRoot).resolve()
        if self.dataRoot.is_relative_to(self.store.root):
            raise ValueError('runtime data must be outside the immutable project store')
        try:
            self.document, self.manifest, self.projectRoot = self.owners.enter_context(self.store.selected())
            self.owners.enter_context(DirectoryOwner(self.dataRoot))
            self.runtime = RuntimeService(dbPath=self.dataRoot/'runtime.sqlite3', workspaceRoot=self.dataRoot/'jobs')
            self.presentation = TestReleaseChannel(self.runtime, self.dataRoot/'display')
            # Real release semantics: stable project release DB/outputs, explicit revision.
            self.prepared = self.presentation.prepare(self.document, self.projectRoot,
                mode='release', releaseRevision=self.manifest['revision'], siteValues=siteValues)
            self.server = AioRuntimeServer(self.runtime, self.presentation, port=port)
            self.address = f'127.0.0.1:{self.server.port}'
            with grpc.insecure_channel(self.address) as channel:
                capabilities = rpc.DisplayServiceStub(channel).Capabilities(pb.DisplayEmpty(), timeout=3)
                if capabilities.runtime_instance_id != self.presentation.runtimeInstanceId:
                    raise RuntimeError('readiness identity mismatch')
            self.info = {'format': 'emo-test-host-1', 'runtimeReady': True, 'projectReady': True,
                'address': self.address, 'runtimeInstanceId': capabilities.runtime_instance_id,
                'revision': self.manifest['revision'], 'preparedId': self.prepared.snapshot.snapshotId,
                'executionRevision': self.prepared.snapshot.executionRevision,
                'capturePlanRevision': self.prepared.snapshot.capturePlanRevision,
                'store': str(self.store.root), 'data': str(self.dataRoot)}
        except BaseException:
            self.close()
            raise

    def writeReady(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.dataRoot):
            raise ValueError('ready descriptor must be in the owned Runtime data directory')
        path.parent.mkdir(parents=True, exist_ok=True)
        atomicJson(path, self.info)

    def close(self):
        if self.closed:
            return
        # Only the host's explicit stop command invokes this, never a viewer close.
        if self.server:
            self.server.close()
            self.server = None
        # The Runtime owner reaps actual workers before disposing presentation
        # resources. A terminal event alone can precede process/bridge retirement.
        # On failure retain all owner references and directory locks for retry.
        if self.runtime:
            self.runtime.close()
        if self.presentation:
            self.presentation.close()
            for preparedId in list(self.presentation.prepared):
                self.presentation.discardPrepared(preparedId)
            self.presentation = None
        self.owners.close()
        self.closed = True


def requestStop(readyFile):
    path = Path(readyFile).resolve()
    if path.stat().st_size > 16 * 1024:
        raise ValueError('host descriptor budget')
    info = readJson(path.read_bytes())
    if (info.get('format') != 'emo-test-host-1' or
            not re.fullmatch(r'[0-9a-f-]{36}', info['runtimeInstanceId']) or
            not re.fullmatch(r'127\.0\.0\.1:[0-9]{1,5}', info['address'])):
        raise ValueError('invalid host descriptor')
    data = Path(info['data']).resolve(strict=True)
    if not path.is_relative_to(data):
        raise ValueError('descriptor is outside its owned data directory')
    with grpc.insecure_channel(info['address']) as channel:
        actual = rpc.DisplayServiceStub(channel).Capabilities(pb.DisplayEmpty(), timeout=3)
        if actual.runtime_instance_id != info['runtimeInstanceId']:
            raise ValueError('stale host; refusing to stop another Runtime')
    atomicJson(data/('stop-' + info['runtimeInstanceId'] + '.json'), {'instance': info['runtimeInstanceId']})


def main(arguments=None):
    parser = argparse.ArgumentParser(description='Local P5-A test release Runtime; no automatic detection')
    parser.add_argument('--store', type=Path)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--ready-file', type=Path, required=True)
    parser.add_argument('--stop', action='store_true', help='Explicitly stop the matching owned test host')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--site', action='append', default=[], metavar='FIELD=RELATIVE_PATH')
    args = parser.parse_args(arguments)
    if args.stop:
        requestStop(args.ready_file)
        return 0
    if not args.store or not args.data:
        parser.error('--store and --data are required to open a test host')
    sites = {}
    for item in args.site:
        key, separator, value = item.partition('=')
        if not separator or not value or key in sites:
            parser.error('each --site must be a unique FIELD=RELATIVE_PATH')
        sites[key] = value
    host = ReleaseHost(args.store, args.data, port=args.port, siteValues=sites)
    stopFile = host.dataRoot/('stop-' + host.info['runtimeInstanceId'] + '.json')
    try:
        host.writeReady(args.ready_file)
        print('Runtime ready; project ready; no Job started. Use --stop --ready-file or Ctrl+C to stop this host.', flush=True)
        print(host.address, flush=True)
        # Do not keep a synchronous ReadFile on stdin pending while Windows
        # CreateProcess creates a spawn worker (observed to block process creation).
        pause = threading.Event()
        while not pause.wait(.1):
            if stopFile.exists() and stopFile.stat().st_size <= 1024 and readJson(stopFile.read_bytes()) == {
                    'instance': host.info['runtimeInstanceId']}:
                break
    except KeyboardInterrupt:
        pass
    finally:
        host.close()
        args.ready_file.unlink(missing_ok=True)
        stopFile.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
