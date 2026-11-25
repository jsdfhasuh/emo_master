"""Runtime startup recovery with a real crashed Runtime and a spawned image writer."""
from contextlib import contextmanager
import multiprocessing
import os
from pathlib import Path
import sys
import time

import pytest

from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.preview.store import _ioPath
from tests.runtime.production_fixture import waitFor


def _runUntilKilled(root, ready):
    from emo_master.apps.designer.state.project_store import saveProject
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.core.project.models import WorkflowEdge, WorkflowNode
    from tests.runtime.current_production_fixture import currentProductionProject, node
    from tests.runtime.test_background_task_job import probeRuntime

    runtime = probeRuntime(root)
    try:
        project, doc = currentProductionProject(root / "project")
        output = root / "结果"
        node(doc, "save").params["outputPath"] = str(output / "confirmed.png")
        workflow = doc.workflows["main"]
        workflow.nodes.insert(-1, WorkflowNode(nodeId="pause-save", operatorId="test.worker_probe",
            params={"stagedImage": str(output / "incomplete.png")}))
        workflow.edges.append(WorkflowEdge(fromNode="save", fromPort="result", toNode="pause-save", toPort="afterSave"))
        doc.dependencies.operators.append(dict(operatorId="test.worker_probe", version="1.0.0"))
        saveProject(project, doc.model_dump(mode="json"))
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
        start = runtime.StartJob(pb.StartJobRequest(project_id=doc.project.projectId, workflow_id="main"), None)
        assert start.ok, start.message
        waitFor((output / "incomplete.ready").is_file)
        ready.send(runtime.jobRepository.get(start.job_id).pid)
        time.sleep(60)
    finally:
        (root / "结果" / "incomplete.release").touch()
        runtime.close()


@contextmanager
def _workerHandle(pid):
    """Keep a handle to this specific child across its parent's exit and PID reuse."""
    if sys.platform == "win32":
        import _winapi
        handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE | 0x0001, False, pid)  # PROCESS_TERMINATE

        def stop():
            if _winapi.WaitForSingleObject(handle, 0) != _winapi.WAIT_OBJECT_0:
                _winapi.TerminateProcess(handle, 1)
            assert _winapi.WaitForSingleObject(handle, 10000) == _winapi.WAIT_OBJECT_0

        try:
            yield stop
        finally:
            stop()
            _winapi.CloseHandle(handle)
    else:
        import select
        import signal
        fd = os.pidfd_open(pid)

        def stop():
            if not select.select([fd], [], [], 0)[0]:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            assert select.select([fd], [], [], 10)[0]

        try:
            yield stop
        finally:
            stop()
            os.close(fd)


@pytest.mark.skipif(sys.platform not in {"win32", "linux"}, reason="requires process handle or pidfd")
@pytest.mark.parametrize("firstStartup", ["both-dead", "writer-alive", "delete-denied"])
def testRuntimeRestartReclaimsOnlyAbandonedImages(tmp_path, monkeypatch, firstStartup):
    ctx = multiprocessing.get_context("spawn")
    receive, send = ctx.Pipe(duplex=False)
    previous = ctx.Process(target=_runUntilKilled, args=(tmp_path, send))
    output = tmp_path / "结果"
    output.mkdir()
    # Even the exact staging filename pattern outside the owned area belongs to its writer.
    other = output / (".emo-image-" + "5" * 32 + ".tmp")
    other.write_bytes(b"external writer")
    target = output / "incomplete.png"

    def restart():
        return RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")

    previous.start()
    send.close()
    try:
        assert receive.poll(30), "previous Runtime did not reach image publication"
        with _workerHandle(receive.recv()) as stopWorker:
            staged, = (file for file in output.rglob(".emo-image-*.tmp") if file != other)
            assert staged.read_bytes().startswith(b"\x89PNG") and not target.exists()
            confirmed = output / "confirmed.png"
            published = confirmed.read_bytes()
            assert published.startswith(b"\x89PNG")
            previous.kill()
            previous.join(10)
            assert previous.exitcode is not None
            if firstStartup != "writer-alive":
                stopWorker()
            with monkeypatch.context() as patch:
                if firstStartup == "delete-denied":
                    unlink = Path.unlink

                    def denyStaged(path, *args, **kwargs):
                        if path == _ioPath(staged):
                            raise PermissionError("test output volume denies deletion")
                        return unlink(path, *args, **kwargs)

                    patch.setattr(Path, "unlink", denyStaged)
                current = restart()
                try:
                    assert staged.exists() == (firstStartup != "both-dead")
                    assert not target.exists()
                    assert confirmed.read_bytes() == published and other.read_bytes() == b"external writer"
                    if firstStartup == "delete-denied":
                        assert any(event.code == "E_IMAGE_STAGING_CLEANUP_DEFERRED"
                                   for event in current.eventStore.readMerged("__runtime__"))
                finally:
                    current.close()
            stopWorker()
            again = restart()
            try:
                assert not staged.exists() and not target.exists()
                assert confirmed.read_bytes() == published and other.read_bytes() == b"external writer"
            finally:
                again.close()
    finally:
        (output / "incomplete.release").touch()
        if previous.is_alive():
            previous.kill()
            previous.join(10)
        previous.close()
        receive.close()
