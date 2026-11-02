"""Read only the Runtime's published history declarations; no Job is required."""
from collections import OrderedDict
from io import BytesIO
import json
from secrets import token_urlsafe
import time

from PIL import Image, UnidentifiedImageError

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.core.presentation.history import historyDefinitions, historyRevision, imagePath
from emo_master.core.presentation.models import Presentation, walkComponents
from emo_master.core.presentation.sqlite_reader import PathValueError, readPage, readValue


class HistoryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class HistoryRpcMixin:
    def _historyContext(self, context):
        from pathlib import Path
        with self._projectStateLock:
            if context.runtime_instance_id != self.runtimeInstanceId:
                raise HistoryError('E_RUNTIME_INSTANCE', 'Runtime 实例已变化，请重新连接')
            current = self.projectConfiguration.current
            if current is None:
                raise HistoryError('E_HISTORY_PROJECT', 'Runtime 尚无正式工程')
            if context.project_id != current['payload']['project']['projectId']:
                raise HistoryError('E_HISTORY_PROJECT', '工程身份不匹配')
            presentation = Presentation.model_validate(current['payload']['presentation'])
            if context.history_definition_revision != historyRevision(presentation):
                raise HistoryError('E_HISTORY_REVISION', '历史来源声明已变化，请更新页面配置')
            return presentation, Path(current['sourcePath']).parent

    def _historySource(self, presentation, componentId, types):
        declaration = historyDefinitions(presentation).get(componentId)
        component = next((c for page in presentation.pages.values() for c in walkComponents(page.components)
                          if c.componentId == componentId), None)
        if declaration is None or component is None or component.type not in types:
            raise HistoryError('E_HISTORY_SOURCE', '来源未在 Runtime 正式工程中声明')
        return component

    @staticmethod
    def _historyFailure(error, image=False):
        kind = pb.HistoryImageReply if image else pb.HistoryReply
        return kind(ok=False, code=getattr(error, 'code', 'E_HISTORY_QUERY'), message=str(error))

    def _historyImageRef(self, requestContext, componentId, value):
        now = time.monotonic()
        token = token_urlsafe(24)
        with self._projectStateLock:
            refs = getattr(self, '_historyRefs', None)
            if refs is None:
                refs = self._historyRefs = OrderedDict()
            while refs and (next(iter(refs.values()))[0] <= now or len(refs) >= 2048):
                refs.popitem(last=False)
            refs[token] = (now + 300, requestContext.project_id, requestContext.history_definition_revision,
                           componentId, value)
        return token

    def QueryHistoryPage(self, request, context):
        try:
            presentation, root = self._historyContext(request.context)
            component = self._historySource(presentation, request.component_id, {'sqlite_table'})
            source = component.props.sqlite
            if request.order_by and request.order_by not in {*source.columns, source.orderBy}:
                raise HistoryError('E_HISTORY_SORT', '只能按声明的字段排序')
            if len(request.filter_text) > 4096:
                raise HistoryError('E_HISTORY_FILTER', '筛选内容过长')
            images = [c for page in presentation.pages.values() for c in walkComponents(page.components)
                      if c.props.imagePath is not None and c.props.imagePath.kind == 'selected_row'
                      and c.props.imagePath.componentId == component.componentId]
            result = readPage(source, page=request.page, pageSize=request.page_size or 20,
                filterColumn=request.filter_column, filterText=request.filter_text,
                orderBy=request.order_by or None,
                descending=request.descending if request.HasField('descending') else None,
                selectionFields=tuple(c.props.imagePath.field for c in images), projectRoot=root)
            rows = []
            for index, values in enumerate(result.rows):
                imageRefs = {}
                for image in images:
                    value = result.selectionRows[index].get(image.props.imagePath.field)
                    if isinstance(value, PathValueError):
                        imageRefs[image.componentId] = dict(status='INVALID_VALUE', message=value.message)
                    elif value is None or value == '':
                        imageRefs[image.componentId] = dict(status='EMPTY')
                    else:
                        imageRefs[image.componentId] = dict(status='AVAILABLE', imageRef=self._historyImageRef(
                            request.context, image.componentId, value))
                rows.append(dict(rowId=result.rowIds[index], values=values, imageRefs=imageRefs))
            payload = dict(columns=[dict(name=name, type='scalar') for name in result.columns], rows=rows,
                page=result.page, pageSize=request.page_size or 20, hasNext=result.hasNext,
                historyDefinitionRevision=request.context.history_definition_revision,
                stableRowIdentity=all(result.rowIds) if result.rowIds else True)
            return pb.HistoryReply(ok=True, result_json=json.dumps(payload, ensure_ascii=True, allow_nan=False))
        except (ValueError, OSError, OverflowError) as error:
            return self._historyFailure(error)

    def QueryHistoryAggregate(self, request, context):
        try:
            presentation, root = self._historyContext(request.context)
            component = self._historySource(presentation, request.component_id, {'sqlite_value', 'sqlite_table'})
            if len(request.filter_text) > 4096:
                raise HistoryError('E_HISTORY_FILTER', '筛选内容过长')
            value = readValue(component.props.sqlite, projectRoot=root,
                filterColumn=request.filter_column, filterText=request.filter_text)
            return pb.HistoryReply(ok=True, result_json=json.dumps(dict(value=value,
                aggregate=component.props.sqlite.aggregate,
                historyDefinitionRevision=request.context.history_definition_revision), allow_nan=False))
        except (ValueError, OSError, OverflowError) as error:
            return self._historyFailure(error)

    def ReadHistoryImage(self, request, context):
        try:
            presentation, root = self._historyContext(request.context)
            if request.WhichOneof('target') == 'image_ref':
                with self._projectStateLock:
                    entry = getattr(self, '_historyRefs', {}).get(request.image_ref)
                if entry is None or entry[0] <= time.monotonic():
                    raise HistoryError('E_HISTORY_IMAGE_EXPIRED', '图片引用已过期，请刷新记录')
                _, project, revision, componentId, value = entry
                if (project, revision) != (request.context.project_id, request.context.history_definition_revision):
                    raise HistoryError('E_HISTORY_REVISION', '图片引用与当前来源声明不匹配')
                component = self._historySource(presentation, componentId, {'image'})
            elif request.WhichOneof('target') == 'component_id':
                component = self._historySource(presentation, request.component_id, {'image'})
                if component.props.imagePath.kind != 'fixed':
                    raise HistoryError('E_HISTORY_SOURCE', '选中行图片需要先查询记录')
                value = component.props.imagePath.path
            else:
                raise HistoryError('E_HISTORY_SOURCE', '请选择图片来源')
            if value is None or value == '':
                return pb.HistoryImageReply(ok=True, status='EMPTY')
            path = imagePath(value, component.props.imagePath.baseDirectory, root, confined=True)
            try:
                with path.open('rb') as stream:
                    data = stream.read(8 * 1024 * 1024 + 1)
            except FileNotFoundError:
                return pb.HistoryImageReply(ok=True, status='MISSING', message='图片文件不存在')
            if not data or len(data) > 8 * 1024 * 1024:
                return pb.HistoryImageReply(ok=True, status='TOO_LARGE', message='图片为空或超过 8 MiB')
            try:
                with Image.open(BytesIO(data)) as image:
                    if image.format not in {'PNG', 'JPEG', 'BMP'}:
                        return pb.HistoryImageReply(ok=True, status='UNSUPPORTED', message='支持 PNG、JPEG、BMP')
                    width, height = image.size
                    if width * height > 32 * 1024 * 1024:
                        return pb.HistoryImageReply(ok=True, status='TOO_LARGE', message='图片尺寸超出限制')
                    mime = Image.MIME[image.format]
                    image.verify()
            except (UnidentifiedImageError, Image.DecompressionBombError, SyntaxError):
                return pb.HistoryImageReply(ok=True, status='UNSUPPORTED', message='图片格式不支持或文件损坏')
            return pb.HistoryImageReply(ok=True, status='AVAILABLE', data=data, mime_type=mime,
                width=width, height=height)
        except (ValueError, OSError, OverflowError) as error:
            return self._historyFailure(error, image=True)
