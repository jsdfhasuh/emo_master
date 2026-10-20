import io
import pytest
from scripts.p0_transfer_diagnostics.observe import ClientCall, ReadHandle, TaggedIterator, ROWS, record
from scripts.p0_transfer_diagnostics.plugin import command


def test_command_keeps_original_scenario_arguments_and_does_not_mutate():
    args = ['python', '-m', 'prototypes.runtime_pages_p0.scenarios', 'continuous', '{}']
    original = list(args)
    actual = command(args)
    assert args == original
    assert actual[:2] == args[:2] and actual[3:] == args[3:]
    assert command(['python', '-m', 'other']) == ['python', '-m', 'other']


def test_reader_preserves_bytes_sizes_and_context_close():
    row = {}
    handle = io.BytesIO(b'abcdef')
    with ReadHandle(handle, row) as wrapped:
        assert wrapped.read(2) == b'ab'
        assert wrapped.read(3) == b'cde'
        assert wrapped.read() == b'f'
        assert wrapped.read(1) == b''
    assert handle.closed and row['read_bytes'] == 6


def test_iterator_preserves_values_exception_and_close():
    closed = []
    def source():
        try:
            yield (b'one', b'two')
            raise TimeoutError('original deadline')
        finally:
            closed.append(True)
    row = {}
    wrapped = TaggedIterator(source(), row)
    assert next(wrapped) == (b'one', b'two')
    with pytest.raises(TimeoutError, match='original deadline'):
        next(wrapped)
    wrapped.close()
    assert closed == [True] and row['iterator_terminal'] == 'TimeoutError'


def test_client_preserves_wire_chunks_errors_and_cancel():
    cancelled = []
    class Call:
        def __iter__(self):
            yield b'a'
            yield b'bc'
            raise TimeoutError('unchanged')
        def cancel(self):
            cancelled.append(True)
            return True
    row = {}
    wrapped = ClientCall(Call(), row)
    assert next(wrapped) == b'a' and next(wrapped) == b'bc'
    with pytest.raises(TimeoutError, match='unchanged'):
        next(wrapped)
    assert wrapped.cancel() is True and cancelled == [True]
    assert row['received_bytes'] == 3


def test_bound_and_payload_free_records():
    ROWS.clear()
    for _ in range(300):
        record('server', 'asset-uuid')
    assert len(ROWS) == 256
    assert set(ROWS[0]) == {'kind', 'asset_id', 'rpc_id', 'start_ns'}
    ROWS.clear()


@pytest.mark.parametrize('message, expected', [('', 'invoke via watchdog.supervised'), ('GO\n', 'ValueError: diagnostic-unknown')])
def test_entry_preserves_go_handshake_and_failure_exit(message, expected, tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root), str(root / 'src'))),
               P0_TRANSFER_DIAG_DIR=str(tmp_path))
    results = [subprocess.run([sys.executable, '-m', module, 'diagnostic-unknown', '{}'],
                             cwd=root, env=env, input=message, capture_output=True, text=True, timeout=15)
               for module in ['prototypes.runtime_pages_p0.scenarios', 'scripts.p0_transfer_diagnostics.observe']]
    assert [result.returncode for result in results] == [1, 1]
    assert all(expected in result.stderr for result in results)


def test_observer_does_not_change_budget():
    from prototypes.runtime_pages_p0.contracts import BUDGET
    assert BUDGET.read_seconds == .5
    assert BUDGET.client_result_seconds == .55


def test_installed_observer_records_actual_path_read(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root), str(root / 'src'))))
    source = tmp_path / 'payload.bin'
    source.write_bytes(b'abc')
    code = '''
import sys
from pathlib import Path
from scripts.p0_transfer_diagnostics.observe import install, TLS
install()
row = {}
TLS.row = row
assert Path(sys.argv[1]).read_bytes() == b'abc'
TLS.row = None
assert row['read_bytes'] == 3 and row['read_max_ns'] >= 0
'''
    result = subprocess.run([sys.executable, '-c', code, str(source)], env=env,
                            cwd=root, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
