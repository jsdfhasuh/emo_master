"""Display-only editor example; no Runtime calls or operator-ID branches."""
from PySide2.QtWidgets import QLayout, QWidget

from emo_master.apps.designer.ui.image_view import ImageView
from emo_master.apps.designer.ui.widgets import CollapsibleSection


class ExampleEditorController:
    def bind(self, rootWidget, context):
        self.root = rootWidget
        self.context = context
        for layout in self.root.findChildren(QLayout):
            layout.setContentsMargins(0, 0, 0, 0)
        self.source = ImageView()
        self.result = ImageView()
        self.root.findChild(QWidget, "sourceSlot").layout().addWidget(self.source)
        self.root.findChild(QWidget, "resultSlot").layout().addWidget(self.result)

    def showImages(self, sourcePixmap, resultPixmap):
        self.source.setImage(sourcePixmap)
        self.result.setImage(resultPixmap)

    def mountVariableBindings(self, scroll):
        scroll.setMinimumHeight(140)
        scroll.setMaximumHeight(200)
        self.bindings = CollapsibleSection("参数来源", scroll)
        self.root.findChild(QWidget, "bindingsSlot").layout().addWidget(self.bindings)

    def loadParams(self, params):
        self.params = dict(params)

    def collectParams(self):
        return dict(self.params)

    def validate(self):
        return None

    def onOpen(self):
        pass

    def onClose(self):
        pass

    def dispose(self):
        pass
