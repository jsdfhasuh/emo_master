"""Apply authored QSS only to the rendered parts of its owning object."""
import re

from PySide2.QtWidgets import QAbstractItemView, QAbstractButton, QHeaderView, QTabBar, QWidget


_TOKEN = re.compile(
    r"""/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\\.|[{},;:()[\]>+~\s]"""
    r"""|(?:(?!/\*)[^\s{},;:()[\]>+~"'\\])+"""
)


def _tokens(code):
    end = 0
    for match in _TOKEN.finditer(code):
        if match.start() != end:
            raise ValueError('字符串、转义或注释未闭合')
        end = match.end()
        token = match.group()
        if token.startswith('/*'):
            yield ' '
        else:
            yield token
    if end != len(code):
        raise ValueError('字符串、转义或注释未闭合')


def _rules(code):
    selectors, buffer, groups = [], [], []
    inRule = False
    for token in _tokens(code):
        if token in ('[', '('):
            groups.append(']' if token == '[' else ')')
        elif token in (']', ')'):
            if not groups or groups.pop() != token:
                raise ValueError('选择器或样式值的括号未配对')
        if not groups and token in ('{', '}', ','):
            if token == '}' and inRule:
                declarations = ''.join(buffer)
                _checkDeclarations(declarations)
                yield selectors, declarations
                selectors, buffer, inRule = [], [], False
                continue
            if token in ('{', ',') and not inRule:
                selector = ''.join(buffer).strip()
                if not selector or selector.startswith('@'):
                    raise ValueError('请输入普通 QSS 选择器，不支持 @ 规则')
                if selector.endswith(('>', '+', '~', ':', '=', '#', '.')):
                    raise ValueError('选择器不完整')
                selectors.append(selector)
                buffer, inRule = [], token == '{'
                continue
            if token != ',':
                raise ValueError('样式规则的大括号未配对')
        buffer.append(token)
    if inRule or groups or selectors or ''.join(buffer).strip():
        raise ValueError('样式规则不完整，应为 选择器 { 属性: 值; }')


def _checkDeclarations(declarations):
    name, depth = None, 0
    buffer = []
    for token in (*_tokens(declarations), ';'):
        if token == '(':
            depth += 1
        elif token == ')':
            depth -= 1
        if token == ':' and not depth and name is None:
            name = ''.join(buffer).strip()
            if name.lower().startswith('qproperty-'):
                raise ValueError('自定义样式不支持 qproperty-*')
            if not re.fullmatch(r'[a-zA-Z_-][a-zA-Z_0-9-]*', name):
                raise ValueError('样式属性名无效')
            buffer = []
        elif token == ';' and not depth:
            hasValue = bool(''.join(buffer).strip())
            if (name is None and hasValue) or (name is not None and not hasValue):
                raise ValueError('样式声明应包含属性名和值，并以分号分隔')
            name, buffer = None, []
        elif token == ':' and not depth:
            raise ValueError('请用分号分隔样式声明')
        else:
            buffer.append(token)


def validateQss(code):
    """Diagnose a limited set of concrete errors before Qt renders the rule."""
    try:
        list(_rules(code))
    except ValueError as error:
        return str(error)
    return ''


def _scoped(selector, scope):
    offset = 0
    target = 0
    pseudo = None
    inAttribute = False
    for token in _tokens(selector):
        if token == '[':
            inAttribute = True
        elif token == ']':
            inAttribute = False
        elif not inAttribute:
            if token.isspace() or token in ('>', '+', '~'):
                target, pseudo = offset + len(token), None
            elif token == ':' and pseudo is None:
                pseudo = offset
        offset += len(token)
    split = offset if pseudo is None else pseudo
    return selector[:split] + ('*' if split == target else '') + scope + selector[split:]


class NativeAppearance:
    def __init__(self, root, settings):
        self.owner = str(id(root))
        self.scope = f'[emoAppearanceOwner="{self.owner}"]'
        branch = settings.qtQss if settings else None
        self.sheet = ''
        if branch and branch.enabled and branch.code:
            try:
                # Give authored rules consistent precedence over the base and state
                # declarations while preserving the relative specificity of each rule.
                scope = self.scope + '[emoPart]' * 4
                self.sheet = ''.join(
                    ' , '.join(_scoped(selector, scope) for selector in selectors)
                    + f'{{{declarations}}}' for selectors, declarations in _rules(branch.code))
            except ValueError:
                pass

    def apply(self, widget, part, base='', *, rules=()):
        widget.setProperty('emoAppearanceOwner', self.owner)
        widget.setProperty('emoPart', part)
        selector = f'*{self.scope}[emoPart="{part}"]'
        sheet = f'{selector}{{{base}}}' if base else ''
        sheet += ''.join(f'{selector}{state}{{{style}}}' for state, style in rules if style)
        sheet += self.sheet
        if widget.styleSheet() != sheet:
            widget.setStyleSheet(sheet)


def selectorHints(root):
    """List the selectors for actual, owned widgets in the current preview."""
    owner = root.property('emoAppearanceOwner')
    if not owner:
        return []
    selectors = []
    for widget in (root, *root.findChildren(QWidget)):
        if widget.property('emoAppearanceOwner') != owner:
            continue
        part = widget.property('emoPart')
        if widget is not root and part != 'marker' and not widget.isVisibleTo(root):
            continue
        selector = f'*[emoPart="{part}"]'
        if isinstance(widget, QHeaderView):
            selector = f'QHeaderView[emoPart="{part}"]::section'
        elif isinstance(widget, QAbstractItemView):
            selector = f'QTableView[emoPart="{part}"]'
            selectors.extend((selector + '::item', selector + '::item:selected'))
        elif isinstance(widget, QTabBar):
            selector = f'QTabBar[emoPart="{part}"]::tab'
            selectors.append(selector + ':selected')
        elif isinstance(widget, QAbstractButton):
            for state in (':checked',) if widget.isCheckable() else (':enabled:hover', ':enabled:pressed', ':disabled'):
                selectors.append(selector + state)
        if selector not in selectors:
            selectors.append(selector)
    return list(dict.fromkeys(selectors))
