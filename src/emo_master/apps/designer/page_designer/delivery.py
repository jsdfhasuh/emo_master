"""Explicit export of the authoritative draft; no Runtime/Job creation."""
from pathlib import Path
from PySide2.QtWidgets import QFileDialog, QMessageBox
from emo_master.core.project.package_builder import buildPageTestPackage


def exportTestPackage(coordinator, output):
    coordinator.sync()
    if coordinator.directory is None:
        raise ValueError('请先保存项目并登记输入资源')
    return buildPageTestPackage(coordinator.session.document(), Path(coordinator.directory), Path(output))


def exportDialog(coordinator):
    directory = QFileDialog.getExistingDirectory(coordinator.window, '导出测试项目包：选择项目外目录')
    if not directory:
        return
    try:
        package = exportTestPackage(coordinator, directory)
    except Exception as error:
        QMessageBox.warning(coordinator.window, '测试包导出失败', str(error))
        return
    QMessageBox.information(coordinator.window, '测试包已导出',
        str(package) + '\n仅供开发测试；不代表性能、稳定性或现场发布验收通过。')
