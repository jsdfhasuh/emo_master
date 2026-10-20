import hashlib

import pytest
from PySide2.QtGui import QImage, QColor

from emo_master.core.presentation.models import Component, Presentation
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.models import ProjectDocument
from emo_master.ui.presentation.renderer import RuntimePages
from emo_master.ui.presentation.static_assets import readAsset
from tests.runtime.test_device_runtime import simulatedProject


def presentation():
    return Presentation.model_validate(dict(defaultPageId="one", pageOrder=["one", "two"], pages={
        "one": dict(name="Static page", components=[
            dict(componentId="image", type="image", props=dict(imageResourceId="picture")),
            dict(componentId="button", type="navigation_button", layout=dict(row=1),
                 props=dict(text="Next", iconResourceId="icon"), actions=dict(clicked=dict(type="navigate", pageId="two")))]),
        "two": dict(name="Other page", components=[])}))


def asset(tmp_path):
    path = tmp_path / "picture.png"
    image = QImage(24, 16, QImage.Format_ARGB32)
    image.fill(QColor("#cf3948"))
    assert image.save(str(path))
    data = path.read_bytes()
    return (str(path), hashlib.sha256(data).hexdigest(), len(data))


def testStaticAssetsHaveNoCaptureDemandAndRetireOnHide(retainedQtApplication, tmp_path):
    value = asset(tmp_path)
    window = RuntimePages(presentation(), assets={"picture": value, "icon": value})
    window.show()
    picture = window.widgets["one"]["image"][1]
    button = window.widgets["one"]["button"][1]
    assert not picture.image.isNull() and not button.icon().isNull()
    from PySide2.QtWidgets import QApplication
    QApplication.processEvents()
    assert window.grab().save(str(tmp_path / "static-page.png"))
    assert window.imageSources() == set()
    window.navigate("two")
    assert picture.image.isNull() and button.icon().isNull()
    window.navigate("one")
    assert picture.image.pixelColor(0, 0).name() == "#cf3948"
    window.hide()
    assert picture.image.isNull() and button.icon().isNull()
    window.show()
    assert not picture.image.isNull()
    window.setSimulationState('WAITING')
    assert not picture.image.isNull()
    window.reload(presentation())
    assert not window.widgets['one']['image'][1].image.isNull()
    picture = window.widgets['one']['image'][1]
    button = window.widgets['one']['button'][1]
    window.close()
    assert picture.image.isNull() and button.icon().isNull()


def testStaticAssetIntegrityFormatAndBudget(retainedQtApplication, tmp_path):
    value = asset(tmp_path)
    with pytest.raises(ValueError, match="BUDGET"):
        readAsset(value, 1)
    with pytest.raises(ValueError, match="HASH"):
        readAsset((value[0], "0" * 64, value[2]), 4096)
    path = tmp_path / "not-image.png"
    path.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="FORMAT"):
        readAsset((str(path), hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size), 4096)


def testStaticAssetContractsAndPublish(tmp_path):
    with pytest.raises(ValueError, match="without result bindings"):
        Component(componentId="bad", type="image", props=dict(imageResourceId="picture"), bindings={"image": "dynamic"})
    with pytest.raises(ValueError, match="navigation_button"):
        Component(componentId="bad", type="text", props=dict(iconResourceId="icon"))
    payload = simulatedProject()
    payload["presentation"] = presentation().model_dump(mode="json")
    doc = ProjectDocument.model_validate(payload)
    assert len(validateBindings(doc, {}, publish=True)) == 2
    from emo_master.core.project.resources import Resource
    for key, purpose in [("picture", "page_image"), ("icon", "icon")]:
        doc.resources.items[key] = Resource(path=key + ".png", sha256="a" * 64, size=10, purpose=purpose)
    assert validateBindings(doc, {}, publish=True) == []
    doc._pageResourcePaths["picture"] = str(tmp_path / "private.png")
    assert str(tmp_path) not in doc.model_dump_json()
