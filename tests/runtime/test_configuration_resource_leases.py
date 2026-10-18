import ctypes
import multiprocessing
import os
from pathlib import Path
import sys

import pytest

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.project_config.resource_journal import ResourceJournal
from emo_master.apps.runtime.project_config.resource_lease import ResourceLease, runWithResourceLease


def owner(root, name):
    return ResourceJournal(SqliteStore(root / 'runtime.db'), root / 'configuration-resources', name, consumerLeases=True)


def consume(path, entered, release, results):
    entered.set()
    if not release.wait(15):
        raise RuntimeError('synthetic consumer release timed out')
    results.send_bytes(Path(path).read_bytes())
    results.close()


def parent(root, info, release, results):
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    root = Path(root)
    runtime = RuntimeService(dbPath=root / 'runtime.db', workspaceRoot=root / 'jobs')
    journal = runtime.configurationResourceJournal
    path = journal.root / 'batch' / 'input.bin'
    path.parent.mkdir()
    journal.reserve(path)
    path.write_bytes(b'leased resource')
    journal.expose([path])
    context = multiprocessing.get_context('spawn')
    entered = context.Event()
    child = context.Process(target=runWithResourceLease,
        args=(journal.leasePath, consume, (str(path), entered, release, results)))
    child.start()
    assert entered.wait(10)
    info.send((str(path), child.pid))
    import time
    time.sleep(30)
    child.join(5)
    runtime.close()


@pytest.mark.skipif(sys.platform not in {'win32', 'linux'}, reason='requires process handle or pidfd')
def testParentDeathCannotReclaimLiveChildButChildExitAllowsRecovery(tmp_path):
    context = multiprocessing.get_context('spawn')
    receive, send = context.Pipe(False)
    result, writer = context.Pipe(False)
    release = context.Event()
    process = context.Process(target=parent, args=(str(tmp_path), send, release, writer))
    handle = pidfd = None
    current = None
    try:
        process.start()
        send.close()
        writer.close()
        assert receive.poll(15)
        path, pid = receive.recv()
        path = Path(path)
        if sys.platform == 'win32':
            kernel = ctypes.windll.kernel32
            kernel.OpenProcess.restype = ctypes.c_void_p
            handle = kernel.OpenProcess(0x100000, False, pid)
            assert handle
        else:
            pidfd = os.pidfd_open(pid)
        process.terminate()
        process.join(5)
        assert process.exitcode is not None
        current = owner(tmp_path, 'restart')
        current.recover()
        assert path.exists() and current.retainedAfterCrash() == [str(path)]
        release.set()
        assert result.poll(10) and result.recv_bytes() == b'leased resource'
        if handle:
            assert kernel.WaitForSingleObject(ctypes.c_void_p(handle), 10000) == 0
        else:
            import select
            poller = select.poll()
            poller.register(pidfd, select.POLLIN)
            assert poller.poll(10000)
        current.recover()
        assert not path.exists() and not current.retainedAfterCrash()
    finally:
        release.set()
        if process.is_alive():
            process.terminate()
            process.join(5)
        process.close()
        receive.close()
        result.close()
        if handle:
            kernel.WaitForSingleObject(ctypes.c_void_p(handle), 10000)
            kernel.CloseHandle(ctypes.c_void_p(handle))
        if pidfd is not None:
            os.close(pidfd)
        if current:
            current.retire()


def testRetirementFencesLateConsumerAndCannotBypassExistingLease(tmp_path):
    previous = owner(tmp_path, 'previous')
    lease = ResourceLease(previous.leasePath)
    previous.retire()
    current = owner(tmp_path, 'current')
    try:
        current.recover()
        assert Path(previous.leasePath).exists()
        lease.close()
        current.recover()
        with pytest.raises(OSError):
            ResourceLease(previous.leasePath)
    finally:
        lease.close()
        current.retire()


def testCrashBetweenRetirementCommitAndFenceStillRequiresExclusiveOwnership(tmp_path):
    previous = owner(tmp_path, 'previous')
    path = previous.root / 'batch' / 'input.bin'
    path.parent.mkdir()
    previous.reserve(path)
    path.write_bytes(b'keep until consumer exits')
    previous.expose([path])
    previous.lease.close()
    with previous.sqlite.connection() as connection:
        connection.execute('UPDATE configurationResourceOwners SET retired=1 WHERE instanceId=?', ('previous',))
    late = ResourceLease(previous.leasePath)
    current = owner(tmp_path, 'current')
    try:
        current.recover()
        assert path.exists() and current.retainedAfterCrash() == [str(path)]
        late.close()
        current.recover()
        assert not path.exists() and not Path(previous.leasePath).exists()
    finally:
        late.close()
        current.retire()
