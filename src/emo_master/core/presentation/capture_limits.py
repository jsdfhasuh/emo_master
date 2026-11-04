"""Software-only normal capture profile; no inferred camera or site requirements."""
from emo_master.core.presentation.models import walkComponents
from emo_master.core.project.snapshots import canonicalJson, captureDefinition


def normalCaptureProfile():
    """Fresh public limits for capability negotiation and pre-run explanation."""
    return {"profile": "normal-two-lane-v1", "maxScopes": 16, "maxSourceIds": 16,
            "maxImageSources": 2, "maxOpenResults": 8,
            "rawBytesPerJob": 8 * 1024 * 1024, "implicitResize": False,
            "rawBytesPerSourceSingle": 8 * 1024 * 1024,
            "rawBytesPerSourceDual": 4 * 1024 * 1024}


def normalCaptureLimits(presentation):
    capture = captureDefinition(presentation)
    used = {sourceId for page in presentation.pages.values()
            for component in walkComponents(page.components)
            for sourceId in component.bindings.values()}
    sources = {key: presentation.dataSources[key].model_dump() for key in used}
    return outputCaptureLimits(sources, capture['scopes'])


def imageCaptureIdentity(source, scopes):
    """One physical output in one invocation, independent of consumer IDs."""
    address = {key: value for key, value in source.items() if key != 'resultScopeId'}
    return canonicalJson(dict(source=address, scope=scopes[source['resultScopeId']]))


def outputCaptureLimits(sources, scopes):
    """The same final per-Job budget for Qt and external output requests."""
    limits = normalCaptureProfile()
    if len(scopes) > limits["maxScopes"]:
        raise ValueError("normal capture scope budget exceeded (16)")
    if len(sources) > limits["maxSourceIds"]:
        raise ValueError("normal capture source budget exceeded (16)")
    images = {key: imageCaptureIdentity(source, scopes) for key, source in sorted(sources.items())
              if source['expectedType'] == "image"}
    unique = sorted(set(images.values()))
    if len(unique) > limits["maxImageSources"]:
        raise ValueError("normal capture supports at most two distinct image sources")
    capacity = limits["rawBytesPerJob"] // max(1, len(unique))
    return dict(limits, sourceCount=len(sources), scopeCount=len(scopes),
                rawBytesBySource={key: capacity for key in images},
                imageLaneBySource={key: unique.index(value) for key, value in images.items()})
