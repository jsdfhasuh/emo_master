"""Bounded ephemeral ownership for local-image draft previews.

Sessions never load a project, compile a workflow, synchronize variables or own a
Job. A server-issued key separates even byte-identical project copies. All calls
must present that session; no project-id fallback is permitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import time
from uuid import uuid4

from emo_master.apps.runtime.preview.draft import draftPreviewProjectId
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument


class DraftPreviewError(ValueError):
    code = "E_PREVIEW_CONTEXT_INVALID"


def draftFingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@dataclass
class PureDraftSession:
    sessionId: str
    projectId: str
    workflowId: str
    nodeId: str
    operatorId: str
    fingerprint: str
    document: ProjectDocument
    touched: float
    assets: set[str] = field(default_factory=set)
    outputAssets: set[str] = field(default_factory=set)

    @property
    def projectKey(self):
        return "draft-preview-" + self.sessionId


class PureDraftPreviewSessions:
    # Physical reclamation is opportunistic on the next preview request/close.
    # Expired leases are never usable, including when no later request arrives.
    ttlSeconds = 300.0
    maxSessions = 8
    maxAssets = 32
    maxBytes = 128 * 1024 * 1024

    def __init__(self, store, registry, *, clock=time.monotonic):
        self.store, self.registry, self.clock = store, registry, clock
        self.sessions: dict[str, PureDraftSession] = {}

    def expire(self):
        for session in list(self.sessions.values()):
            if self.clock() - session.touched >= self.ttlSeconds:
                self.close(session.sessionId)

    def open(self, request):
        self.expire()
        projectId, error = draftPreviewProjectId(request)
        if not projectId:
            raise DraftPreviewError(error)
        descriptor = self.registry.get(request.operator_id)
        if (descriptor is None or descriptor.editorIssues or descriptor.manifest.editor is None
                or descriptor.manifest.editor.previewMode != "pure"):
            raise DraftPreviewError("当前算子不支持纯图片预览")
        if len(self.sessions) >= self.maxSessions:
            raise DraftPreviewError("纯预览会话已达上限（8），请关闭其他配置窗口")
        payload = json.loads(request.project_json)
        session = PureDraftSession(uuid4().hex, projectId, request.workflow_id, request.node_id,
            request.operator_id, draftFingerprint(payload),
            ProjectDocument.model_validate(migrateProjectPayload(payload)), self.clock())
        self.sessions[session.sessionId] = session
        return session

    def require(self, sessionId, projectId, request=None):
        self.expire()
        session = self.sessions.get(sessionId)
        if session is None or session.projectId != projectId:
            raise DraftPreviewError("纯预览会话已失效或不属于当前工程，请重新选择本地图片")
        if request is not None:
            project, error = draftPreviewProjectId(request)
            if (not project or (request.workflow_id, request.node_id, request.operator_id) !=
                    (session.workflowId, session.nodeId, session.operatorId)):
                raise DraftPreviewError(error or "纯预览节点与会话不一致")
            if draftFingerprint(json.loads(request.project_json)) != session.fingerprint:
                self.close(sessionId)
                raise DraftPreviewError("工程草稿已变更，旧预览已失效，请重新选择本地图片")
        session.touched = self.clock()
        return session

    def retain(self, session, assets, *, outputs=False):
        assetIds = {asset.assetId for asset in assets}
        if outputs:
            for assetId in session.outputAssets:
                self.store.removeTransient(assetId)
            session.assets.difference_update(session.outputAssets)
            session.outputAssets.clear()
        combined = session.assets | assetIds
        stored = [self.store.resolve(assetId) for assetId in combined]
        if (len(combined) > self.maxAssets or
                sum(asset.path.stat().st_size for asset in stored if asset is not None) > self.maxBytes):
            for assetId in assetIds:
                self.store.removeTransient(assetId)
            raise DraftPreviewError("纯预览图片缓存已达上限，请刷新来源后重新选择图片")
        session.assets = combined
        if outputs:
            session.outputAssets = assetIds

    def close(self, sessionId):
        session = self.sessions.pop(sessionId, None)
        if session is not None:
            for assetId in session.assets:
                self.store.removeTransient(assetId)

    def closeAll(self):
        for sessionId in list(self.sessions):
            self.close(sessionId)
