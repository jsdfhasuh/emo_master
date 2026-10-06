"""Explicit, bounded test-project format. No device permission or field release."""
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unicodedata

from emo_master import __version__
from emo_master.core.presentation.models import walkComponents
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.resources import safeRelativePath
from emo_master.core.project.snapshots import canonicalJson, freezeProjectSnapshot, captureDefinition
from emo_master.core.workflow.compiler import WorkflowCompiler


MIB = 1024 * 1024
MAX_PROJECT = MIB
MAX_RESOURCE = 8 * MIB
MAX_TOTAL = 64 * MIB
MAX_ARCHIVE = 72 * MIB
MAX_ENTRIES = 34  # project, manifest, at most 32 declared local images
FORMAT = 'emo-pages-test-1'
OPERATORS = {'vision.io.image_loader': ('image_loader', '1.1.0'),
             'vision.analysis.blob': ('blob_analysis', '1.2.0'),
             'vision.collection.count': ('collection_count', '1.1.0'),
             'vision.io.image_saver': ('image_saver', '1.0.0')}


def packagePath(name):
    safeRelativePath(name)
    if len(name) > 220 or unicodedata.normalize('NFC', name) != name:
        raise ValueError('noncanonical or overlong package path')
    for part in name.split('/'):
        if (part.startswith(' ') or part.endswith((' ', '.')) or any(ord(c) < 32 or c in '<>"|?*~' for c in part)
                or re.fullmatch(r'(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|CLOCK\$|COM[1-9¹²³]|LPT[1-9¹²³])',
                                part.split('.')[0].rstrip(' '), re.I)):
            raise ValueError('Windows path alias or reserved name')
    return name


def readJson(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=pairs)
    canonicalJson(value)  # includes non-finite exponent rejection
    return value


def sha(data):
    return hashlib.sha256(data).hexdigest()


def trustedRegistry():
    # Only application-installed builtins. Never scan an imported project for code.
    from emo_master.core.plugin.registry import PluginRegistry
    root = Path(__file__).resolve().parents[2] / 'plugins' / 'builtins'
    return PluginRegistry(__version__).scan(root).activeOperators


def portableDocument(document):
    result = ProjectDocument.model_validate(document.model_dump())
    if result.resources is None or result.presentation is None:
        raise ValueError('test delivery requires explicit project 2.2')
    # Declared paths are materialized later. Do not archive developer fallbacks.
    for binding in [*result.resources.parameterBindings, *result.resources.siteBindings]:
        target = binding.target
        node = next(n for n in result.workflows[target.workflowId].nodes if n.nodeId == target.nodeId)
        params = node.params
        for part in target.parameterPath[:-1]:
            params = params[part]
        params[target.parameterPath[-1]] = ''
    return result


def validateTestProject(document, root, registry):
    """Same formal release snapshot and compiler as Runtime, without execution."""
    if (document.schemaVersion != '2.2' or document.resources is None
            or document.presentation is None or not document.presentation.pages):
        raise ValueError('nonempty project 2.2 pages required')
    if document.devices.bindings:
        raise ValueError('device configuration is not part of a local test delivery')
    if any(not isinstance(item, str) or item not in OPERATORS for item in document.dependencies.operators):
        raise ValueError('unsupported dependency declaration; packages cannot install plugins')
    plan = document.resources
    if len(plan.items) > MAX_ENTRIES - 2 or sum(i.size for i in plan.items.values()) > MAX_TOTAL - 2 * MIB:
        raise ValueError('resource budget exceeded')
    for item in plan.items.values():
        packagePath(item.path)
        if item.path.casefold() in {'project.json', 'manifest.json'}:
            raise ValueError('resource uses reserved package path')
        if item.purpose != 'input_image' or item.size > MAX_RESOURCE:
            raise ValueError('P5-A supports declared local input images up to 8 MiB only')
        if Path(item.path).suffix.lower() not in {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'}:
            raise ValueError('unsupported local image file type')
    if set(plan.items) != {b.resourceId for b in plan.parameterBindings}:
        raise ValueError('unreferenced resource is not deliverable')
    sites = {}
    for binding in plan.siteBindings:
        if binding.purpose != 'output_file':
            raise ValueError('P5-A supports isolated output_file sites only; no devices/secrets')
        sites[binding.field] = binding.field + '.png'
    usedOperators = {}
    for workflow in document.workflows.values():
        for node in workflow.nodes:
            if node.kind != 'operator':
                continue
            if node.operatorId not in OPERATORS:
                raise ValueError('unsupported test operator: ' + str(node.operatorId))
            module, version = OPERATORS[node.operatorId]
            descriptor = registry.get(node.operatorId)
            if (descriptor is None or descriptor.manifest.version != version or
                    descriptor.operatorClass.__module__ != 'emo_master.plugins.builtins.' + module + '.operator'):
                raise ValueError('trusted builtin version unavailable: ' + node.operatorId)
            usedOperators[node.operatorId] = version
    capture = captureDefinition(document.presentation)
    if len(capture['scopes']) != 1 or len(capture['sources']) > 16:
        raise ValueError('P5-A requires one explicit result scope, at most 16 sources')
    images = 0
    for source in capture['sources']:
        scope = capture['scopes'][source['resultScopeId']]
        if (source['kind'] not in {'node_output', 'workflow_output'} or
                source['workflowId'] != scope['scopeWorkflowId'] or source['callPath'] != scope['callPath']):
            raise ValueError('unsupported source / mixed invocation scope')
        images += source['expectedType'] == 'image'
    if images > 1:
        raise ValueError('P2 single image slot: reuse the same source across pages')
    components = {}
    for page in document.presentation.pages.values():
        for component in walkComponents(page.components):
            components[component.type] = component.version
            if component.type == 'navigation_button' and not component.actions:
                raise ValueError('navigation button requires an explicit action')
    with tempfile.TemporaryDirectory(prefix='emo-package-validate-') as temporary:
        snapshot = freezeProjectSnapshot(document, {k: d.manifest for k, d in registry.items()},
            mode='release', releaseRevision='validation-only', resourceRoot=Path(root),
            siteDataRoot=Path(temporary), siteValues=sites)
        compiled = ProjectDocument.model_validate(document.model_dump())
        params = readJson(snapshot.parametersJson)
        for wid, workflow in compiled.workflows.items():
            for node in workflow.nodes:
                if node.kind == 'operator':
                    node.params = params[wid][node.nodeId]
        WorkflowCompiler(operatorRegistry=registry).compile(compiled)
    return {'application': __version__, 'deliveryFormat': FORMAT, 'projectSchema': '2.2',
            'presentationSchema': '1.0', 'components': components, 'operators': usedOperators}


def manifestFor(document, projectBytes, compatibility):
    files = {'project.json': {'size': len(projectBytes), 'sha256': sha(projectBytes)}}
    files.update({item.path: {'size': item.size, 'sha256': item.sha256}
                  for item in document.resources.items.values()})
    content = {'format': FORMAT, 'purpose': 'development-test-only',
               'projectId': document.project.projectId, 'compatibility': compatibility, 'files': files}
    return dict(content, revision=sha(canonicalJson(content).encode()))
