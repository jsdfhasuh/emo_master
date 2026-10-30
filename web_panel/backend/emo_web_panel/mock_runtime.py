"""Explicit draft-only demonstration, not Panel Contract v1 certification."""
import asyncio
import base64
import hashlib
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
    def __init__(self, runtime_id="configured"):
        self.runtime_id=runtime_id
        self.events=[]
        self.event_sequence=0
        self.running = True
        self.ordinal = 0
        self.lock = asyncio.Lock()
        self.control_count = 0
    async def start(self): pass
    async def close(self): pass
    async def info(self):
        return {"runtimeInstanceId":"mock-instance","projectId":"demo-project","mode":"mock","configurationRevision":"1","contractStatus":"draft-unverified","capabilities":["display","history","control"]}
    async def runs(self):
        return [{"id":"demo-run","runId":"demo-run","name":"Demo run","runtimeInstanceId":"mock-instance","projectId":"demo-project","workflowId":"demo-workflow","configurationRevision":"1","state":"running" if self.running else "stopped"}]
    async def workflows(self):
        return [{"id":"demo-workflow","name":"Mock workflow","inputs":{},"inputSchema":{"type":"object","properties":{},"additionalProperties":False},"configurationRevision":"1","canStart":True}]
    async def snapshot(self, run_id):
        if run_id != "demo-run": raise ValueError("unknown run")
        if self.running: self.ordinal += 1
        return {"runtimeInstanceId":"mock-instance","jobId":"demo-run","resultScopeId":"scope-1","resultKey":f"mock-{self.ordinal}","resultOrdinal":str(self.ordinal),"executionRevision":"1","capturePlanRevision":"1","status":"complete","executionTerminal":True,"runState":"running" if self.running else "stopped","receivedAt":datetime.now(timezone.utc).isoformat(),"sources":{
            "image":{"state":"available","image":{"url":f"/api/assets/runtime/{self.runtime_id}/demo-image","ownerResultKey":f"mock-{self.ordinal}","resourceId":"demo-image","mimeType":"image/png","byteSize":len(IMAGE),"sha256":hashlib.sha256(IMAGE).hexdigest()}},
            "score":{"state":"available","value":99.5},"verdict":{"state":"available","value":"OK"},
            "measurements":{"state":"available","value":[{"name":"width","value":10.2}]}}}
    def validate_control(self, action, target, body):
        if body.get("runtimeInstanceId") != "mock-instance" or body.get("projectId") != "demo-project" or body.get('configurationRevision')!='1':raise ValueError('Stale or wrong control target/configuration revision')
        inputs=body.get('inputs',{})
        if not isinstance(inputs,dict) or inputs:raise ValueError('Mock workflow declares no inputs; expected empty object')
        if action=='start' and body.get('workflowId')!='demo-workflow':raise ValueError('Unknown workflow')
        if action=='stop' and target!='demo-run':raise ValueError('Unknown run')
    async def control(self, action, target, body):
        self.validate_control(action,target,body)
        async with self.lock:
            if action == "start" and body.get("workflowId") != "demo-workflow": raise ValueError("unknown workflow")
            if action == "stop" and target != "demo-run": raise ValueError("unknown run")
            self.running = action == "start"
            self.control_count += 1
            self.event_sequence += 1
            self.events.append({"sequence":str(self.event_sequence),"at":datetime.now(timezone.utc).isoformat(),"kind":action,"runId":"demo-run","message":"Mock Runtime accepted "+action})
            self.events=self.events[-100:]
            return {"state":"succeeded","runId":"demo-run","mode":"mock"}
    async def history(self, component_id, page, page_size, q="", sort="id", order="asc", aggregation="count", field="measurement", follow_filter=False):
        if component_id not in {"history-table","history-count"}: raise ValueError("undeclared history component")
        rows = [{"id":str(n),"result":"OK" if n%4 else "NG","imageRef":f"{self.runtime_id}:demo-image","measurement":float(n)} for n in range(1,31)]
        if sort not in {"id","result"} or order not in {"asc","desc"} or len(q)>200:raise ValueError("Unsupported history query")
        all_rows=rows
        rows=[r for r in rows if q.lower() in str(r).lower()]
        rows.sort(key=lambda r:int(r["id"]) if sort=="id" else r["result"],reverse=order=="desc")
        if aggregation not in {'count','sum','avg','min','max'} or field!='measurement':raise ValueError('Unsupported declared aggregate')
        values=[r[field] for r in (rows if follow_filter else all_rows)]
        aggregate=len(values) if aggregation=='count' else (None if not values else {'sum':lambda:sum(values),'avg':lambda:sum(values)/len(values),'min':lambda:min(values),'max':lambda:max(values)}[aggregation]())
        return {"rows":rows[(page-1)*page_size:page*page_size],"total":len(rows),"page":page,"pageSize":page_size,"value":aggregate,"mode":"mock","aggregation":aggregation,"followsFilter":follow_filter}

    async def counters(self):
        return {"source":"mock-runtime-authoritative", "total":str(self.ordinal), "ok":str(self.ordinal-self.ordinal//4), "ng":str(self.ordinal//4), "at":datetime.now(timezone.utc).isoformat()}

    async def task_events(self, after="0"):
        if not isinstance(after,str) or not after.isdigit() or len(after)>20:raise ValueError("Invalid event sequence")
        cursor=int(after)
        return {"events":[event for event in self.events if int(event['sequence'])>cursor],"nextSequence":str(self.event_sequence),"resetRequired":bool(self.events and cursor<int(self.events[0]['sequence'])-1),"source":"mock-runtime-event-log"}
