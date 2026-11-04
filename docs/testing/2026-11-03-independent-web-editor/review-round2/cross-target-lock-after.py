import asyncio,json,tempfile
from pathlib import Path
from httpx import AsyncClient,ASGITransport
from emo_web_panel.app import create_app
from emo_web_panel.config import Config
from emo_web_panel.mock_runtime import MockRuntime

class SlowSubmit(MockRuntime):
    def __init__(self):
        super().__init__();self.entered=asyncio.Event();self.resume=asyncio.Event()
    async def submit_output_requests(self,body):
        self.entered.set();await self.resume.wait()
        return await super().submit_output_requests(body)

async def run(cancel=False):
    with tempfile.TemporaryDirectory() as directory:
        runtime=SlowSubmit();app=create_app(Config(Path(directory)),runtime)
        app.state.auth.create_user('admin','safe-test-password','admin')
        async with app.router.lifespan_context(app),AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            login=await client.post('/api/session',json={'username':'admin','password':'safe-test-password'})
            headers={'X-CSRF-Token':login.json()['csrf']}
            added=await client.post('/api/admin/runtimes',headers=headers,json={'id':'other','alias':'Other target','mode':'mock','address':''})
            assert added.status_code==200,added.text
            other=(await client.get('/api/runtimes/other/info')).json()
            context=(await client.get('/api/editor/configured/context')).json()
            source=context['draft']['presentation'];choice=next(c for c in context['catalog']['choices'] if c['source']['port']=='count')
            source['dataSources'][choice['sourceId']]=choice['source'];source['resultScopes'][choice['scopeId']]=choice['scope']
            source['pages'][source['defaultPageId']]['components']=[{'componentId':'count','type':'number','bindings':{'value':choice['sourceId']}}]
            project=context['runtime']['projectId']
            saved=await client.put('/api/editor/configured/draft',headers=headers,json={'projectId':project,'presentation':source})
            assert saved.status_code==200,saved.text
            publish=asyncio.create_task(client.post('/api/editor/configured/publish',headers=headers,json={'projectId':project,'expectedDraftRevision':saved.json()['draftRevision'],'requestId':'cross-target-publish'}))
            await asyncio.wait_for(runtime.entered.wait(),5)
            stopping=asyncio.create_task(client.post('/api/runs/demo-run/stop',headers=headers,json={'runtimeId':'other','runtimeInstanceId':other['runtimeInstanceId'],'projectId':other['projectId'],'configurationRevision':'1','runGroupId':'','requestId':'stop-other-during-publish'}))
            await asyncio.wait([stopping],timeout=.3)
            stopped_during_wait=stopping.done()
            state_during=(await client.get('/api/runtimes/other/runs')).json()[0]['state']
            if cancel:
                publish.cancel()
                try:await publish
                except asyncio.CancelledError:pass
                pub_state='CANCELLED_HTTP_TASK'
            else:
                runtime.resume.set();response=await asyncio.wait_for(publish,5);pub_state=response.json()['state']
            stopped=await asyncio.wait_for(stopping,5)
            final=(await client.get('/api/runtimes/other/runs')).json()[0]['state']
            return {'cancelPublicationTask':cancel,'otherStopCompletedWhilePublishPending':stopped_during_wait,'otherStateWhilePublishPending':state_during,'publishState':pub_state,'stopStatus':stopped.status_code,'otherFinalState':final,'realNetworkRequests':0}

async def main():
    result=[await run(),await run(True)]
    path=Path(__file__).with_suffix('.json');path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result));print('EVIDENCE='+str(path))
asyncio.run(main())
