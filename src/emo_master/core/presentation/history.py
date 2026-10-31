"""History identities and local image path rules, shared by both transports."""
from pathlib import Path

from emo_master.core.presentation.local_paths import resolveLocalPath
from emo_master.core.presentation.models import walkComponents
from emo_master.core.project.snapshots import revisionOf


def historyDefinitions(presentation):
    definitions = {}
    for page in presentation.pages.values():
        components = list(walkComponents(page.components))
        tables = {c.componentId: c for c in components if c.type == 'sqlite_table'}
        for component in components:
            if component.type in {'sqlite_table', 'sqlite_value'} and component.props.sqlite.location == 'runtime':
                definitions[component.componentId] = dict(type=component.type,
                    sqlite=component.props.sqlite.model_dump(mode='json'))
            image = component.props.imagePath
            if image is None:
                continue
            table = tables.get(image.componentId) if image.kind == 'selected_row' else None
            location = table.props.sqlite.location if table is not None else image.location
            if location == 'runtime':
                definitions[component.componentId] = dict(type='image',
                    imagePath=image.model_dump(mode='json'))
    return definitions


def historyRevision(presentation):
    return revisionOf(dict(ruleVersion='1.0', components=historyDefinitions(presentation)))


def imagePath(value, baseDirectory='', projectRoot=None, *, confined=False):
    from emo_master.core.presentation.sqlite_reader import PATH_TEXT_LIMIT, PathValueError
    if isinstance(value, PathValueError):
        raise ValueError(value.message)
    if value is None or value == '':
        raise ValueError('尚未选择记录，或图片路径为空')
    if not isinstance(value, str):
        raise ValueError('图片路径必须是文字')
    if len(value) > PATH_TEXT_LIMIT or '\x00' in value:
        raise ValueError('图片路径过长或包含无效字符')
    if any('://' in item or item.startswith(('\\\\', '//')) for item in (value, baseDirectory)):
        raise ValueError('仅支持本地图片路径，不支持网络地址')
    path = Path(value).expanduser()
    base = resolveLocalPath(baseDirectory, projectRoot, purpose='图片基准目录') if baseDirectory else None
    if not path.is_absolute():
        if base is None:
            raise ValueError('相对图片路径需要配置明确的基准目录')
        path = base / path
    if confined:
        if base is None:
            raise ValueError('Runtime 图片需要声明允许的基准目录')
        if not path.resolve().is_relative_to(base.resolve()):
            raise ValueError('图片路径超出声明的基准目录')
    return path
