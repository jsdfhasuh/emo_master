"""Explicit export of the authoritative draft; no Runtime/Job creation."""
from pathlib import Path
from PySide2.QtWidgets import QFileDialog, QMessageBox
from emo_master.core.project.package_builder import buildPageTestPackage
from emo_master.core.project.runtime_package import buildRuntimePackage


def exportPackageLabel(coordinator):
    managed = coordinator.session.workflows.devices.get('schemaVersion') == '1.0'
    return '导出 Runtime 项目包' if managed else '导出测试项目包'


def exportTestPackage(coordinator, output):
    coordinator.sync()
    if coordinator.directory is None:
        raise ValueError('请先保存项目，确定资源所在目录')
    document = coordinator.session.document()
    if document.schemaVersion in {'2.6', '2.7', '2.8', '2.9', '2.10'}:
        project = coordinator.window.projectController.currentProjectFile or coordinator.directory
        return buildRuntimePackage(Path(project), Path(output), document=document)
    return buildPageTestPackage(document, Path(coordinator.directory), Path(output))


def exportDialog(coordinator):
    title = exportPackageLabel(coordinator)
    directory = QFileDialog.getExistingDirectory(coordinator.window, title + '：选择项目外目录')
    if not directory:
        return
    try:
        package = exportTestPackage(coordinator, directory)
    except Exception as error:
        coordinator.window.appendLocalEvent(dict(
            eventType='designer.page_export.failed', level='WARN' if coordinator.directory is None else 'ERROR',
            message=f'{title}失败：{error}', code=str(getattr(error, 'code', '')),
            payload=dict(outputDirectory=directory, errorType=type(error).__name__, reason=str(error))),
            source='page_designer')
        QMessageBox.warning(coordinator.window, title + '失败', str(error))
        return
    usage = ('可通过独立 Runtime 的“更新工程”导入。' if title == '导出 Runtime 项目包'
             else '仅供旧版页面测试交付入口导入。')
    QMessageBox.information(coordinator.window, title + '完成',
        str(package) + '\n' + usage + '\n已导出当前草稿；原项目文件未保存，Runtime 未更新。')


def exportWebPanel(coordinator, output):
    from emo_master.core.project.web_panel import buildWebPanel
    coordinator.sync()
    if coordinator.directory is None:
        raise ValueError('请先保存工程，确定页面资源所在目录')
    return buildWebPanel(coordinator.session.document(), coordinator.directory, output)


def exportWebPanelDialog(coordinator):
    output, _filter = QFileDialog.getSaveFileName(
        coordinator.window, '导出 Web 页面包', '', 'Web 页面包 (*.emoweb)')
    if not output:
        return
    path = Path(output)
    if not path.suffix:
        path = path.with_suffix('.emoweb')
    try:
        package = exportWebPanel(coordinator, path)
    except (OSError, ValueError) as error:
        coordinator.window.appendPageLog('ERROR', str(error))
        QMessageBox.warning(coordinator.window, '导出 Web 页面包失败', str(error))
    else:
        QMessageBox.information(coordinator.window, 'Web 页面包已导出', str(package))
