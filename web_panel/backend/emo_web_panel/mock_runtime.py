"""Explicit draft-only demonstration, not Panel Contract v1 certification."""
import asyncio
import base64
import hashlib
import copy
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
        {"id":"space","type":"spacer","props":{}},
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
        self.instance_id="mock-instance" if runtime_id=="configured" else f"mock-instance:{runtime_id}"
        self.events=[]
        self.event_sequence=0
        self.running = True
        self.ordinal = 0
        self.lock = asyncio.Lock()
        self.control_count = 0
        from emo_panel_protocol.canonical import revisionOf
        self.output_config = {"schemaVersion": "1.0", "sources": {}, "scopes": {}}
        self.output_revision = revisionOf(self.output_config)
        self.output_receipts = {}
        self.frozen_output_config = copy.deepcopy(self.output_config)
    async def start(self): pass
    async def close(self): pass
    async def info(self):
        return {"runtimeInstanceId":self.instance_id,"projectId":"demo-project","mode":"mock","configurationRevision":"1","documentRevision":"1","contractStatus":"draft-unverified","capabilities":["display","history","control","runtime_logs_query_v1","output_requests_v1"]}

    async def output_catalog(self):
        from emo_panel_protocol.models import DataSource, ResultScope
        scope = ResultScope(entryWorkflowId="demo-workflow", scopeWorkflowId="demo-workflow").model_dump(mode="json")
        choices = []
        for sid, port, kind, title in (("image", "result_image", "image", "结果图"),
                                        ("count", "count", "integer", "数量"),
                                        ("verdict", "result", "boolean", "OK/NG"),
                                        ("measurements", "detections", "collection", "检测明细")):
            source = DataSource(kind="node_output", resultScopeId="scope-1", workflowId="demo-workflow",
                                nodeId="demo-inference", port=port, expectedType=kind).model_dump(mode="json")
            choices.append({"title": "模拟检测流程 → 推理节点 → " + title, "sourceId": sid,
                            "scopeId": "scope-1", "source": source, "scope": scope,
                            "hint": "示例目录，不连接设备", "available": True, "reason": ""})
        return {**await self.output_requests(), "workflows": await self.workflows(), "choices": choices}

    async def output_requests(self):
        return {"ok": True, "code": "OK", "runtimeInstanceId": self.instance_id,
                "projectId": "demo-project", "documentRevision": "1", "configRevision": self.output_revision,
                "config": copy.deepcopy(self.output_config), "diagnostics": [],
                "capabilities": {"maxSources": 16, "maxScopes": 16, "maxImageSources": 2}}

    async def validate_output_requests(self, body):
        from emo_panel_protocol.models import DataSource, ResultScope
        current = await self.output_requests()
        reason = ""
        if body.get("runtimeInstanceId") != self.instance_id or body.get("projectId") != "demo-project":
            reason = "Runtime 或工程身份已变化"
        elif str(body.get("expectedDocumentRevision")) != "1" or body.get("expectedConfigRevision") != self.output_revision:
            reason = "预期工程或采集配置修订不匹配"
        config = body.get("config", {})
        try:
            if set(config) != {"schemaVersion", "sources", "scopes"} or config["schemaVersion"] != "1.0":
                raise ValueError("Invalid request configuration")
            scopes = {key: ResultScope.model_validate(value) for key, value in config["scopes"].items()}
            sources = {key: DataSource.model_validate(value) for key, value in config["sources"].items()}
            if len(sources) > 16 or len(scopes) > 16 or sum(value.expectedType == "image" for value in sources.values()) > 2:
                raise ValueError("超出采集额度")
            catalog = {item["sourceId"]: item for item in (await self.output_catalog_choices())}
            for sid, value in sources.items():
                if value.resultScopeId not in scopes or sid not in catalog or value.model_dump(mode="json") != catalog[sid]["source"]:
                    raise ValueError("来源不存在、类型或结果作用域不匹配: " + sid)
        except (ValueError, TypeError, KeyError) as error:
            reason = str(error)
        return {**current, "ok": not reason, "code": "INVALID_ARGUMENT" if reason else "OK", "message": reason,
                "diagnostics": [{"message": reason}] if reason else []}

    async def output_catalog_choices(self):
        return (await self.output_catalog())["choices"]

    async def submit_output_requests(self, body):
        from emo_panel_protocol.canonical import revisionOf
        request_id = body.get("requestId")
        if request_id in self.output_receipts:
            return copy.deepcopy(self.output_receipts[request_id])
        checked = await self.validate_output_requests(body)
        if not checked["ok"]:
            return checked
        self.output_config = copy.deepcopy(body["config"])
        self.output_revision = revisionOf(self.output_config)
        reply = {**await self.output_requests(), "state": "SAVED", "requestId": request_id}
        self.output_receipts[request_id] = copy.deepcopy(reply)
        return reply

    async def output_request_status(self, request_id, body=None):
        return copy.deepcopy(self.output_receipts.get(request_id, {"ok": False, "code": "NOT_FOUND", "state": "NOT_FOUND", "message": "原请求结果尚不可确认"}))

    async def source_problems(self, run_id, presentation):
        from emo_panel_protocol.models import DataSource
        # Immutable packages omit optional nulls; compare the source definition,
        # not that serialization difference, just as the real Runtime does.
        return {sid: "SOURCE_NOT_CAPTURED · 当前任务未采集此输出；先发布，再明确启动新任务"
                for sid, source in presentation.get("dataSources", {}).items()
                if sid not in self.frozen_output_config["sources"] or
                DataSource.model_validate(self.frozen_output_config["sources"][sid]) != DataSource.model_validate(source)}
    async def runs(self):
        return [{"id":"demo-run","runId":"demo-run","name":"Demo run","runtimeInstanceId":self.instance_id,"projectId":"demo-project","workflowId":"demo-workflow","configurationRevision":"1","state":"running" if self.running else "stopped"}]
    async def workflows(self):
        return [{"id":"demo-workflow","runtimeInstanceId":self.instance_id,"projectId":"demo-project","name":"Mock workflow","inputs":{},"inputSchema":{"type":"object","properties":{},"additionalProperties":False},"configurationRevision":"1","canStart":True}]
    async def snapshot(self, run_id):
        if run_id != "demo-run": raise ValueError("unknown run")
        if self.running: self.ordinal += 1
        result = {"runtimeInstanceId":self.instance_id,"jobId":"demo-run","resultScopeId":"scope-1","resultKey":f"mock-{self.ordinal}","resultOrdinal":str(self.ordinal),"executionRevision":"1","capturePlanRevision":"1","status":"complete","executionTerminal":True,"runState":"running" if self.running else "stopped","receivedAt":datetime.now(timezone.utc).isoformat(),"sources":{
            "image":{"state":"available","image":{"url":f"/api/assets/runtime/{self.runtime_id}/demo-image","ownerResultKey":f"mock-{self.ordinal}","resourceId":"demo-image","mimeType":"image/png","byteSize":len(IMAGE),"sha256":hashlib.sha256(IMAGE).hexdigest()}},
            "score":{"state":"available","value":99.5},"verdict":{"state":"available","value":"OK"},
            "measurements":{"state":"available","value":[{"name":"width","value":10.2}]}}}
        for sid, source in self.frozen_output_config["sources"].items():
            port = source.get("port")
            value = {"count": 3, "result": True, "detections": [{"name": "模拟目标", "value": 3}]} .get(port)
            result["sources"][sid] = (copy.deepcopy(result["sources"]["image"]) if port == "result_image"
                                      else {"state": "available", "value": value})
        return result
    def validate_control(self, action, target, body):
        if body.get("runtimeInstanceId") != self.instance_id or body.get("projectId") != "demo-project" or body.get('configurationRevision')!='1':raise ValueError('Stale or wrong control target/configuration revision')
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
            if action == "start": self.frozen_output_config = copy.deepcopy(self.output_config)
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

    async def query_runtime_logs(self, query):
        return synthetic_log_page(self.instance_id,'demo-project',query)

    async def task_events(self, after="0"):
        if not isinstance(after,str) or not after.isdigit() or len(after)>20:raise ValueError("Invalid event sequence")
        cursor=int(after)
        return {"events":[event for event in self.events if int(event['sequence'])>cursor],"nextSequence":str(self.event_sequence),"resetRequired":bool(self.events and cursor<int(self.events[0]['sequence'])-1),"source":"mock-runtime-event-log"}


def synthetic_log_page(instance_id, project_id, query):
    reply={'ok':True,'code':'OK','message':'','runtimeInstanceId':instance_id,
           'upperEventId':'2','nextEventId':str(query.get('afterEventId','0')),'hasMore':False,'events':[]}
    if query.get('runtimeInstanceId') and query['runtimeInstanceId']!=instance_id:
        return {**reply,'ok':False,'code':'RESET_REQUIRED','message':'Runtime instance changed'}
    upper=int(query.get('upperEventId',2));after=int(query.get('afterEventId',0))
    rows=[{'eventId':str(n),'sequence':str(n),'timestampMs':'1793664000000','jobId':'demo-run',
           'projectId':project_id if n==2 else 'previous-project','workflowId':'demo-workflow','nodeId':'node-1',
           'eventType':'node.log','originApp':'runtime','component':'operator','runtimeInstanceId':instance_id,
           'level':'INFO','code':'DEMO_LOG','message':'Synthetic Runtime log','payload':{'deviceId':'demo-device'}}
          for n in (1,2)]
    rows=[item for item in rows if after<int(item['eventId'])<=upper
          and all(not query.get(key) or item.get(key)==query[key] for key in ('projectId','jobId','component','workflowId','nodeId'))
          and (not query.get('deviceId') or item['payload']['deviceId']==query['deviceId'])
          and (not query.get('levels') or item['level'] in query['levels'])
          and ('startTimestampMs' not in query or int(item['timestampMs'])>=int(query['startTimestampMs']))
          and ('endTimestampMs' not in query or int(item['timestampMs'])<int(query['endTimestampMs']))]
    page=rows[:query.get('limit',200)]
    return {**reply,'upperEventId':str(upper),'nextEventId':page[-1]['eventId'] if len(rows)>len(page) else str(upper),
            'hasMore':len(rows)>len(page),'events':page}
