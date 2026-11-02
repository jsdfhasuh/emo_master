"""Qt-free, read-only probes shared by startup and connection switching."""
from concurrent.futures import CancelledError
from emo_master.clients.runtime.connection import connectionProfile, createChannel
from emo_master.clients.runtime.connection import validateRuntimeTarget as validateRuntimeTarget


def readCurrentConnection(client):
    info = client.deviceNetworkCommand('', '', 'info')
    snapshot, versionError = None, ''
    if info['projectId']:
        try:
            snapshot = client.getOnlineProjectSnapshot()
        except Exception as error:
            versionError = str(error)
    return info, snapshot, versionError


def connectCandidate(target, *, cancelled=None):
    def checkCancelled():
        if cancelled is not None and cancelled.is_set():
            raise CancelledError()

    profile = connectionProfile(target)
    target = profile.target
    checkCancelled()
    from emo_master.apps.runtime.grpc_server.generated.runtime_pb2_grpc import RuntimeServiceStub
    from emo_master.apps.designer.services.runtime_client import RuntimeClient

    channel = createChannel(profile)
    client = None
    try:
        client = RuntimeClient(runtimeService=RuntimeServiceStub(channel), ownedChannel=channel,
                               runtimeTarget=target, connectionProfile=profile,
                               deadlineMs=int(profile.timeoutSeconds * 1000))
        info = client.deviceNetworkCommand('', '', 'info')
        checkCancelled()
        snapshot = client.getOnlineProjectSnapshot() if info['projectId'] else None
        checkCancelled()
        types, _requirements, _rejected = client.listDeviceTypes()
        checkCancelled()
        return client, info, snapshot, types
    except BaseException:
        if client is not None:
            client.close()
        else:
            channel.close()
        raise
