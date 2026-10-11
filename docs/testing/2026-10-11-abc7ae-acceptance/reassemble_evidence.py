#!/usr/bin/env python3
"""Python 3, Windows/macOS/Linux: verify parts and reconstruct the original ZIP.
Does not extract archives, launch probes, or overwrite existing output.
Usage: python reassemble_evidence.py [new-output-file.zip]
"""
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'EVIDENCE-PARTS.json').read_text(encoding='utf-8'))
output = Path(sys.argv[1]) if len(sys.argv) > 1 else root / manifest['combined_name']
if output.exists():
    raise SystemExit('Refusing to overwrite existing output: ' + str(output))
for item in manifest['parts']:
    path = root / item['path']
    data = path.read_bytes()
    if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
        raise SystemExit('Part verification failed: ' + item['path'])
hash_value = hashlib.sha256()
size = 0
with output.open('xb') as destination:
    for item in manifest['parts']:
        with (root / item['path']).open('rb') as source:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                destination.write(block)
                hash_value.update(block)
                size += len(block)
if size != manifest['combined_bytes'] or hash_value.hexdigest() != manifest['combined_sha256']:
    raise SystemExit('Combined verification failed; retain output for inspection.')
print('Verified:', output)
print('Bytes:', size)
print('SHA256:', hash_value.hexdigest())
