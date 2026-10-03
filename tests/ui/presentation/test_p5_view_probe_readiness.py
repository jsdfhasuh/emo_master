"""Standalone probe waits for actual GUI images, retaining result identity."""
from types import SimpleNamespace as Row

from PySide2.QtCore import QCoreApplication, QEvent
from PySide2.QtGui import QImage

from emo_master.ui.presentation.renderer import ImageView
from scripts.p5_view_probe import renderedImagesReady


def testProbeDoesNotTreatMetadataOrWrongKeyImageAsReady(qtApp):
    widget = ImageView()
    scope = Row(result=Row(identity=Row(resultKey='current')), images={})
    renderer = Row(currentPageId='page', widgets={'page': {
        'image': (Row(type='image', bindings={'image': 'source'}), widget)}},
        config=Row(dataSources={'source': Row(resultScopeId='scope')}), displayed={'scope': scope})
    try:
        assert renderer.displayed and not renderedImagesReady(renderer)
        scope.images['source'] = object()
        assert not renderedImagesReady(renderer)
        image = QImage(2, 2, QImage.Format_RGB32)
        image.fill(0)
        widget.setImage(image, 'previous')
        assert not renderedImagesReady(renderer)
        widget.setImage(image, 'current')
        assert renderedImagesReady(renderer)
        renderer.currentPageId = 'other'
        assert not renderedImagesReady(renderer)
    finally:
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def testProbeRequiresEveryBoundCurrentPageImage(qtApp):
    first, second = ImageView(), ImageView()
    image = QImage(2, 2, QImage.Format_RGB32)
    image.fill(0)
    first.setImage(image, 'current')
    second.setImage(image, 'old')
    scope = Row(result=Row(identity=Row(resultKey='current')), images={'a': object(), 'b': object()})
    renderer = Row(currentPageId='page', widgets={'page': {
        'a': (Row(type='image', bindings={'image': 'a'}), first),
        'b': (Row(type='image', bindings={'image': 'b'}), second)}},
        config=Row(dataSources={key: Row(resultScopeId='scope') for key in ('a', 'b')}),
        displayed={'scope': scope})
    try:
        assert not renderedImagesReady(renderer)
        second.setImage(image, 'current')
        assert renderedImagesReady(renderer)
    finally:
        for widget in (first, second):
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
