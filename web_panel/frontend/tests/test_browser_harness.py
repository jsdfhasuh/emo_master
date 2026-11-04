"""Browser-free compatibility check for the actual Playwright fixture callback.

This exercises Playwright's Python wrapper, not browser behavior or the product.
"""
import ast
from pathlib import Path
import unittest

from playwright._impl._sync_base import mapping


class BrowserHarnessTests(unittest.TestCase):
    def test_package_spacer_assertion_checks_option_not_enabled_parent_select(self):
        # A disabled option inside a label still has an enabled parent select.
        # Playwright's enabled-state retargeting follows that label's control.
        path = Path(__file__).with_name('editor_package_browser.py')
        tree = ast.parse(path.read_text(encoding='utf-8'))
        mode = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == 'mode')
        case = self

        class Field:
            value = 'fixed'
            disabled = True

            def get_by_label(self, label):
                case.assertEqual(label, '空白模式')
                return self

            def locator(self, selector):
                case.assertEqual(selector, 'option[value="flex"]')
                return self

            def is_enabled(self):
                return True  # Enabled parent select, not the option's state.

            def to_have_value(self, value):
                case.assertEqual(self.value, value)

            def to_have_js_property(self, name, value):
                case.assertEqual(name, 'disabled')
                case.assertIs(self.disabled, value)

        field = Field()
        namespace = {'page': field, 'expect': lambda locator: locator}
        exec(compile(ast.Module(body=[mode], type_ignores=[]), str(path), 'exec'), namespace)
        assertion = namespace['mode']
        assertion('fixed', False)
        field.value, field.disabled = 'flex', False
        assertion('flex', True)
        with self.assertRaises(AssertionError):
            assertion('flex', False)
        with self.assertRaises(AssertionError):
            assertion('fixed', True)

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
