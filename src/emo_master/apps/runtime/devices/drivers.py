"""Explicit built-in import edges for frozen Runtime dependency discovery."""
import importlib

from .imv_host import ManagedImv
from .simulated import SimulatedCamera
from .slmp import ManagedSlmp


BUILTINS = {f"{driver.__module__}:{driver.__name__}": driver
            for driver in (ManagedImv, SimulatedCamera, ManagedSlmp)}


def resolveDriver(entry):
    if entry in BUILTINS:
        return BUILTINS[entry]
    module, name = entry.split(":")
    return getattr(importlib.import_module(module), name)
