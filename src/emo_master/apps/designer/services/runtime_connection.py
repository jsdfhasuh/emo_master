"""Qt-free, read-only probes shared by startup and connection switching."""
from concurrent.futures import CancelledError
from urllib.parse import urlsplit


def validateRuntimeTarget(value: str) -> str:
    target = value.strip()
    try:
        address = urlsplit("//" + target)
        valid = bool(address.hostname and address.port and not address.path and not address.query
                     and not address.fragment and not address.username and not address.password
                     and not any(char.isspace() for char in target))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("请输入 Runtime 的主机:端口，例如 127.0.0.1:50051。")
    return target


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

    target = validateRuntimeTarget(target)
    checkCancelled()
    import grpc
    from emo_master.apps.runtime.grpc_server.generated.runtime_pb2_grpc import RuntimeServiceStub
    from emo_master.apps.designer.services.runtime_client import RuntimeClient

    channel = grpc.insecure_channel(target)
    client = None
    try:
        client = RuntimeClient(runtimeService=RuntimeServiceStub(channel), ownedChannel=channel,
                               runtimeTarget=target, deadlineMs=5000)
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
