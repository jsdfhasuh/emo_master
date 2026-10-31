"""Build a complete, path-free .emoweb snapshot from the authoritative draft."""
from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
import zipfile

from PIL import Image

from emo_panel_protocol.canonical import canonicalJson, revisionOf
from emo_panel_protocol.published import PanelManifest, PublishedPresentation
from emo_master.core.presentation.history import historyRevision
from emo_master.core.presentation.models import walkComponents
from emo_master.core.presentation.targets import effectiveImageLocation, presentationForTarget
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.snapshots import captureDefinition


class PanelPublishError(ValueError):
    pass


def publishedSnapshot(document, *, manifests=None):
    original = document.presentation
    if original is None:
        raise PanelPublishError('工程尚未设计页面')
    presentation = presentationForTarget(original, 'web')
    if not presentation.pages:
        raise PanelPublishError('没有 Web 页面，请在页面属性中选择 Web 目标')
    for pageId, page in original.pages.items():
        if 'web' not in page.targets:
            continue
        for component in walkComponents(page.components):
            for action in component.actions.values():
                if action.type == 'navigate' and action.pageId not in presentation.pages:
                    raise PanelPublishError(f'{page.name} / {component.componentId}：导航目标未启用 Web')
    used = {sourceId for page in presentation.pages.values()
            for component in walkComponents(page.components) for sourceId in component.bindings.values()}
    presentation.dataSources = {key: value for key, value in presentation.dataSources.items() if key in used}
    scopes = {source.resultScopeId for source in presentation.dataSources.values()}
    for page in presentation.pages.values():
        scopes.update(page.resultScopeIds)
        page.targets = ['web']  # Validate only this terminal's exported view.
    presentation.resultScopes = {key: value for key, value in presentation.resultScopes.items() if key in scopes}
    if manifests is None:
        from emo_master.core.project.test_delivery import trustedRegistry
        manifests = {key: value.manifest for key, value in trustedRegistry().items()}
    issues = validateBindings(document.model_copy(update={'presentation': presentation}), manifests, publish=True)
    if issues:
        raise PanelPublishError('\n'.join(f'{issue.path}: {issue.message}' for issue in issues))
    capture = captureDefinition(presentation)
    payload = presentation.model_dump(mode='json')
    payload['schemaVersion'] = 'published-1'
    requirements = {'layout.ordered-responsive-v1', 'result.closed-v1', 'actions.readonly-v1'}

    def exportComponents(components, pageId):
        for item in components:
            props = item['props']
            sqlite = props.pop('sqlite', None)
            path = props.pop('imagePath', None)
            if item['type'] in {'sqlite_table', 'sqlite_value'}:
                source = sqlite or {}
                if source.get('location', 'display_local') != 'runtime':
                    raise PanelPublishError(f"{pageId} / {item['componentId']}：历史来源仅本机可读，请选择由 Runtime 读取")
                item['history'] = dict(componentId=item['componentId'],
                    kind='table' if item['type'] == 'sqlite_table' else 'aggregate',
                    columns=source.get('columns', []), orderBy=source.get('orderBy', ''),
                    descending=source.get('descending', True), aggregate=source.get('aggregate', 'count'))
                requirements.add('history.query-v1')
            if path is not None:
                component = next(c for c in walkComponents(presentation.pages[pageId].components)
                                 if c.componentId == item['componentId'])
                if effectiveImageLocation(presentation, pageId, component) != 'runtime':
                    raise PanelPublishError(f"{pageId} / {item['componentId']}：图片仅本机可读，请选择 Runtime 或工程静态资源")
                item['history'] = dict(componentId=item['componentId'],
                    kind='fixed_image' if path['kind'] == 'fixed' else 'selected_row_image',
                    sourceComponentId=path.get('componentId'))
                requirements.add('history.image-v1')
            requirements.add('component.' + item['type'] + '@1.0')
            exportComponents(item['children'], pageId)
    for pageId, page in payload['pages'].items():
        page.pop('targets', None)
        exportComponents(page['components'], pageId)
    snapshot = PublishedPresentation.model_validate(payload).model_dump(mode='json', exclude_none=True)
    return snapshot, capture, sorted(requirements), historyRevision(original)


def buildWebPanel(document, projectRoot, output, *, manifests=None):
    snapshot, capture, requirements, history = publishedSnapshot(document, manifests=manifests)
    root = Path(projectRoot).resolve()
    output = Path(output)
    if output.suffix.lower() != '.emoweb':
        raise PanelPublishError('Web 页面包扩展名必须是 .emoweb')
    assets, contents = {}, {}
    referenced = {component['props'][field] for page in snapshot['pages'].values()
        for component in _walk(page['components']) for field in ('imageResourceId', 'iconResourceId')
        if field in component['props']}
    total = 0
    for key in sorted(referenced):
        resource = document.resources.items.get(key) if document.resources else None
        if resource is None or resource.purpose not in {'page_image', 'icon'}:
            raise PanelPublishError('未声明的页面静态资源：' + key)
        if not 0 < resource.size <= 8 * 1024 * 1024:
            raise PanelPublishError('页面资源超过 8 MiB：' + key)
        path = (root / resource.path).resolve()
        if not path.is_relative_to(root):
            raise PanelPublishError('页面资源超出工程目录：' + key)
        try:
            with path.open('rb') as stream:
                data = stream.read(resource.size + 1)
        except OSError as error:
            raise PanelPublishError('无法读取页面静态资源：' + key) from error
        if len(data) != resource.size or hashlib.sha256(data).hexdigest() != resource.sha256:
            raise PanelPublishError('页面资源内容与声明不匹配：' + key)
        with Image.open(BytesIO(data)) as image:
            if image.format not in {'PNG', 'JPEG', 'BMP'} or image.width * image.height > 4 * 1024 * 1024:
                raise PanelPublishError('页面静态资源需要 PNG/JPEG/BMP 且不超过 4M 像素：' + key)
            mime = Image.MIME[image.format]
            suffix = {'PNG': '.png', 'JPEG': '.jpg', 'BMP': '.bmp'}[image.format]
            image.verify()
        name = 'assets/' + resource.sha256 + suffix
        assets[key] = dict(path=name, sha256=resource.sha256, byteSize=len(data), mimeType=mime)
        contents[name] = data
        total += len(data)
        if total > 64 * 1024 * 1024:
            raise PanelPublishError('页面静态资源合计超过 64 MiB')
    manifest = dict(format='emo-web-panel-1', projectId=document.project.projectId,
        panelContractVersion='1.0', rendererRequirements=requirements, presentation='presentation.json',
        sourcePresentationVersion=document.presentation.schemaVersion, historyDefinitionRevision=history,
        assets=assets, captureRequirements=capture)
    manifest['packageRevision'] = revisionOf(dict(manifest=manifest, presentation=snapshot))
    manifest = PanelManifest.model_validate(manifest).model_dump(mode='json')
    contents['manifest.json'] = canonicalJson(manifest).encode('utf-8')
    contents['presentation.json'] = canonicalJson(snapshot).encode('utf-8')
    # Build before replacing an existing export; an invalid draft cannot leave
    # a partially written deliverable at the selected destination.
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(buffer.getvalue())
    except OSError as error:
        raise PanelPublishError('无法保存 Web 页面包：' + str(output)) from error
    return output


def _walk(components):
    for component in components:
        yield component
        yield from _walk(component['children'])


def readWebPanel(path):
    """Validate the package metadata and content identity for tools and preview."""
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist()) > 1026 or sum(info.file_size for info in archive.infolist()) > 80 * 1024 * 1024:
            raise PanelPublishError('页面包超过限制')
        manifest = PanelManifest.model_validate_json(archive.read('manifest.json')).model_dump(mode='json')
        presentation = PublishedPresentation.model_validate_json(archive.read('presentation.json')).model_dump(
            mode='json', exclude_none=True)
        identity = deepcopy(manifest)
        revision = identity.pop('packageRevision')
        if revision != revisionOf(dict(manifest=identity, presentation=presentation)):
            raise PanelPublishError('页面包摘要不匹配')
        for asset in manifest['assets'].values():
            data = archive.read(asset['path'])
            if len(data) != asset['byteSize'] or hashlib.sha256(data).hexdigest() != asset['sha256']:
                raise PanelPublishError('页面资源摘要不匹配')
        return manifest, presentation
