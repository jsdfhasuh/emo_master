"""Reproduce the previous immediate-rejection behavior without editing source."""
if __name__ == '__main__':
    import pytest
    from emo_master.apps.runtime.presentation.service import PresentationService

    original = PresentationService.checkNormalAdmission

    def immediate(self, *, retirementTimeoutSeconds=0.0, isActive=lambda: True):
        return original(self, retirementTimeoutSeconds=0.0, isActive=isActive)

    PresentationService.checkNormalAdmission = immediate
    raise SystemExit(pytest.main([
        'tests/runtime/presentation/test_normal_restart.py::testRemoteRestartWaitsForStoppedDisplayRetirement[restart]',
        '--timeout=90', '-q',
        '--junitxml=build/ui-main-integration/normal-restart/immediate-before.xml',
    ]))
