from pathlib import Path
import hashlib, json
root = Path(__file__).resolve().parent
for row in json.loads((root / "ZIP-PARTS.json").read_text()):
    chunks = []
    for item in row["parts"]:
        data = (root / item["name"]).read_bytes()
        assert len(data) == item["bytes"] and hashlib.sha256(data).hexdigest() == item["sha256"], item["name"]
        chunks.append(data)
    data = b"".join(chunks)
    assert len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"], row["original"]
    target = root / row["original"]
    if target.exists():
        assert target.read_bytes() == data, "Refusing to overwrite different file: " + str(target)
    else:
        target.write_bytes(data)
    print("Verified", row["original"])
