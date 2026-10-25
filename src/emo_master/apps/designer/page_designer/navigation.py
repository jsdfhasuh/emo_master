"""Page projection using the same fitted tabs and searchable picker as flows."""
from emo_master.apps.designer.ui.workflow_navigation import WorkflowTabs


class PageTabs(WorkflowTabs):
    def __init__(self):
        super().__init__(noun='页面')
        self.setObjectName('workflowTabs')
        self.setAccessibleName('页面导航')

    def syncPages(self, presentation, activeId):
        self.syncWorkflows([(key, presentation.pages[key].name) for key in presentation.pageOrder], activeId)
        for index, key in enumerate(presentation.pageOrder):
            title = presentation.pages[key].name
            role = '首页' if key == presentation.defaultPageId else ''
            self.setEntryMarker(index, role, f'{title}\n页面 ID：{key}' + ('\n首页' if role else ''))

    def selectPage(self, pageId):
        self.selectWorkflow(pageId)

    def pageId(self, index):
        return self.tabBar().tabData(index)
