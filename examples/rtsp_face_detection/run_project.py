"""Run an existing project without changing its configuration or echoing credentials."""
import argparse
from collections import deque
from datetime import datetime, timezone
import json
import signal
from pathlib import Path
from time import perf_counter, sleep
from uuid import uuid4

import emo_master
from emo_master import __version__
from emo_master.apps.designer.state.project_store import loadProjectDocument
from emo_master.apps.runtime.workflow.cancellation import CancellationToken, CancellationRequested
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.workflow.compiler import WorkflowCompiler


def runProject(path, workflow, *, repeat=1, interval=2.0, maxFrames=0, token=None, reportStream=None):
    path = Path(path).resolve()
    registry = PluginRegistry(__version__).scan(Path(emo_master.__file__).parent / "plugins/builtins").activeOperators
    document = prepareRuntimeProject(loadProjectDocument(path), path.parent, registry)
    token = token if token is not None else CancellationToken()
    reports = deque(maxlen=100)
    frameCount = 0
    lastFrame = perf_counter()
    def publish(**event):
        nonlocal frameCount, lastFrame
        target = "camera_once" if workflow == "main" else workflow
        if event["eventType"] != "workflow.completed" or event["context"].workflowId != target:
            return
        out = event["payload"]["outputs"]
        now = perf_counter()
        space = out["detections"]["coordinateSpace"]
        frameCount += 1
        report = dict(timestamp=datetime.now(timezone.utc).isoformat(), workflow=workflow, frame=frameCount,
            faceCount=out["faceCount"], hasFace=out["hasFace"], imageShape=[space["imageHeight"], space["imageWidth"]],
            elapsedMs=round((now - lastFrame) * 1000, 1), detections=out["detections"], archive=out["archive"])
        lastFrame = now
        reports.append(report)
        if reportStream is not None:
            reportStream.write(json.dumps(report, ensure_ascii=True) + "\n")
            reportStream.flush()
        print(json.dumps({key: report[key] for key in ("workflow", "frame", "faceCount", "hasFace", "imageShape", "elapsedMs", "archive")}), flush=True)
        if workflow == "main" and maxFrames and frameCount >= maxFrames:
            token.cancel()
    runner = WorkflowRunner(WorkflowCompiler(operatorRegistry=registry).compile(document), registry, eventPublisher=publish)
    try:
        for index in range(repeat):
            job = uuid4().hex
            runner.run(workflow, {}, RunContext.root(job, workflow), token)
            if index + 1 < repeat:
                deadline = perf_counter() + interval
                while perf_counter() < deadline:
                    token.raise_if_cancelled()
                    sleep(min(0.1, max(0, deadline - perf_counter())))
    except CancellationRequested:
        print("Stopped; no further frames will be captured.", flush=True)
    finally:
        runner.disposeOperators()
    return list(reports)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).with_name("rtsp-face-detection.emoproj"))
    parser.add_argument("--workflow", choices=["main", "camera_once", "sample", "empty"], default="sample")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--max-frames", type=int, default=0, help="Stop main after N frames; 0 means run until Ctrl+C")
    args = parser.parse_args()
    if args.repeat < 1 or args.interval < 0 or args.max_frames < 0:
        parser.error("repeat must be positive; interval and max-frames must be nonnegative")
    if args.workflow == "main" and args.repeat != 1:
        parser.error("main already contains an unlimited While; use --max-frames for a bounded check")
    directory = args.project.resolve().parent / "verification"
    directory.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = directory / f"{args.workflow}-{stamp}.jsonl"
    token = CancellationToken()
    previous = signal.signal(signal.SIGINT, lambda *_: token.cancel())
    print(f"Report: {destination}; Ctrl+C stops the run.", flush=True)
    try:
        with destination.open("x", encoding="utf-8") as stream:
            runProject(args.project, args.workflow, repeat=args.repeat, interval=args.interval,
                       maxFrames=args.max_frames, token=token, reportStream=stream)
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    main()
