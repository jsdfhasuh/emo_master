"""Verify GitHub's outer artifact digest and a decrypted evidence manifest.
Output only a verification receipt. Never print report bodies or key material.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
from zipfile import ZipFile

from scripts.ci_evidence import FILES, MAX_MEMBER, MAX_TOTAL

MIB = 1024 * 1024


def unpack(archive, digest, age, identity, expected, *, verified_members=False):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or archive.stat().st_size > 52 * MIB:
        raise ValueError("bad outer archive")
    with archive.open("rb") as src:
        h = hashlib.sha256()
        for chunk in iter(lambda: src.read(MIB), b""):
            h.update(chunk)
        if "sha256:" + h.hexdigest() != digest:
            raise ValueError("central repository digest mismatch")
        src.seek(0)
        with ZipFile(src) as bundle:
            files = bundle.infolist()
            if len(files) != 1 or files[0].filename != "evidence.tar.age" or files[0].file_size > 52 * MIB:
                raise ValueError("unexpected artifact members")
            ciphertext = bundle.read(files[0])
    if not ciphertext.startswith(b"age-encryption.org/v1\n"):
        raise ValueError("not an age ciphertext")
    result = subprocess.run([str(age), "-d", "-i", str(identity)], input=ciphertext, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, check=True, timeout=90)
    tmp = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as tar:
        for member in tar:
            if not member.isfile() or member.name not in FILES[expected["suite_family"]] | {"manifest.json"}:
                raise ValueError("unexpected plaintext member")
            if member.name in tmp or member.size > MAX_MEMBER or total + member.size > MAX_TOTAL + MIB:
                raise ValueError("oversized or duplicate member")
            total += member.size
            with tar.extractfile(member) as handle:
                tmp[member.name] = handle.read(member.size + 1)
    manifest = json.loads(tmp.pop("manifest.json"))
    if manifest.get("version") != 1 or any(manifest.get(key) != val for key, val in expected.items()):
        raise ValueError("mismatched artifact identity")
    actual = {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in tmp.items()}
    if actual != manifest.get("files"):
        raise ValueError("member digest mismatch")
    return tmp if verified_members else actual


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for item in ("archive", "age", "identity", "expected"):
        p.add_argument(item, type=Path)
    p.add_argument("digest")
    a = p.parse_args()
    try:
        files = unpack(a.archive, a.digest, a.age, a.identity, json.loads(a.expected.read_text(encoding="ascii")))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, tarfile.TarError):
        print("evidence_recovery=failure")
        raise SystemExit(1) from None
    print(f"evidence_recovery=success members={len(files)}")
