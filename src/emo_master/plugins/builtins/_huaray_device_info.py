"""IMV 2.4.2.3 device-info ABI from the vendor's IMVDefines.h (20240711)."""
import ctypes as c

String = c.c_char * 256


class GigEDeviceInfo(c.Structure):
    _fields_ = [('nIpConfigOptions', c.c_uint), ('nIpConfigCurrent', c.c_uint), ('nReserved', c.c_uint * 3),
        *[(name, String) for name in ('macAddress', 'ipAddress', 'subnetMask', 'defaultGateWay', 'protocolVersion', 'ipConfiguration')],
        ('chReserved', String * 6)]


class UsbDeviceInfo(c.Structure):
    _fields_ = [*[(name, c.c_bool) for name in ('bLowSpeedSupported', 'bFullSpeedSupported', 'bHighSpeedSupported',
        'bSuperSpeedSupported', 'bDriverInstalled')], ('boolReserved', c.c_bool * 3), ('Reserved', c.c_uint * 4),
        *[(name, String) for name in ('configurationValid', 'genCPVersion', 'u3vVersion', 'deviceGUID', 'familyName',
                                     'u3vSerialNumber', 'speed', 'maxPower')], ('chReserved', String * 4)]


class DeviceSpecificInfo(c.Union):
    _fields_ = [('gigeDeviceInfo', GigEDeviceInfo), ('usbDeviceInfo', UsbDeviceInfo)]


class GigEInterfaceInfo(c.Structure):
    _fields_ = [(name, String) for name in ('description', 'macAddress', 'ipAddress', 'subnetMask', 'defaultGateWay')] + [
        ('chReserved', String * 5)]


class UsbInterfaceInfo(c.Structure):
    _fields_ = [(name, String) for name in ('description', 'vendorID', 'deviceID', 'subsystemID', 'revision', 'speed')] + [
        ('chReserved', String * 4)]


class InterfaceInfo(c.Union):
    _fields_ = [('gigeInterfaceInfo', GigEInterfaceInfo), ('usbInterfaceInfo', UsbInterfaceInfo)]


class DeviceInfo(c.Structure):
    _fields_ = [('nCameraType', c.c_int), ('nCameraReserved', c.c_int * 5),
        *[(name, String) for name in ('cameraKey', 'cameraName', 'serialNumber', 'vendorName', 'modelName', 'manufactureInfo', 'deviceVersion')],
        ('cameraReserved', String * 5), ('DeviceSpecificInfo', DeviceSpecificInfo),
        ('nInterfaceType', c.c_int), ('nInterfaceReserved', c.c_int * 5), ('interfaceName', String),
        ('interfaceReserved', String * 5), ('InterfaceInfo', InterfaceInfo)]


def deviceRecord(info):
    def text(value):
        return bytes(value).split(b'\0', 1)[0].decode('utf-8', errors='replace')
    record = {name: text(getattr(info, name)) for name in ('cameraKey', 'cameraName', 'serialNumber', 'vendorName', 'modelName')}
    record.update(cameraType=int(info.nCameraType), interfaceType=int(info.nInterfaceType))
    if info.nCameraType == 0:
        network = info.DeviceSpecificInfo.gigeDeviceInfo
        record.update({name: text(getattr(network, name)) for name in ('macAddress', 'ipAddress', 'subnetMask', 'defaultGateWay')})
        record['interfaceIp'] = text(info.InterfaceInfo.gigeInterfaceInfo.ipAddress)
        record['ipConfigCurrent'] = int(network.nIpConfigCurrent)
    return record
