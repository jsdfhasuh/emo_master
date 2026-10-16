"""Explicit local-only P2 demo: one real Job, two read-only network clients."""
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

import grpc  # noqa: E402
from examples.runtime_pages_p2 import sampleProject  # noqa: E402
from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer  # noqa: E402
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb  # noqa: E402
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc  # noqa: E402
from emo_master.apps.runtime.grpc_server.service import RuntimeService  # noqa: E402
from emo_master.apps.runtime.presentation.service import PresentationService  # noqa: E402
from emo_master.clients.runtime.display_session import DisplaySession  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="emo-p2-demo-") as directory:
        root = Path(directory)
        runtime = RuntimeService(dbPath=root / "runtime.sqlite3", workspaceRoot=root / "legacy-jobs")
        presentation = PresentationService(runtime, root / "display")
        server = AioRuntimeServer(runtime, presentation)
        clients = []
        channel = grpc.insecure_channel(f"127.0.0.1:{server.port}")
        try:
            stub = rpc.DisplayServiceStub(channel)
            prepared = stub.Prepare(pb.DisplayPrepareRequest(project_json=sampleProject(root).model_dump_json(), resource_root=str(root)), timeout=15)
            assert not stub.ListJobs(pb.DisplayEmpty()).jobs
            job = stub.Start(pb.DisplayStartRequest(prepared_id=prepared.prepared_id), timeout=5).job_id
            clients = [DisplaySession(f"127.0.0.1:{server.port}", job) for _ in range(2)]
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not all(c.stats["decoded"] for c in clients):
                time.sleep(.02)
            outputs = [c.latest["root"] for c in clients]
            assert outputs[0][0] == outputs[1][0]
            assert all(result.status == "COMPLETE" and result.sources[0].valueJson == "2" and not errors
                       for result, images, errors in outputs)
            assert (outputs[0][1]["image"] == outputs[1][1]["image"]).all()
            clients[0].close()
            assert stub.Snapshot(pb.DisplayRequest(job_id=job, runtime_instance_id=presentation.runtimeInstanceId), timeout=.5).results
            assert len(stub.ListJobs(pb.DisplayEmpty()).jobs) == 1
            print(json.dumps({"job": job, "same_result": outputs[0][0].identity.resultKey,
                "count": 2, "image_sha256": outputs[0][0].sources[1].image.sha256,
                "clients": [dict(c.stats) for c in clients], "resources": presentation.resourceStats(),
                "single_client_exit_preserved_job": True}, indent=2))
            clients.pop(0)
        finally:
            for client in clients:
                client.close()
            channel.close()
            server.close()
            runtime.close()
            presentation.close()
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
