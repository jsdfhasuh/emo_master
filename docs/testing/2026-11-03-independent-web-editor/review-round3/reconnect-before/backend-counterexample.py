import json
from pathlib import Path
from copy import deepcopy
from test_editor import EditorTests
ROOT=Path(__file__).parent
fixture=EditorTests()
fixture.setUp()
try:
    initial=fixture.save(fixture.pages(('result_image','count','result')))
    old=fixture.publish(initial['draftRevision'],'reconnect-old-0001')
    start={'requestId':'reconnect-start-0001','configurationRevision':'1','runtimeInstanceId':'mock-instance','projectId':fixture.project,'workflowId':'demo-workflow'}
    started=fixture.client.post('/api/runs/start',headers=fixture.headers,json=start)
    assert started.status_code==200,started.text
    runs=fixture.client.get('/api/runtimes/configured/runs').json()
    job_id=runs[0]['id']
    body={'runtimeId':'configured','projectId':fixture.project,'panelId':old['packageId'],'runId':job_id}
    created=fixture.client.post('/api/views',headers=fixture.headers,json=body)
    assert created.status_code==200,created.text
    vid=created.json()['id']
    frozen=deepcopy(fixture.app.state.runtime.frozen_output_config)
    next_source=fixture.pages()
    updated=fixture.save(next_source)
    new=fixture.publish(updated['draftRevision'],'reconnect-new-0001')
    params={'runtimeId':'configured','projectId':fixture.project}
    old_panel=fixture.client.get('/api/panels/'+old['packageId'],params=params)
    old_view=fixture.client.post('/api/views',headers=fixture.headers,json=body)
    old_package=fixture.client.get(fixture.base+'/versions/'+old['versionId']+'/package',params={'projectId':fixture.project})
    retained=fixture.app.state.views.views[vid]
    current_draft=fixture.client.get(fixture.base+'/context').json()['draft']['presentation']
    report={'status':'CONFIRMED','layer':'current FastAPI/EditorStore + synthetic MockRuntime; no physical device','oldPackageId':old['packageId'],'newPackageId':new['packageId'],'oldPanelReadStatus':old_panel.status_code,'oldPanelReadDetail':old_panel.json(),'newOldPackageViewStatus':old_view.status_code,'oldVersionPackageDownloadStatus':old_package.status_code,'oldVersionPackageBytes':len(old_package.content),'retainedViewPanelId':retained['panelId'],'retainedViewPackageRevision':retained['presentation']['packageRevision'],'currentJob':job_id,'controlCount':fixture.app.state.runtime.control_count,'frozenCaptureUnchanged':fixture.app.state.runtime.frozen_output_config==frozen,'frozenSources':list(frozen['sources']),'nextJobSources':list(fixture.app.state.runtime.output_config['sources'])}
    assert old_panel.status_code==404 and old_view.status_code==404
    assert old_package.status_code==200 and retained['panelId']==old['packageId']
    assert report['frozenCaptureUnchanged'] and report['controlCount']==1
    (ROOT/'backend-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
finally:fixture.tearDown()

