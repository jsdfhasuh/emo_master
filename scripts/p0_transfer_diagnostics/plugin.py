"""Run the original test and supervisor, replacing only the child module entry."""
import subprocess

_NATIVE = subprocess.Popen


def command(args):
    if (isinstance(args, list) and len(args) >= 4 and
            args[1:3] == ['-m', 'prototypes.runtime_pages_p0.scenarios'] and args[3] == 'continuous'):
        return args[:2] + ['scripts.p0_transfer_diagnostics.observe'] + args[3:]
    return args


def pytest_configure(config):
    def popen(args, *rest, **kwargs):
        return _NATIVE(command(args), *rest, **kwargs)
    subprocess.Popen = popen


def pytest_unconfigure(config):
    subprocess.Popen = _NATIVE
