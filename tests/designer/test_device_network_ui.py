from copy import deepcopy
from threading import Event
from time import monotonic, sleep

from PySide2.QtWidgets import QApplication, QMessageBox

from emo_master.apps.designer.ui.device_network import DeviceNetworkDialog
from tests.runtime.test_device_network import ROW


class Client:
    def __init__(self):
        self.calls = []
        self.release = Event()
        self.release.set()

    def deviceNetworkCommand(self, project, runtime, action, **kwargs):
        self.calls.append((action, kwargs))
        assert self.release.wait(3)
        if action == 'discover':
            return dict(discoveryId='discovery', devices=[deepcopy(ROW)], sdkVersion='synthetic')
        return dict(state='COMPLETED', requestId=kwargs['requestId'], after=dict(ROW, ipAddress='192.0.2.20'))


def finish(dialog):
    deadline = monotonic() + 5
    while dialog.pending is not None and monotonic() < deadline:
        QApplication.processEvents()
        dialog.finish()
        sleep(.005)
    assert dialog.pending is None


def testDiscoveryAndHardwareMutationAreSeparateConfirmedActions(retainedQtApplication, tmp_path, monkeypatch):
    client = Client()
    dialog = DeviceNetworkDialog(client, 'project', 'runtime', 'camera', dict(generation='generation', definitionRevision='revision'))
    dialog.show()
    try:
        assert not client.calls
        dialog.discoverButton.click()
        finish(dialog)
        dialog.table.selectRow(0)
        assert dialog.fields['ipAddress'].text() == ROW['ipAddress']
        assert dialog.selectedCameraKey is None
        dialog.fields['ipAddress'].setText('192.0.2.20')
        monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.No)
        dialog.forceButton.click()
        assert len(client.calls) == 1
        QApplication.processEvents()
        assert dialog.grab().save(str(tmp_path / 'device-network.png'))
        monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.Yes)
        dialog.forceButton.click()
        finish(dialog)
        assert [call[0] for call in client.calls] == ['discover', 'force_ip']
        request = client.calls[-1][1]
        assert request['payload']['confirmed'] is True and request['payload']['expectedGeneration'] == 'generation'
        assert request['payload']['ipAddress'] == '192.0.2.20'
        assert dialog.selectedCameraKey is None and dialog.receiptId.text() == request['requestId']
        assert not dialog.forceButton.isEnabled()
        dialog.receiptButton.click()
        finish(dialog)
        assert client.calls[-1][0] == 'status'
    finally:
        client.release.set()
        finish(dialog)
        dialog.close()


def testAdoptingSelectorDoesNotMutateHardwareAndPendingCloseIsFenced(retainedQtApplication):
    client = Client()
    dialog = DeviceNetworkDialog(client, 'project', 'runtime', 'camera', {})
    dialog.show()
    try:
        client.release.clear()
        dialog.discoverButton.click()
        dialog.reject()
        dialog.close()
        assert dialog.isVisible()
        client.release.set()
        finish(dialog)
        dialog.table.selectRow(0)
        dialog.adoptButton.click()
        assert dialog.selectedCameraKey == ROW['cameraKey']
        assert [call[0] for call in client.calls] == ['discover']
    finally:
        client.release.set()
        finish(dialog)
        dialog.close()
