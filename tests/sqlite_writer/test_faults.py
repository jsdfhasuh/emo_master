"""Real spawn/SQLite faults with an outer process watchdog and final cleanup."""
import json
import multiprocessing
from pathlib import Path
import sqlite3
import time

import pytest


def faultProbe(root: str, mode: str, pipe):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.sqlite_writer.test_dependencies import project
    from tests.sqlite_writer.test_backend import makeDb
    from tests.runtime.runtime_test_utils import waitForTerminal
    directory = Path(root)
    business = makeDb(directory / 'business.sqlite3')
    payload = project()
    payload['workflows']['main']['nodes'][0]['params'].update(databasePath=str(business), failurePolicy='continue')
    (directory / 'project.json').write_text(json.dumps(payload), encoding='utf-8')
    if mode != 'lock':
        with sqlite3.connect(business) as connection:
            connection.execute('CREATE TRIGGER expensive BEFORE INSERT ON records BEGIN '
                'SELECT sum(x) FROM (WITH RECURSIVE nums(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM nums WHERE x<1000000000) SELECT x FROM nums); END')
    service = RuntimeService(dbPath=directory / 'runtime.db', workspaceRoot=directory / 'jobs')
    locker = None
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=root), None).ok
        if mode == 'lock':
            locker = sqlite3.connect(business)
            locker.execute('BEGIN IMMEDIATE')
        jobs = []
        for value in (['A', 'B'] if mode == 'lock' else ['A']):
            reply = service.StartJob(pb.StartJobRequest(project_id='sqlite-test', inputs_json=json.dumps({'value': value})), None)
            assert reply.ok, reply.message
            jobs.append(reply.job_id)
        deadline = time.monotonic() + 6
        while not any(e.eventType == 'sqlite.write.started' for e in service.eventStore.readMerged(jobs[0])):
            assert time.monotonic() < deadline
            time.sleep(.01)
        if mode in {'cancel', 'force'}:
            service.StopJob(pb.StopJobRequest(job_id=jobs[0], mode='force' if mode == 'force' else 'graceful'), None)
        records = [waitForTerminal(service, job, timeoutSeconds=9) for job in jobs]
        receipts = []
        for job in jobs:
            events = service.eventStore.readMerged(job)
            for event in events:
                receipt = json.loads(event.payloadJson).get('receipt') if event.eventType == 'sqlite.write.finished' else json.loads(event.payloadJson).get('diagnostics', {}).get('sqliteReceipt')
                if receipt:
                    receipts.append(receipt)
            if mode == 'force':
                unknown = next(e for e in events if e.code == 'E_SQLITE_UNKNOWN')
                terminal = next(e for e in events if e.eventType == 'job.aborted')
                assert unknown.sequence < terminal.sequence
        with sqlite3.connect(business) as connection:
            count = connection.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        if mode == 'timeout':
            assert records[0].status == 'COMPLETED'
            assert any(r['status'] == 'FAILED' and r['error']['code'] == 'E_SQLITE_TIMEOUT' for r in receipts)
            assert count == 0
        elif mode == 'cancel':
            assert records[0].status == 'ABORTED'
            assert count == 0
        elif mode == 'force':
            assert records[0].status == 'ABORTED'
            assert any(r['status'] == 'UNKNOWN' for r in receipts)
        else:
            assert all(r.status == 'COMPLETED' for r in records)
            assert all(r['status'] == 'FAILED' and r['error']['code'] == 'E_SQLITE_LOCKED' for r in receipts)
            assert count == 0
        pipe.send({'mode': mode, 'status': [r.status for r in records], 'receipts': receipts, 'count': count})
    finally:
        if locker is not None:
            locker.rollback()
            locker.close()
        service.close()
        assert not service.sqliteOutcomes.pending
        assert not multiprocessing.active_children()
        pipe.close()


@pytest.mark.parametrize('mode', ['timeout', 'cancel', 'force', 'lock'])
def testSupervisedRealSpawnTimeoutCancelForceAndTwoJobContention(tmp_path, mode):
    context = multiprocessing.get_context('spawn')
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=faultProbe, args=(str(tmp_path), mode, send))
    process.start()
    send.close()
    try:
        assert receive.poll(25), 'outer watchdog: no result, real work must be cleaned up'
        result = receive.recv()
        process.join(5)
        assert not process.is_alive() and process.exitcode == 0
        assert result['mode'] == mode
    finally:
        if process.is_alive():
            process.terminate()
            process.join(3)
        if process.is_alive():
            process.kill()
            process.join(3)
        receive.close()
        process.close()
