"""Portable debug evidence: immutable complete values, never session references."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from emo_master.apps.runtime.operator_debug.assets import CACHE_BYTES, VALUE_BYTES, decode
from emo_master.apps.runtime.operator_debug.contracts import encode, parse
from emo_master.apps.runtime.operator_debug.data import companionPort
from emo_master.core.contracts.port_types import matchesPortSpec


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def writeArchive(path, manifest, files, *, cancelled=lambda: False):
    """One same-directory rename commits all data and metadata together."""
    path = Path(path)
    rawManifest = encode(manifest, VALUE_BYTES).encode("utf-8")
    if len(rawManifest) + sum(map(len, files.values())) > CACHE_BYTES:
        raise ValueError("完整输入/结果包超过 256 MiB")
    temporary = path.with_name('.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
                archive.writestr('metadata.json', rawManifest)
                for name, raw in files.items():
                    if cancelled():
                        raise ValueError("导出已取消，未替换目标文件")
                    archive.writestr(name, raw)
            stream.flush()
            os.fsync(stream.fileno())
        if cancelled():
            raise ValueError("导出已取消，未替换目标文件")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def materializeReference(connection, wire):
    if 'assetRef' in wire:
        downloaded = connection.download(wire['assetRef'])
        return downloaded['content'], downloaded['asset']['mime'], deepcopy(downloaded['asset']['provenance'])
    if 'executionId' in wire:
        record = connection.call('GetOperatorDebugExecution', execution_id=wire['executionId'])
        port = wire['port']
        if record.get('status') == 'RUNNING':
            raise ValueError('执行尚未完成，不能导出临时结果')
        if port in record.get('outputAssets', {}):
            return materializeReference(connection, {'assetRef': record['outputAssets'][port]['assetId']})
        if port not in record.get('outputs', {}):
            raise ValueError('完整输出已过期，未导出摘要替代品')
        provenance = dict(record.get('sourceIdentity', {}), kind='debug-output', captureId=wire['executionId'])
        return encode(record['outputs'][port], VALUE_BYTES).encode('utf-8'), 'application/json', provenance
    if 'inline' in wire:
        return encode(wire['inline'], VALUE_BYTES).encode('utf-8'), 'application/json', {'kind': 'inline'}
    raise ValueError('没有可保存的完整数据源')


def saveFixture(path, name, rows, identity, parameters, connection, *, cancelled=lambda: False):
    entries, files = {}, {}
    for port, row in rows.items():
        item = dict(type=row['type'], mode=row['mode'], companionPort=row.get('companionPort'))
        wire = row['wire']
        if row['mode'] in {'value', 'null'}:
            item['value'] = deepcopy(wire['inline'])
        elif row['mode'] == 'source':
            raw, mime, provenance = materializeReference(connection, wire)
            decode(raw, mime)
            filename = 'assets/' + _sha(raw) + ('.png' if mime == 'image/png' else '.json')
            files[filename] = raw
            item.update(file=filename, mime=mime, sha256=_sha(raw), bytes=len(raw), provenance=provenance)
        entries[port] = item
    manifest = dict(format='emo-debug-fixture', version=1, name=name, identity=identity,
                    parameters=parameters, ports=entries)
    writeArchive(path, manifest, files, cancelled=cancelled)
    return str(path)


def readFixture(path, types):
    """Validate the entire package before any Runtime upload or row mutation."""
    if Path(path).stat().st_size > CACHE_BYTES:
        raise ValueError('输入集文件超过 256 MiB')
    with ZipFile(path) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        if len(members) > 1025 or len(set(name.casefold() for name in names)) != len(names):
            raise ValueError('输入集包含重复路径或过多文件')
        if sum(member.file_size for member in members) > CACHE_BYTES:
            raise ValueError('输入集解压后超过 256 MiB')
        if 'metadata.json' not in names or archive.getinfo('metadata.json').file_size > VALUE_BYTES:
            raise ValueError('输入集缺少合法元数据')
        manifest = parse(archive.read('metadata.json').decode('utf-8'), VALUE_BYTES)
        if not isinstance(manifest, dict) or manifest.get('format') != 'emo-debug-fixture' or type(manifest.get('version')) is not int or manifest['version'] != 1:
            raise ValueError('不支持的输入集格式或版本')
        if (not isinstance(manifest.get('name'), str) or not 0 < len(manifest['name']) <= 256
                or not isinstance(manifest.get('identity'), dict) or not isinstance(manifest.get('parameters'), dict)):
            raise ValueError('输入集名称、身份或参数元数据无效')
        ports = manifest.get('ports')
        if not isinstance(ports, dict) or set(ports) != set(types):
            raise ValueError('输入集端口与当前输入不一致，未覆盖任何输入')
        files = {}
        used = {'metadata.json'}
        for port, item in ports.items():
            if not isinstance(item, dict) or item.get('type') != types[port] or item.get('mode') not in {'missing', 'value', 'null', 'source'}:
                raise ValueError('输入集类型/模式不匹配：' + port)
            if item['mode'] == 'source':
                name, mime = item.get('file'), item.get('mime')
                if (not isinstance(name, str) or mime not in {'image/png', 'application/json'}
                        or name != 'assets/' + str(item.get('sha256')) + ('.png' if mime == 'image/png' else '.json')
                        or name not in names or archive.getinfo(name).file_size > VALUE_BYTES):
                    raise ValueError('输入集数据路径或大小无效：' + port)
                raw = archive.read(name)
                if _sha(raw) != item.get('sha256') or len(raw) != item.get('bytes') or not isinstance(item.get('provenance'), dict):
                    raise ValueError('输入集数据摘要/来源校验失败：' + port)
                value, _charge = decode(raw, mime)
                if (types[port] == 'image') != (mime == 'image/png'):
                    raise ValueError('图像必须使用完整 PNG 数据源：' + port)
                if not matchesPortSpec(value, {'type': types[port], 'nullable': True}):
                    raise ValueError('完整数据与端口类型不符：' + port)
                files[name] = raw
                used.add(name)
            elif item['mode'] == 'value':
                if types[port] == 'image' or 'value' not in item or item['value'] is None or not matchesPortSpec(item['value'], {'type': types[port], 'nullable': False}):
                    raise ValueError('输入集手工值类型错误：' + port)
            elif item['mode'] == 'null' and item.get('value', 'absent') is not None:
                raise ValueError('输入集空值模式错误：' + port)
        for port, item in ports.items():
            companion = companionPort(port, ports) if types[port] == 'image' else None
            if item.get('companionPort') not in (None, companion):
                raise ValueError('输入集关联 frame 端口错误：' + port)
            if item['mode'] in {'missing', 'null'} or companion not in ports:
                continue
            frame = ports[companion]
            if frame['mode'] in {'missing', 'null'}:
                continue
            capture = item.get('provenance', {}).get('captureId')
            if not capture or capture != frame.get('provenance', {}).get('captureId'):
                raise ValueError('图像与 frame 不是同一完整来源，未替换输入：' + port)
        if set(names) != used:
            raise ValueError('输入集包含未声明的文件')
    return manifest, files


def loadFixture(path, types, connection, *, cancelled=lambda: False):
    manifest, files = readFixture(path, types)
    values = {}
    for port, item in manifest['ports'].items():
        if cancelled():
            raise ValueError('输入集加载已取消，未更改输入')
        mode = item['mode']
        wire = None
        if mode == 'source':
            wire = connection.upload(files[item['file']], item['mime'],
                dict(item['provenance'], fixtureName=manifest.get('name', ''), fixtureSha256=_sha(files[item['file']])))
        elif mode in {'value', 'null'}:
            wire = {'inline': deepcopy(item['value'])}
        values[port] = dict(mode=mode, wire=wire, label=f'{manifest.get("name", "")} / {port}', type=item['type'])
    return dict(values=values, name=manifest.get('name', ''), parameters=manifest.get('parameters', {}))


def exportResult(path, selection, connection, *, cancelled=lambda: False):
    raw, mime, provenance = materializeReference(connection, selection['wire'])
    decode(raw, mime)
    filename = 'value.png' if mime == 'image/png' else 'value.json'
    metadata = dict(format='emo-debug-result', version=1, identity=selection['identity'],
                    port=selection['port'], provenance=provenance, mime=mime,
                    bytes=len(raw), sha256=_sha(raw), file=filename)
    writeArchive(path, metadata, {filename: raw}, cancelled=cancelled)
    return str(path)


def exportRawResult(path, selection, connection, *, cancelled=lambda: False):
    """Raw-file convenience; the evidence archive separately retains identity."""
    raw, mime, _provenance = materializeReference(connection, selection['wire'])
    decode(raw, mime)
    path = Path(path)
    if path.suffix.lower() != ('.png' if mime == 'image/png' else '.json'):
        raise ValueError('文件扩展名与原始数据类型不匹配，未写入')
    temporary = path.with_name('.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if cancelled():
            raise ValueError('导出已取消，未替换目标文件')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return str(path)
