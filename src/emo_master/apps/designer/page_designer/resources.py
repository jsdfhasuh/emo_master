"""Explicit input registration, never guesses or copies output directories."""
import hashlib
from pathlib import Path

from emo_master.core.project.resources import Resource, ResourceBinding, ParameterTarget


def registerImage(session, directory, workflowId, nodeId, filename):
    if directory is None:
        raise ValueError('请先保存项目，再登记图片资源')
    source = Path(filename)
    if not source.is_file() or source.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('本地演示输入文件必须存在且不超过 8 MiB')
    content = source.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    key = 'input-' + sha
    relative = 'assets/' + key + source.suffix.lower()
    root = Path(directory).resolve()
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise ValueError('资源目录越界')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            raise ValueError('目标资源摘要冲突，不覆盖')
    else:
        with target.open('xb') as stream:
            stream.write(content)
    with session.transaction():
        document = session.document()
        node = next(n for n in document.workflows[workflowId].nodes if n.nodeId == nodeId)
        if node.operatorId != 'vision.io.image_loader':
            raise ValueError('只能登记明确的 ImageLoader 输入')
        plan = document.resources
        plan.items[key] = Resource(path=relative, sha256=sha, size=len(content), purpose='input_image')
        parameter = ParameterTarget(workflowId=workflowId, nodeId=nodeId, parameterPath=['imagePath'])
        plan.parameterBindings = [b for b in plan.parameterBindings if b.target != parameter]
        plan.parameterBindings.append(ResourceBinding(target=parameter, resourceId=key))
        session.workflows.projectExtensions['resources'] = plan.model_dump()
