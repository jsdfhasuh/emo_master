"""Runtime-owned stable preparation; no Job is created here."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil

from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.snapshots import ProjectSnapshot, freezeProjectSnapshot
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.apps.runtime.context.sqlite_store import SqliteStore


@dataclass(frozen=True)
class PreparedProject:
    snapshot: ProjectSnapshot
    projectPath: Path
    sourceJson: str
    files: tuple[tuple[str, str], ...]

    def verify(self):
        for name, expected in self.files:
            digest = hashlib.sha256()
            with Path(name).open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise ValueError("prepared resource changed")


def prepare(project, registry, root: Path, resourceRoot: Path, *, siteValues=None,
            mode="debug", releaseRevision=None) -> PreparedProject:
    root.mkdir(parents=True, exist_ok=True)
    snapshot = freezeProjectSnapshot(project, {k: v.manifest for k, v in registry.items()},
        mode=mode, resourceRoot=resourceRoot, siteDataRoot=root, siteValues=siteValues,
        releaseRevision=releaseRevision)
    destination = root / "prepared" / snapshot.snapshotId
    destination.mkdir(parents=True)
    try:
        document = ProjectDocument.model_validate_json(snapshot.projectJson)
        parameters = json.loads(snapshot.parametersJson)
        assert document.resources is not None and document.presentation is not None
        # Copy from the already validated location, then validate the copy again to
        # catch replacement while preparing. Names are application-generated.
        paths = {}
        for index, (key, item) in enumerate(document.resources.items.items()):
            target = destination / f"resource-{index}{Path(item.path).suffix}"
            with (resourceRoot / item.path).open("rb") as inputStream, target.open("xb") as output:
                remaining = item.size
                digest = hashlib.sha256()
                while remaining:
                    chunk = inputStream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("resource changed during copy")
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                if inputStream.read(1) or digest.hexdigest() != item.sha256:
                    raise ValueError("resource changed during copy")
            paths[key] = str(target)
        for binding in document.resources.parameterBindings:
            parameterTarget = binding.target
            value = parameters[parameterTarget.workflowId][parameterTarget.nodeId]
            for part in parameterTarget.parameterPath[:-1]:
                value = value[part]
            value[parameterTarget.parameterPath[-1]] = paths[binding.resourceId]
        for workflowId, workflow in document.workflows.items():
            for node in workflow.nodes:
                if node.kind == "operator":
                    node.params = parameters[workflowId][node.nodeId]
        WorkflowCompiler(operatorRegistry=registry).compile(document)
        presentation = document.presentation
        used = {key for key, source in presentation.dataSources.items()
                if source.model_dump() in json.loads(snapshot.capturePlanJson)["sources"]}
        sources = {key: presentation.dataSources[key].model_dump() for key in sorted(used)}
        scopes = json.loads(snapshot.capturePlanJson)["scopes"]
        if len(scopes) > 16:
            raise ValueError("scope identity budget exceeded")
        for scopeId, scope in scopes.items():
            members = [s for s in sources.values() if s["resultScopeId"] == scopeId]
            if len(members) > 16:
                raise ValueError("source budget exceeded")
            for source in members:
                if (source["kind"] not in {"node_output", "workflow_output"}
                        or source["workflowId"] != scope["scopeWorkflowId"]
                        or source["callPath"] != scope["callPath"]):
                    raise ValueError("P2 requires outputs within their explicit invocation scope")
        Path(snapshot.outputRoot).mkdir(parents=True, exist_ok=True)
        SqliteStore(Path(snapshot.runtimeDbPath)).initialize()
        projectPath = destination / "project.json"
        projectPath.write_text(document.model_dump_json(), encoding="utf-8")
        files = [(str(projectPath), hashlib.sha256(projectPath.read_bytes()).hexdigest())]
        files.extend((paths[key], item.sha256) for key, item in document.resources.items.items())
        return PreparedProject(snapshot, projectPath, json.dumps({"sources": sources, "scopes": scopes}), tuple(files))
    except BaseException:
        shutil.rmtree(destination)
        raise
