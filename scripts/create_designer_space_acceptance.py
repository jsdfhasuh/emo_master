"""Generate a device-free latest-format project for space/spacer acceptance."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject  # noqa: E402
from emo_master.apps.designer.state.workflow_store import WorkflowStore  # noqa: E402
from emo_master.core.project.models import ProjectDocument  # noqa: E402
from emo_master.core.workflow.compiler import WorkflowCompiler  # noqa: E402


def createProject(target):
    payload = WorkflowStore().toPayload('Designer 空间与空白块验收')

    def text(key, value):
        return dict(componentId=key, type='text', props=dict(text=value),
                    responsive=dict(minHeight=64))

    pages = {
        'empty': dict(name='空页面', targets=['desktop', 'web'], components=[]),
        'short': dict(name='短页面', targets=['desktop', 'web'], components=[
            text('short-text', '窗口跟随、固定终端与滚动条验收')]),
        'long': dict(name='长页面', targets=['desktop', 'web'], components=[
            text(f'long-{i}', f'第 {i+1} 行：拖动尺寸越过溢出阈值，检查横向位置稳定') for i in range(24)]),
        'spacers': dict(name='固定与弹性空白块', targets=['desktop', 'web'], components=[
            text('spacer-heading', '以下三个并排组件之间包含固定和弹性空白块'),
            dict(componentId='row', type='container', grid=dict(type='responsive', direction='horizontal'),
                 responsive=dict(minHeight=96), children=[
                    text('left', '左侧'), dict(componentId='fixed-gap', type='spacer'),
                    dict(componentId='flex-gap', type='spacer', props=dict(spacerMode='flex')),
                    text('right', '右侧')]),
            dict(componentId='vertical-gap', type='spacer', props=dict(spacerMode='flex'),
                 responsive=dict(width='content', minWidth=0, preferredWidth=24, minHeight=24,
                                 preferredHeight=24, weight=1, alignSelf='start')),
            text('spacer-footer', '竖向弹性块在有剩余高度时把本行推向底部')]),
    }
    payload['presentation'].update(defaultPageId='short', pageOrder=list(pages), pages=pages)
    document = ProjectDocument.model_validate(payload)
    assert (document.schemaVersion, document.presentation.schemaVersion) == ('2.9', '1.2')
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    saveProject(target, document.model_dump(mode='json'))
    assert loadProjectDocument(target) == document
    WorkflowCompiler().compile(document)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path)
    options = parser.parse_args()
    print(createProject(options.target).resolve())
