"""Chromium + real Web HTTP regressions; explicit mock Runtime, no devices.

Build and package the frontend first. Pass --browser for an installed Chromium.
"""
import argparse
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import uvicorn
from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from emo_web_panel.app import create_app
from emo_web_panel.config import Config
from emo_web_panel.editor import blank_presentation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(Config(Path(directory)))
        app.state.auth.create_user("editor", "synthetic-test-password", "admin")
        source = blank_presentation()
        gap = {"componentId": "root-gap", "type": "spacer", "props": {"spacerMode": "flex"}}
        nested = {**deepcopy(gap), "componentId": "nested-gap"}
        source["pages"][source["defaultPageId"]]["components"] = [
            gap, {"componentId": "box", "type": "container", "grid": {"type": "responsive"}, "children": [nested]},
            {"componentId": "logo", "type": "image"}]
        app.state.editor.save("configured", "demo-project", source)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
            worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            worker.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started and worker.is_alive() and time.monotonic() < deadline:
                    time.sleep(.05)
                assert server.started, "Web server did not start"
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True, **({"executable_path": args.browser} if args.browser else {}))
                    page = browser.new_page(viewport={"width": 1600, "height": 1100})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(origin + "/editor")
                    page.get_by_label("用户名", exact=True).fill("editor")
                    page.get_by_label("密码", exact=True).fill("synthetic-test-password")
                    page.get_by_role("button", name="登录", exact=True).click()
                    page.get_by_label("页面名称", exact=True).wait_for()

                    def select(cid):
                        page.locator(f'[data-editor-component="{cid}"]').click(position={"x": 10, "y": 10})

                    def mode(value, enabled):
                        field = page.get_by_label("空白模式")
                        expect(field).to_have_value(value)
                        assert field.locator('option[value="flex"]').is_enabled() == enabled

                    # Root layout conversion is one undoable edit, with a usable fixed grid spacer.
                    select("root-gap")
                    mode("flex", True)
                    page.get_by_label("布局类型", exact=True).first.select_option("grid")
                    mode("fixed", False)
                    page.get_by_role("button", name="撤销", exact=True).click()
                    mode("flex", True)
                    page.get_by_role("button", name="重做", exact=True).click()
                    mode("fixed", False)

                    # A responsive child is not changed by its ancestor's grid, only its own parent.
                    select("nested-gap")
                    mode("flex", True)
                    select("box")
                    page.get_by_label("布局类型", exact=True).last.select_option("grid")
                    select("nested-gap")
                    mode("fixed", False)
                    page.get_by_role("button", name="撤销", exact=True).click()
                    mode("flex", True)
                    page.get_by_role("button", name="重做", exact=True).click()
                    mode("fixed", False)

                    # Moving or pasting an elastic spacer into a grid normalizes the copied source.
                    page.get_by_label("布局类型", exact=True).first.select_option("responsive")
                    select("root-gap")
                    page.get_by_label("空白模式").select_option("flex")
                    select("root-gap")
                    page.keyboard.press("Control+c")
                    page.locator('[data-editor-component="root-gap"]').drag_to(page.locator('[data-editor-component="box"] > .editor-container'))
                    select("root-gap")
                    mode("fixed", False)
                    page.get_by_label("布局类型", exact=True).first.select_option("grid")
                    page.locator(".editor-canvas-toolbar span").click()
                    page.keyboard.press("Control+v")
                    mode("fixed", False)

                    # Upload the previously rejected ordinary solid-color BMP through the UI.
                    select("logo")
                    image = BytesIO()
                    Image.new("RGB", (1024, 1024), "white").save(image, "BMP")
                    with page.expect_response(lambda r: "/assets?" in r.url and r.request.method == "POST") as upload:
                        page.locator('input[type="file"]').set_input_files({"name": "white.bmp", "mimeType": "image/bmp", "buffer": image.getvalue()})
                    assert upload.value.ok, upload.value.text()
                    with page.expect_response(lambda r: r.url.endswith("/draft") and r.request.method == "PUT") as saved:
                        page.get_by_role("button", name="保存草稿", exact=True).click()
                    assert saved.value.ok, saved.value.text()
                    page.reload()
                    page.get_by_label("页面名称", exact=True).wait_for()
                    select("root-gap")
                    mode("fixed", False)
                    select("nested-gap")
                    mode("fixed", False)
                    with page.expect_response(lambda r: r.url.endswith("/preview")) as preview:
                        page.get_by_role("button", name="示例预览", exact=True).click()
                    assert preview.value.ok, preview.value.text()
                    page.get_by_role("dialog").wait_for()
                    page.get_by_role("button", name="关闭预览", exact=True).click()
                    with page.expect_response(lambda r: r.url.endswith("/publish")) as published:
                        page.get_by_role("button", name="发布", exact=True).click()
                    assert published.value.ok, published.value.text()
                    assert published.value.json()["state"] == "PUBLISHED", published.value.text()
                    assert app.state.runtime.control_count == 0
                    assert not errors, errors
                    browser.close()
                    print("PASS: root/nested grid, undo/redo, drag/paste, BMP upload, save/reopen, preview and publish; mock Runtime only")
            finally:
                server.should_exit = True
                worker.join(timeout=10)


if __name__ == "__main__":
    main()
