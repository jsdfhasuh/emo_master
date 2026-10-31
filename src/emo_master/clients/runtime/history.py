"""Job-independent history adapter for embedded or remote Runtime clients."""
from dataclasses import dataclass
import json
import threading
import grpc

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.core.presentation.history import historyRevision
from emo_master.core.presentation.sqlite_reader import PathValueError, SqlitePage


@dataclass(frozen=True)
class HistoryImageRef:
    imageRef: str = ''
    componentId: str = ''
    status: str = 'AVAILABLE'
    message: str = ''


class HistoryClient:
    def __init__(self, call, *, runtimeInstanceId, projectId, presentation):
        self._invoke = call
        self._ownedChannel: grpc.Channel | None = None
        self.context = pb.HistoryContext(runtime_instance_id=runtimeInstanceId, project_id=projectId,
            history_definition_revision=historyRevision(presentation))

    def call(self, method, request):
        try:
            return self._invoke(method, request)
        except (RuntimeError, grpc.RpcError) as error:
            raise ValueError('历史数据连接失败：' + str(error)) from error

    @classmethod
    def fromRuntimeClient(cls, client, document):
        info = client._call('GetRuntimeInfo', pb.RuntimeInfoRequest())
        if not info.ok:
            raise ValueError(info.message)
        return cls(client._call, runtimeInstanceId=info.runtime_instance_id,
            projectId=document.project.projectId, presentation=document.presentation)

    @classmethod
    def fromConnectionProfile(cls, profile, *, runtimeInstanceId, projectId, presentation):
        """An observer-owned connection; never fall back to the Designer target."""
        from emo_master.clients.runtime.connection import connectionProfile, createChannel
        from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
        profile = connectionProfile(profile)
        channel = createChannel(profile)
        try:
            stub = rpc.RuntimeServiceStub(channel)
            result = cls(lambda name, request: getattr(stub, name)(request, timeout=profile.timeoutSeconds),
                runtimeInstanceId=runtimeInstanceId, projectId=projectId, presentation=presentation)
        except BaseException:
            channel.close()
            raise
        result._ownedChannel = channel
        return result

    def forPresentation(self, presentation):
        """Keep the selected endpoint/instance while checking an edited draft."""
        return HistoryClient(self.call, runtimeInstanceId=self.context.runtime_instance_id,
            projectId=self.context.project_id, presentation=presentation)

    def close(self):
        if self._ownedChannel is not None:
            self._ownedChannel.close()
            self._ownedChannel = None

    @staticmethod
    def _payload(reply):
        if not reply.ok:
            raise ValueError(reply.code + ': ' + reply.message)
        return json.loads(reply.result_json)

    def readPage(self, componentId, **arguments):
        request = pb.QueryHistoryPageRequest(context=self.context, component_id=componentId,
            page=arguments.get('page', 0), page_size=arguments.get('pageSize', 20),
            filter_column=arguments.get('filterColumn', ''), filter_text=arguments.get('filterText', ''),
            order_by=arguments.get('orderBy', ''))
        if arguments.get('descending') is not None:
            request.descending = arguments['descending']
        value = self._payload(self.call('QueryHistoryPage', request))
        selections = tuple({key: HistoryImageRef(imageRef=ref.get('imageRef', ''),
            status=ref['status'], message=ref.get('message', '')) for key, ref in row['imageRefs'].items()}
            for row in value['rows'])
        return SqlitePage(tuple(column['name'] for column in value['columns']),
            tuple(tuple(row['values']) for row in value['rows']), value['page'], value['hasNext'],
            selections, tuple(row['rowId'] if value['stableRowIdentity'] else '' for row in value['rows']))

    def readValue(self, componentId):
        return self._payload(self.call('QueryHistoryAggregate', pb.QueryHistoryAggregateRequest(
            context=self.context, component_id=componentId)))['value']

    def readImage(self, ref):
        if isinstance(ref, PathValueError):
            raise ValueError(ref.message)
        if not isinstance(ref, HistoryImageRef):
            raise ValueError('请先选择历史记录')
        if ref.status != 'AVAILABLE':
            raise ValueError(ref.message or ('图片路径为空' if ref.status == 'EMPTY' else ref.status))
        request = pb.ReadHistoryImageRequest(context=self.context)
        if ref.imageRef:
            request.image_ref = ref.imageRef
        else:
            request.component_id = ref.componentId
        reply = self.call('ReadHistoryImage', request)
        if not reply.ok:
            raise ValueError(reply.code + ': ' + reply.message)
        if reply.status != 'AVAILABLE':
            raise ValueError(reply.message or reply.status)
        return bytes(reply.data)


class LazyHistoryClient:
    """Negotiate in the existing read worker, never while building a Qt widget."""
    def __init__(self, client, document):
        self.client, self.document = client, document.model_copy(deep=True)
        self.scope = getattr(client, 'runtimeScope', None)
        self.service = getattr(client, 'runtimeService', None)
        self._ready = None
        self._lock = threading.Lock()

    def _check(self):
        if (self.scope != getattr(self.client, 'runtimeScope', None)
                or self.service is not getattr(self.client, 'runtimeService', None)):
            raise ValueError('Runtime 连接已切换，请重新打开历史页面')

    def _read(self, method, *args, **kwargs):
        self._check()
        try:
            with self._lock:
                if self._ready is None:
                    self._ready = HistoryClient.fromRuntimeClient(self.client, self.document)
                owner = self._ready
            value = getattr(owner, method)(*args, **kwargs)
        except (RuntimeError, grpc.RpcError) as error:
            raise ValueError('历史数据连接失败：' + str(error)) from error
        self._check()
        return value

    def readPage(self, componentId, **arguments):
        return self._read('readPage', componentId, **arguments)

    def readValue(self, componentId):
        return self._read('readValue', componentId)

    def readImage(self, ref):
        return self._read('readImage', ref)
