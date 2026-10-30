"""Explicit draft-only demonstration, not Panel Contract v1 certification."""
import asyncio
import base64
from datetime import datetime, timezone

IMAGE = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')

def demo_panel():
    comps = [
        {"id":"title","type":"page_title","props":{}},
        {"id":"image","type":"image","binding":{"sourceId":"image"},"props":{}},
        {"id":"score","type":"number","binding":{"sourceId":"score"},"props":{"label":"Score","decimals":2}},
        {"id":"verdict","type":"indicator","binding":{"sourceId":"verdict"},"props":{"label":"Result"}},
        {"id":"status","type":"runtime_status","props":{}},
        {"id":"text","type":"text","props":{"text":"MOCK demonstration — not a connected production Runtime"}},
        {"id":"table","type":"table","binding":{"sourceId":"measurements"},"props":{"columns":[{"key":"name","title":"Name"},{"key":"value","title":"Value"}]}},
        {"id":"clock","type":"clock","props":{}},
        {"id":"nav","type":"page_navigation","props":{}},
        {"id":"freeze","type":"navigation_button","props":{"text":"Freeze","action":"freeze"}},
        {"id":"history-table","type":"sqlite_table","props":{"columns":[{"key":"id","title":"ID"},{"key":"result","title":"Result"}]}},
        {"id":"history-count","type":"sqlite_value","props":{"label":"History count","aggregate":"count"}},
        {"id":"container","type":"container","props":{"title":"Details"},"children":[{"id":"inside","type":"text","props":{"text":"Independent view state"}}]},
    ]
    return {"id":"demo-panel","name":"Demo panel","packageRevision":"draft-1","panelContractVersion":"draft-unverified","pages":[{"id":"main","title":"Demo production","components":comps}]}

class MockRuntime:
    def __init__(self):
        self.running = True
        self.ordinal = 0
        self.lock = asyncio.Lock()
        self.control_count = 0
    async def start(self): pass
    async def close(self): pass
    async def info(self):
        return {"runtimeInstanceId":"mock-instance","projectId":"demo-project","mode":"mock","contractStatus":"draft-unverified","capabilities":["display","history","control"]}
    async def runs(self):
        return [{"id":"demo-run","runId":"demo-run","name":"Demo run","runtimeInstanceId":"mock-instance","projectId":"demo-project","workflowId":"demo-workflow","state":"running" if self.running else "stopped"}]
    async def workflows(self):
        return [{"id":"demo-workflow","name":"Mock workflow","inputs":{},"canStart":True}]
    async def snapshot(self, run_id):
        if run_id != "demo-run": raise ValueError("unknown run")
        if self.running: self.ordinal += 1
        return {"runtimeInstanceId":"mock-instance","jobId":"demo-run","resultScopeId":"scope-1","resultKey":f"mock-{self.ordinal}","resultOrdinal":str(self.ordinal),"executionRevision":"1","capturePlanRevision":"1","status":"complete","executionTerminal":True,"receivedAt":datetime.now(timezone.utc).isoformat(),"sources":{
            "image":{"state":"available","image":{"url":"/api/assets/demo-image","ownerResultKey":f"mock-{self.ordinal}","resourceId":"demo-image","mimeType":"image/png","byteSize":len(IMAGE)}},
            "score":{"state":"available","value":99.5},"verdict":{"state":"available","value":"OK"},
            "measurements":{"state":"available","value":[{"name":"width","value":10.2}]}}}
    async def control(self, action, target, body):
        if body.get("runtimeInstanceId") != "mock-instance" or body.get("projectId") != "demo-project": raise ValueError("stale or wrong control target")
        async with self.lock:
            if action == "start" and body.get("workflowId") != "demo-workflow": raise ValueError("unknown workflow")
            if action == "stop" and target != "demo-run": raise ValueError("unknown run")
            self.running = action == "start"
            self.control_count += 1
            return {"state":"succeeded","runId":"demo-run","mode":"mock"}
    async def history(self, component_id, page, page_size, q="", sort="id", order="asc"):
        if component_id not in {"history-table","history-count"}: raise ValueError("undeclared history component")
        rows = [{"id":str(n),"result":"OK" if n%4 else "NG","imageRef":"demo-image"} for n in range(1,31)]
        if sort not in {"id","result"} or order not in {"asc","desc"} or len(q)>200:raise ValueError("Unsupported history query")
        rows=[r for r in rows if q.lower() in str(r).lower()]
        rows.sort(key=lambda r:int(r["id"]) if sort=="id" else r["result"],reverse=order=="desc")
        return {"rows":rows[(page-1)*page_size:page*page_size],"total":len(rows),"page":page,"pageSize":page_size,"value":len(rows),"mode":"mock"}
