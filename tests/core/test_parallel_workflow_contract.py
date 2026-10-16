import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from emo_master import __version__
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.presentation.workflow_view import presentationForWorkflow
from emo_master.core.project.models import ProjectDocument, RuntimeSettings
from emo_master.apps.runtime.presentation.normal_capture import freezeNormalCapture
from tests.runtime.two_station_fixture import twoStationProject


@pytest.mark.parametrize('limit', [1, 2, 8, 100, None])
def testExplicitConcurrencyConfiguration(limit):
    settings = RuntimeSettings(maxConcurrentJobs=limit)
    assert RuntimeSettings.model_validate_json(settings.model_dump_json()).maxConcurrentJobs == limit


@pytest.mark.parametrize('limit', [0, -1])
def testOldInvalidLimitsDoNotSilentlyBecomeUnlimited(limit):
    with pytest.raises(ValidationError):
        RuntimeSettings(maxConcurrentJobs=limit)


def testScopesDeriveIndependentViewsWithoutAnyStationDeclaration(tmp_path):
    doc = twoStationProject(tmp_path)
    registry = PluginRegistry(__version__).scan(Path(__file__).resolve().parents[2] / 'src/emo_master/plugins').activeOperators
    assert 'stations' not in doc.model_dump()['production']
    assert not validateBindings(doc, {key: item.manifest for key, item in registry.items()})
    saved = deepcopy(doc.model_dump())
    first, second = (freezeNormalCapture(doc, registry, tmp_path, key) for key in ('main', 'second'))
    assert set(json.loads(first.sourceJson)['sources']) == {'count', 'image'}
    assert set(json.loads(second.sourceJson)['sources']) == {'count-second', 'image-second'}
    assert presentationForWorkflow(doc, 'second').pageOrder == ['second']
    assert doc.model_dump() == saved
    # Cross-root sources cannot accidentally appear in a single Job-bound page.
    doc.presentation.pages['second'].components[0].bindings['value'] = 'count'
    assert any(issue.code == 'scope_invalid' for issue in validateBindings(doc, {key: item.manifest for key, item in registry.items()}))
    bad = dict(saved, production=dict(saved['production'], stations={}))
    with pytest.raises(ValidationError):
        ProjectDocument.model_validate(bad)
