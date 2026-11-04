"""Run with an isolated interpreter (-I) after installing the two delivery wheels."""
from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from fastapi.testclient import TestClient
from PIL import Image
import emo_panel_protocol
import emo_web_panel
from emo_web_panel.app import create_app
from emo_web_panel.config import Config


def main():
    evidence = {"python": sys.executable, "isolated": bool(sys.flags.isolated),
                "webPackage": emo_web_panel.__file__, "sdkPackage": emo_panel_protocol.__file__,
                "sdkVersion": emo_panel_protocol.__version__}
    assert sys.flags.isolated, "Use python -I to exclude checkout PYTHONPATH"
    assert "site-packages" in str(emo_web_panel.__file__)
    for name in ("emo_master", "PySide2", "cv2", "onnxruntime"):
        assert importlib.util.find_spec(name) is None, name + " unexpectedly installed"
    with tempfile.TemporaryDirectory(prefix="installed-web-editor-") as directory:
        app = create_app(Config(Path(directory), mode="mock"))
        password = secrets.token_urlsafe(24)
        app.state.auth.create_user("smoke-admin", password, "admin")
        with TestClient(app) as client:
            assert client.get("/editor").status_code == 200
            response = client.post("/api/session", json={"username": "smoke-admin", "password": password})
            headers = {"X-CSRF-Token": response.json()["csrf"]}
            context = client.get("/api/editor/configured/context").json()
            source = context["draft"]["presentation"]
            project = context["runtime"]["projectId"]
            first = source["defaultPageId"]
            source["pageOrder"].append("details")
            source["pages"]["details"] = {"name": "详情页", "targets": ["web"], "components": []}
            for choice in context["catalog"]["choices"]:
                kind = {"image": "image", "integer": "number", "boolean": "indicator", "collection": "table"}[choice["source"]["expectedType"]]
                source["dataSources"][choice["sourceId"]] = choice["source"]
                source["resultScopes"][choice["scopeId"]] = choice["scope"]
                page = source["pages"]["details" if kind == "table" else first]
                page["components"].append({"componentId": "c-" + choice["sourceId"], "type": kind,
                    "bindings": {{"image": "image", "table": "rows"}.get(kind, "value"): choice["sourceId"]}})
            buffer = BytesIO()
            Image.new("RGB", (8, 8), "blue").save(buffer, "PNG")
            resource = client.post("/api/editor/configured/assets", params={"projectId": project},
                headers={**headers, "Content-Type": "image/png"}, content=buffer.getvalue()).json()
            source["pages"]["details"]["components"].append({"componentId": "static", "type": "image", "props": {"imageResourceId": resource["resourceId"]}})
            saved = client.put("/api/editor/configured/draft", headers=headers, json={"projectId": project, "presentation": source, "expectedDraftRevision": ""})
            assert saved.status_code == 200, saved.text
            release = client.post("/api/editor/configured/publish", headers=headers, json={"projectId": project, "expectedDraftRevision": saved.json()["draftRevision"]}).json()
            assert release["state"] == "PUBLISHED", release
            panel = client.get("/api/panels").json()[0]
            assert panel["editorManaged"] is True
            assert client.get("/api/assets/" + panel["id"] + "/" + resource["resourceId"]).content == buffer.getvalue()
            assert app.state.runtime.control_count == 0
            evidence.update(editorEntry="PASS", fullSourceRoundTrip="PASS", staticResource="PASS", publish="PASS", noImplicitStart="PASS", pageCount=len(panel["pages"]))
    assert not any(name in sys.modules for name in ("emo_master", "PySide2", "cv2", "onnxruntime"))
    evidence["lightweightImports"] = "PASS"
    # Exercise the installed CLI HTTP server with no Node installation on PATH.
    with tempfile.TemporaryDirectory(prefix="installed-web-serve-") as directory:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment["PATH"] = os.pathsep.join([str(Path(sys.executable).parent),
            str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")])
        assert shutil.which("node", path=environment["PATH"]) is None
        with open(Path(directory) / "server.log", "w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-I", "-m", "emo_web_panel.main", "--data-dir", directory,
                "serve", "--mode", "mock", "--port", str(port)], env=environment, cwd=directory,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                            assert json.load(response)["status"] == "ok"
                        break
                    except OSError:
                        if time.monotonic() >= deadline or process.poll() is not None:
                            raise AssertionError("Installed CLI server did not start")
                        time.sleep(.1)
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/editor", timeout=3) as response:
                    assert response.status == 200
                evidence["installedCliWithoutNode"] = "PASS"
            finally:
                process.terminate()
                process.wait(timeout=10)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
