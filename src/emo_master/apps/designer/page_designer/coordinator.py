"""One project boundary shared with the existing flow controller."""
import hashlib
from pathlib import Path

from PySide2.QtWidgets import QAction, QMessageBox, QStackedWidget

from emo_master.apps.designer.state.project_edit_session import ProjectEditSession


class PageCoordinator:
    def __init__(self, window, flowWidget):
        self.window = window
        self.commandDepth = 0
        self.directory = None
        self.editor = None
        self.session = ProjectEditSession(window.workflowStore.toPayload(),
            workflows=window.workflowStore, enablePresentation=False)
        self.stack = QStackedWidget()
        window.takeCentralWidget()
        self.stack.addWidget(flowWidget)
        window.setCentralWidget(self.stack)
        window.projectController.editCoordinator = self
        window.flowScene.editCompleted = self.sync
        for title, handler, shortcut in [('流程设计', self.showFlow, ''),
                ('页面设计', self.showPages, ''), ('撤销项目编辑', lambda: self.history(False), 'Ctrl+Z'),
                ('重做项目编辑', lambda: self.history(True), 'Ctrl+Shift+Z')]:
            action = QAction(title, window)
            action.triggered.connect(handler)
            if shortcut:
                action.setShortcut(shortcut)
            window.mainToolbar.addAction(action)

    def sync(self):
        self.window.workflowController.captureActiveWorkflow()
        self.session.checkpoint()

    def pageActive(self):
        return self.editor is not None and self.stack.currentWidget() is self.editor

    def showFlow(self):
        self.stack.setCurrentIndex(0)

    def showPages(self):
        self.sync()
        if self.session.document().presentation is None:
            answer = QMessageBox.question(self.window, '启用页面设计',
                '此项目将采用 2.2 格式。保存时保留 project.json.bak；旧版本需使用备份。继续？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
            self.session.enablePresentation()
        if self.editor is None:
            from .workspace import PageWorkspace
            self.editor = PageWorkspace(self)
            self.stack.addWidget(self.editor)
        self.editor.refresh()
        self.stack.setCurrentWidget(self.editor)

    def history(self, redo=False):
        self.sync()
        if (self.session.redo if redo else self.session.undo)():
            self.window.workflowController._renderActive()
            self.window.activeWorkflowId = self.session.workflows.activeWorkflowId
            self.window._refreshWorkflowTabs()
            if self.editor:
                if self.session.document().presentation is None:
                    self.showFlow()
                else:
                    self.editor.refresh()

    def confirmLeave(self):
        self.sync()
        if not self.session.dirty:
            return True
        answer = QMessageBox.question(self.window, '项目有未保存修改',
            '流程和页面共用同一项目。保存后继续？',
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            self.window.saveProjectAction()
            return not self.session.dirty
        return True

    def loaded(self, directory):
        self.shutdown()
        self.directory = directory
        self.session.acceptLoaded()
        self.showFlow()

    def saved(self, directory):
        self.directory = directory
        self.session.markSaved()

    def copyResources(self, directory):
        """Save-as copies declared immutable inputs only, never DB/output trees."""
        plan = self.session.document().resources
        if not plan or not plan.items:
            return
        if self.directory is None:
            raise ValueError('资源根目录未知，不能另存')
        sourceRoot, targetRoot = Path(self.directory).resolve(), Path(directory).resolve()
        for item in plan.items.values():
            source = sourceRoot / item.path
            target = targetRoot / item.path
            if not source.resolve().is_relative_to(sourceRoot) or not target.resolve().is_relative_to(targetRoot):
                raise ValueError('资源越界')
            if source.stat().st_size != item.size:
                raise ValueError('资源大小已变化')
            digest = hashlib.sha256()
            with source.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != item.sha256:
                raise ValueError('资源摘要已变化')
            if source.resolve() == target.resolve():
                continue
            if target.exists():
                with target.open('rb') as stream:
                    other = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        other.update(chunk)
                if other.hexdigest() != item.sha256:
                    raise ValueError('另存目标资源冲突，不覆盖')
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            with source.open('rb') as stream, target.open('xb') as output:
                shutil.copyfileobj(stream, output, 1024 * 1024)

    def shutdown(self):
        if self.editor:
            self.editor.close()
            self.stack.removeWidget(self.editor)
            self.editor.deleteLater()
            self.editor = None
