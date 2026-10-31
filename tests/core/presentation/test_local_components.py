"""New page components survive normal project persistence without result bindings."""
import pytest
from pydantic import ValidationError

from emo_master.apps.designer.state.project_store import saveProject, loadProjectDocument
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.presentation.models import Component, Props, Presentation, SqliteSource
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler


def localProject():
    document = ProjectDocument.model_validate(WorkflowStore().toPayload())
    components = [Component(componentId=kind, type=kind, layout={'row': index})
                  for index, kind in enumerate(('page_title', 'page_navigation', 'clock',
                                                 'sqlite_table', 'sqlite_value'))]
    for item in components[3:]:
        item.props.sqlite = SqliteSource(databasePath='C:/example/history.db', table='检测记录',
                                        columns=['时间', '判定'], orderBy='时间')
    document.presentation = Presentation(defaultPageId='history', pageOrder=['history'],
        pages={'history': {'name': '历史查阅', 'components': components}})
    return document


def testLocalComponentsPersistInCurrentProjectFormat(tmp_path):
    document = localProject()
    assert document.schemaVersion == '2.9'
    assert document.devices.schemaVersion == '1.0' and not document.devices.definitions
    assert validateBindings(document, {}, publish=True) == []
    saveProject(tmp_path, document.model_dump(mode='json'))
    loaded = loadProjectDocument(tmp_path)
    assert loaded.presentation == document.presentation
    assert loaded.presentation.pages['history'].components[3].props.sqlite.columns == ['时间', '判定']
    WorkflowCompiler().compile(loaded)


def testExistingPropsDoNotGainSqliteConfiguration():
    assert 'sqlite' not in Props(title='现有组件').model_dump()
    assert 'sqlite' not in Props().model_dump(include={'title'})


@pytest.mark.parametrize('kind', ['page_title', 'page_navigation', 'clock', 'sqlite_table', 'sqlite_value'])
def testIndependentComponentsRejectWorkflowBindings(kind):
    with pytest.raises(ValidationError, match='unsupported binding'):
        Component(componentId='local', type=kind, bindings={'value': 'count'})


def testSqliteDraftCanBeConfiguredBeforePublication():
    document = localProject()
    table, value = document.presentation.pages['history'].components[3:]
    table.props.sqlite = SqliteSource()
    value.props.sqlite.aggregate = 'avg'
    assert validateBindings(document, {}) == []
    issues = validateBindings(document, {}, publish=True)
    assert len(issues) == 2 and {issue.code for issue in issues} == {'sqlite_unconfigured'}
    table.props.sqlite = SqliteSource(databasePath='C:/example/history.db', table='检测记录')
    value.props.sqlite.valueColumn = '置信度'
    assert len(validateBindings(document, {}, publish=True)) == 1


def testSqliteFieldNamesPermitRealDatabaseNamesAndRejectDuplicates():
    source = SqliteSource(table='inspection records', columns=['capture time', 'operator"note'])
    assert SqliteSource.model_validate(source.model_dump()) == source
    with pytest.raises(ValidationError, match='unique'):
        SqliteSource(columns=['time', 'time'])
    with pytest.raises(ValidationError, match='SQLite source requires'):
        Component(componentId='text', type='text', props={'sqlite': {'table': 'history'}})
