"""Browser-free compatibility check for the actual Playwright fixture callback.

This exercises Playwright's Python wrapper, not browser behavior or the product.
"""
import ast
from pathlib import Path
import unittest

from playwright._impl._sync_base import mapping


class BrowserHarnessTests(unittest.TestCase):
    def test_dispatch_socket_callback_supports_playwright_wrapping(self):
        # dot: execute the callback expression used by the browser script itself,
        # so replacing it with the incompatible built-in append regresses here.
        path = Path(__file__).with_name('editor_dispatch_browser.py')
        tree = ast.parse(path.read_text(encoding='utf-8'))
        registration = next(node for node in ast.walk(tree)
                            if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)
                            and node.func.attr == 'route_web_socket')
        sockets = []
        callback = eval(compile(ast.Expression(registration.args[1]), str(path), 'eval'),
                        {'sockets': sockets})
        wrapped = mapping.wrap_handler(callback)
        first, second = object(), object()
        wrapped(first)
        wrapped(second)
        self.assertEqual(sockets, [first, second])
        self.assertIs(mapping.wrap_handler(callback), wrapped)


if __name__ == '__main__':
    unittest.main()
