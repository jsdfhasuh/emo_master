"""Runtime-side file admission for explicit single-node image reads."""
from pathlib import Path
import stat

from .assets import VALUE_BYTES
from .contracts import DebugError, fail


def validateRuntimeImagePath(params):
    raw = params.get("imagePath", "")
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        fail("E_PARAM_INVALID", "图像路径必须是 Runtime 所在电脑上的有效绝对路径。")
    path = Path(raw.strip())
    # The debug worker runs in a temporary directory, not the project directory
    # or Designer's cwd. Never silently reinterpret a relative file reference.
    if not path.is_absolute():
        fail("E_PARAM_INVALID", "图像调试需要 Runtime 所在电脑上的绝对路径，不支持相对路径：" + raw)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            fail("E_INPUT_MISSING", "Runtime 图像路径不是普通文件：" + str(path))
        if info.st_size > VALUE_BYTES:
            fail("E_DEBUG_LIMIT", "Runtime 图像文件超过调试读取上限 64 MiB：" + str(path))
        with path.open("rb"):
            pass
    except FileNotFoundError:
        fail("E_INPUT_MISSING", "Runtime 所在电脑找不到图像文件；不会自动上传 Designer 本地文件：" + str(path))
    except PermissionError:
        fail("E_INPUT_UNREADABLE", "Runtime 没有权限读取图像文件：" + str(path))
    except DebugError:
        raise
    except (OSError, ValueError) as error:
        fail("E_INPUT_UNREADABLE", "Runtime 无法访问图像文件：" + str(path) + "；" + str(error))
