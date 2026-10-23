"""Verify spawned Runtime jobs and closed presentation assets for a saved bundle."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4


def verify(project, *, camera=False):
    root = project.resolve().parent
    data = root / "verification" / ("runtime-" + uuid4().hex[:8])
    os.environ["EMO_RUNTIME_DATA_DIR"] = str(data)
    os.environ.pop("EMO_RUNTIME_DB_PATH", None)
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.presentation.service import PresentationService

    service = RuntimeService()
    reports = []
    try:
        presentation = PresentationService(service, data / "display")
        reply = service.LoadProject(pb.LoadProjectRequest(project_path=str(project.resolve())), None)
        assert reply.ok, reply.message
        # A second main job verifies that Stop releases resources for restart.
        for workflow in ["sample", "empty", *(["main", "main"] if camera else [])]:
            started = service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId,
                workflow_id=workflow, inputs_json="{}", capture_presentation=True,
                start_request_id=uuid4().hex, expected_runtime_instance_id=service.runtimeInstanceId), None)
            assert started.ok, started.message
            deadline = monotonic() + 45
            expected = {f"{workflow}_{key}" for key in ("image", "overlay", "faceCount", "hasFace")}
            observed = {}
            snapshots = {}
            def readyResults():
                # A snapshot exposes latest per scope, not a history of frames.
                for result in presentation.store.snapshot(started.job_id)["results"]:
                    snapshots[result.identity.resultKey] = dict(ordinal=result.identity.resultOrdinal, status=result.status,
                        sources={s.sourceId: dict(state=s.state, reason=s.reason) for s in result.sources})
                    if expected.issubset({s.sourceId for s in result.sources if s.state == "AVAILABLE"}):
                        observed[result.identity.resultKey] = result
                return list(observed.values())
            while monotonic() < deadline:
                status = service.GetJobStatus(pb.GetJobStatusRequest(job_id=started.job_id), None)
                if workflow == "main" and len(readyResults()) >= 3:
                    assert status.status == "RUNNING", status.status
                    break
                if status.status in {"COMPLETED", "FAILED", "ABORTED"}:
                    break
                sleep(0.05)
            liveStatus = status.status
            if workflow == "main":
                assert len(readyResults()) >= 3, (status.status, status.error_code, status.message)
                stopAt = monotonic()
                service.StopJob(pb.StopJobRequest(job_id=started.job_id, mode="graceful"), None)
                while monotonic() < deadline:
                    status = service.GetJobStatus(pb.GetJobStatusRequest(job_id=started.job_id), None)
                    if status.status in {"COMPLETED", "FAILED", "ABORTED"}:
                        break
                    sleep(0.05)
                assert status.status == "ABORTED", (status.status, status.error_code, status.message)
                stopMs = round((monotonic() - stopAt) * 1000, 1)
            else:
                assert status.status == "COMPLETED", (status.status, status.error_code, status.message)
            results = []
            while monotonic() < deadline:
                results = readyResults()
                if results:
                    break
                sleep(0.05)
            assert results, "Job completed but presentation has not closed"
            result = results[-1]
            sources = {s.sourceId: s for s in result.sources}
            assert expected.issubset(sources), list(sources)
            for key in expected:
                assert sources[key].state == "AVAILABLE", (key, sources[key].reason)
            count = json.loads(sources[f"{workflow}_faceCount"].valueJson)
            hasFace = json.loads(sources[f"{workflow}_hasFace"].valueJson)
            assert hasFace is (count > 0)
            if workflow == "sample":
                assert count >= 1
            elif workflow == "empty":
                assert count == 0
            report = dict(workflow=workflow, jobId=started.job_id, status=status.status,
                terminalCode=status.error_code,
                liveStatus=liveStatus, completedFrames=len(results), stopMs=stopMs if workflow == "main" else None,
                resultOrdinals=[r.identity.resultOrdinal for r in results],
                observedResults=list(snapshots.values()),
                resultStatus=result.status, faceCount=count, hasFace=hasFace,
                sourceStates={key: sources[key].state for key in sorted(expected)},
                images={key: sources[key].image.model_dump(mode="json") for key in sorted(expected) if sources[key].image})
            reports.append(report)
            assert all(r["status"] in {"COMPLETE", "CANCELLED"} for r in snapshots.values()), snapshots
            print(json.dumps({key: report[key] for key in ("workflow", "status", "resultStatus", "faceCount", "hasFace")}), flush=True)
            while True:
                try:
                    presentation.release(started.job_id)
                    break
                except ValueError:
                    if monotonic() >= deadline:
                        raise
                    sleep(0.05)
            if workflow == "main":
                files = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in (root / "outputs/camera").glob("*")}
                sleep(0.5)
                assert files == {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in (root / "outputs/camera").glob("*")}
                report["noWritesAfterStop"] = True
    finally:
        service.close()
        output = root / "verification" / f"runtime-results-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        output.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
        print(f"Report: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--camera", action="store_true", help="Explicitly allow reading the configured real camera")
    args = parser.parse_args()
    verify(args.project, camera=args.camera)
