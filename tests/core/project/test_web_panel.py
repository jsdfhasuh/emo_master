import hashlib
import json
import zipfile

from PIL import Image
import pytest

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.web_panel import PanelPublishError, buildWebPanel, readWebPanel


def panelDocument(root):
    Image.new('RGB', (5, 5), 'blue').save(root / 'logo.png')
    data = (root / 'logo.png').read_bytes()
    payload = WorkflowStore().toPayload()
    payload['schemaVersion'] = '2.9'
    payload['resources']['items']['logo'] = dict(path='logo.png', sha256=hashlib.sha256(data).hexdigest(),
        size=len(data), purpose='page_image')
    payload['presentation'] = dict(schemaVersion='1.2', defaultPageId='local', pageOrder=['local', 'web'], pages=dict(
        local=dict(name='Desktop', components=[]), web=dict(name='Panel', targets=['web'], components=[
            dict(componentId='logo', type='image', props=dict(imageResourceId='logo')),
            dict(componentId='rows', type='sqlite_table', props=dict(sqlite=dict(location='runtime',
                databasePath='D:/private/production.db', table='records', columns=['id', 'result']))),
            dict(componentId='photo', type='image', props=dict(imagePath=dict(kind='selected_row',
                componentId='rows', field='secret_image_path', baseDirectory='D:/private/images')))])))
    return ProjectDocument.model_validate(payload)


def testCompletePanelSnapshotContainsNoLocalPaths(tmp_path):
    document = panelDocument(tmp_path)
    before = document.model_dump_json()
    path = buildWebPanel(document, tmp_path, tmp_path / 'panel.emoweb')
    manifest, snapshot = readWebPanel(path)
    assert manifest['format'] == 'emo-web-panel-1'
    assert snapshot['pageOrder'] == ['web'] and snapshot['defaultPageId'] == 'web'
    assert 'component.sqlite_table@1.0' in manifest['rendererRequirements']
    assert snapshot['pages']['web']['components'][1]['history']['columns'] == ['id', 'result']
    assert manifest['assets']['logo']['path'].startswith('assets/')
    with zipfile.ZipFile(path) as archive:
        text = archive.read('presentation.json').decode() + archive.read('manifest.json').decode()
        assert all(value not in text for value in ('private', 'production.db', 'secret_image_path', 'databasePath', 'baseDirectory'))
        assert len(archive.namelist()) == 3
    assert document.model_dump_json() == before
    second = buildWebPanel(document, tmp_path, tmp_path / 'second.emoweb')
    assert second.read_bytes() == path.read_bytes()


def testPublishGivesComponentForLocalHistoryAndRejectsInaccessibleNavigation(tmp_path):
    document = panelDocument(tmp_path)
    document.presentation.pages['web'].components[1].props.sqlite.location = 'display_local'
    with pytest.raises(PanelPublishError, match='rows.*仅本机'):
        buildWebPanel(document, tmp_path, tmp_path / 'panel.emoweb')
    document = panelDocument(tmp_path)
    from emo_master.core.presentation.models import Component
    document.presentation.pages['web'].components.append(Component.model_validate(dict(componentId='bad-link',
        type='navigation_button', actions=dict(clicked=dict(type='navigate', pageId='local')))))
    with pytest.raises(PanelPublishError, match='bad-link.*导航'):
        buildWebPanel(document, tmp_path, tmp_path / 'panel.emoweb')
    assert not (tmp_path / 'panel.emoweb').exists()


def testPanelRevisionDetectsChangedRenderingSnapshot(tmp_path):
    path = buildWebPanel(panelDocument(tmp_path), tmp_path, tmp_path / 'panel.emoweb')
    with zipfile.ZipFile(path) as source:
        files = {name: source.read(name) for name in source.namelist()}
    snapshot = json.loads(files['presentation.json'])
    snapshot['pages']['web']['name'] = 'Modified'
    files['presentation.json'] = json.dumps(snapshot).encode()
    with zipfile.ZipFile(path, 'w') as output:
        for name, data in files.items():
            output.writestr(name, data)
    with pytest.raises(PanelPublishError, match='摘要'):
        readWebPanel(path)


@pytest.mark.parametrize('problem, expected', [('root', 'photo'), ('columns', 'rows'), ('binding', 'unbound-web')])
def testPublishValidatesUsableWebHistoryAndBindings(tmp_path, problem, expected):
    from emo_master.core.presentation.models import Component
    document = panelDocument(tmp_path)
    page = document.presentation.pages['web']
    if problem == 'root':
        page.components[2].props.imagePath.baseDirectory = ''
    elif problem == 'columns':
        page.components[1].props.sqlite.columns = []
    else:
        page.components.append(Component(componentId='unbound-web', type='number'))
    with pytest.raises(PanelPublishError, match=expected):
        buildWebPanel(document, tmp_path, tmp_path / 'invalid.emoweb')
    assert not (tmp_path / 'invalid.emoweb').exists()


def testDesktopOnlyDraftDoesNotBlockValidatedWebExport(tmp_path):
    from emo_master.core.presentation.models import Component, ResultScope
    document = panelDocument(tmp_path)
    document.presentation.pages['local'].components.append(Component(componentId='unfinished-desktop', type='number'))
    document.presentation.pages['local'].resultScopeIds.append('desktop-draft')
    document.presentation.resultScopes['desktop-draft'] = ResultScope(entryWorkflowId='missing', scopeWorkflowId='missing')
    before = document.model_dump_json()
    package = buildWebPanel(document, tmp_path, tmp_path / 'valid.emoweb')
    _manifest, snapshot = readWebPanel(package)
    assert snapshot['pageOrder'] == ['web'] and 'desktop-draft' not in snapshot['resultScopes']
    assert document.model_dump_json() == before
