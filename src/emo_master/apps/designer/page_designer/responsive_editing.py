"""Ordered page layout commands; all mutations share the project transaction."""
from uuid import uuid4

from emo_master.core.presentation.models import Component, Props, ResponsiveLayout, walkComponents
from emo_master.apps.designer.state.presentation_store import _component


def parentOf(presentation, pageId, key):
    def visit(children, parentId):
        for index, child in enumerate(children):
            if child.componentId == key:
                return parentId, children, index
            found = visit(child.children, child.componentId)
            if found is not None:
                return found
        return None
    result = visit(presentation.pages[pageId].components, None)
    if result is None:
        raise KeyError(key)
    return result


def layoutFor(presentation, pageId, parentId):
    return (_component(presentation, pageId, parentId).grid if parentId
            else presentation.pages[pageId].layout)


def childrenFor(presentation, pageId, parentId):
    if parentId is None:
        return presentation.pages[pageId].components
    parent = _component(presentation, pageId, parentId)
    if parent.type != 'container':
        raise ValueError('请把组件放入页面或容器')
    return parent.children


def selectionRoots(presentation, pageId, keys):
    keys = set(keys)
    result = []
    def visit(children):
        for child in children:
            if child.componentId in keys:
                result.append(child)
            else:
                visit(child.children)
    visit(presentation.pages[pageId].components)
    return result


class ResponsiveCommands:
    def __init__(self, session):
        self.session = session

    def place(self, pageId, *, keys=(), kind=None, parentId=None, index=None,
              besideId=None, direction=None, before=False):
        selected = []
        def edit(p):
            items = selectionRoots(p, pageId, keys)
            if kind is not None:
                items = [Component(componentId=str(uuid4()), type=kind,
                    props=Props(fontSize=24, fontWeight='bold') if kind == 'page_title' else Props())]
            if not items:
                return
            subtree = {c.componentId for item in items for c in walkComponents([item])}
            if parentId in subtree or besideId in subtree:
                raise ValueError('容器不能移入自身或子组件')
            destination = childrenFor(p, pageId, parentId)
            position = len(destination) if index is None else max(0, min(index, len(destination)))
            preceding = [c.componentId for c in destination[:position] if c.componentId not in subtree]
            for item in items:
                if item.componentId in keys:
                    _parent, siblings, old = parentOf(p, pageId, item.componentId)
                    siblings.pop(old)
            if besideId is not None:
                targetParent, siblings, position = parentOf(p, pageId, besideId)
                target = siblings[position]
                parentLayout = layoutFor(p, pageId, targetParent)
                if parentLayout.type == 'responsive' and parentLayout.direction == direction:
                    destination = siblings
                    position += 0 if before else 1
                else:
                    members = [*items, target] if before else [target, *items]
                    group = Component(componentId=str(uuid4()), type='container',
                                      grid=ResponsiveLayout(direction=direction), children=members)
                    siblings[position] = group
                    selected.extend(item.componentId for item in items)
                    return
            else:
                position = sum(c.componentId in preceding for c in destination)
            destination[position:position] = items
            selected.extend(item.componentId for item in items)
        self.session.editPresentation(edit)
        return selected

    def group(self, pageId, keys, direction):
        groupId = str(uuid4())
        def edit(p):
            items = selectionRoots(p, pageId, keys)
            if not items:
                raise ValueError('请先选择要编组的组件')
            parents = [parentOf(p, pageId, item.componentId)[0] for item in items]
            # Explicit grouping may gather objects from separate containers.
            parentId = parents[0] if all(parent == parents[0] for parent in parents) else None
            siblings = childrenFor(p, pageId, parentId)
            position = min((i for i, child in enumerate(siblings) if child in items), default=len(siblings))
            for item in items:
                _parent, source, old = parentOf(p, pageId, item.componentId)
                source.pop(old)
            group = Component(componentId=groupId, type='container',
                              grid=ResponsiveLayout(direction=direction), children=items)
            siblings.insert(position, group)
        self.session.editPresentation(edit)
        return groupId

    def ungroup(self, pageId, key):
        selected = []
        def edit(p):
            _parent, siblings, index = parentOf(p, pageId, key)
            group = siblings[index]
            if group.type != 'container':
                raise ValueError('请选择一个容器后取消编组')
            siblings[index:index + 1] = group.children
            selected.extend(c.componentId for c in group.children)
        self.session.editPresentation(edit)
        return selected

    def delete(self, pageId, keys):
        def edit(p):
            for item in selectionRoots(p, pageId, keys):
                _parent, siblings, index = parentOf(p, pageId, item.componentId)
                siblings.pop(index)
        self.session.editPresentation(edit)

    def arrange(self, pageId, keys, *, alignment=None, distribute=None):
        def edit(p):
            items = selectionRoots(p, pageId, keys)
            if len(items) < (3 if distribute else 2):
                raise ValueError('等距分布至少选择三个组件' if distribute else '对齐至少选择两个组件')
            parents = {parentOf(p, pageId, item.componentId)[0] for item in items}
            if len(parents) != 1:
                raise ValueError('请先将跨容器的组件编组，再设置组内排列')
            parentId = parents.pop()
            layout = layoutFor(p, pageId, parentId)
            if layout.type != 'responsive':
                raise ValueError('请先在布局属性中选择响应式排列')
            horizontal = layout.direction in ('horizontal', 'wrap')
            if distribute:
                if (distribute == 'horizontal') != horizontal:
                    raise ValueError('请先把所选组件编为对应方向的排列组')
                if len(items) != len(childrenFor(p, pageId, parentId)):
                    raise ValueError('等距分布作用于整个排列组；可先将所选组件编组')
                layout.justify = 'space-between'
                return
            xaxis = alignment in ('left', 'hcenter', 'right')
            value = {'left': 'start', 'hcenter': 'center', 'right': 'end',
                     'top': 'start', 'vcenter': 'center', 'bottom': 'end'}[alignment]
            if xaxis != horizontal:
                for item in items:
                    item.responsive.alignSelf = value
                    if not horizontal:
                        item.responsive.width = 'content'
            else:
                if len(items) != len(childrenFor(p, pageId, parentId)):
                    raise ValueError('此方向作用于整个排列组；可先将所选组件编组')
                layout.justify = value
        self.session.editPresentation(edit)
