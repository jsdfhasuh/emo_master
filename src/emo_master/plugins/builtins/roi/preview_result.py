"""Immutable, same-request ROI pixels and honest client-side provenance.

The pure-preview protocol does not return resolved parameters or an executionId.
Requested editor values must never be labelled as actual execution parameters.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile


@dataclass(frozen=True)
class PreviewImage:
    port: str
    assetId: str
    content: bytes
    width: int
    height: int

    def descriptor(self):
        return dict(port=self.port, assetId=self.assetId, file=self.port + ".png",
                    mimeType="image/png", width=self.width, height=self.height,
                    byteSize=len(self.content), sha256=hashlib.sha256(self.content).hexdigest())


@dataclass(frozen=True)
class RoiPreviewSnapshot:
    metadataJson: str
    outputsJson: str
    images: tuple[PreviewImage, ...]

    def resultJson(self):
        return json.dumps(dict(schemaVersion=1, kind="roi-pure-preview-client-snapshot",
            preview=json.loads(self.metadataJson), outputs=json.loads(self.outputsJson),
            images=[image.descriptor() for image in self.images]),
            ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")

    def writeArchive(self, stream):
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("result.json", self.resultJson())
            for image in self.images:
                archive.writestr(image.port + ".png", image.content)


def atomicWrite(path, writer, validate):
    """Replace only a fully written current selection; preserve old file on error.

    Called on the GUI thread. No worker may inspect a mutable Qt draft, and a
    modal file picker cannot make an old selection current again on return.
    """
    path = Path(path)
    temporary = None
    try:
        validate()
        with tempfile.NamedTemporaryFile(prefix=".roi-preview-", suffix=".tmp",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        validate()
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
