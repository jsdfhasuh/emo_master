"""Explicit input registration, never guesses or copies output directories."""
from pathlib import Path

from emo_master.core.project.node_input_files import alignNodeInputFiles, projectInputFilePath


def registerImage(session, directory, workflowId, nodeId, filename):
    if directory is None:
        raise ValueError('请先保存项目，再登记图片资源')
    source = Path(filename)
    if not source.is_file() or source.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('本地演示输入文件必须存在且不超过 8 MiB')
    with session.transaction():
        document = session.document()
        node = next(n for n in document.workflows[workflowId].nodes if n.nodeId == nodeId)
        if node.operatorId != 'vision.io.image_loader':
            raise ValueError('只能登记明确的 ImageLoader 输入')
        node.params['imagePath'] = projectInputFilePath(source, directory, node.operatorId)
        document = alignNodeInputFiles(document, directory, only={(workflowId, nodeId)})
        session._restore(document.model_dump(mode="json"))
