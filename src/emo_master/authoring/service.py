"""One business service shared by HTTP and MCP adapters."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

from .catalog import RuntimeCatalog
from .commands import command_schema
from .drafts import DraftService
from .errors import AuthoringError
from .files import FilesService, purpose_values


def object_schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required),
            "additionalProperties": False}


STRING = {"type": "string", "minLength": 1}
DRAFT = {"draftId": STRING}
REVISION = {**DRAFT, "expectedDraftRevision": STRING}
TARGET = {"targetId": STRING}


class AuthoringService:
    def __init__(self, data_dir: Path, targets=None, *, timeout=30, max_transfer_bytes=256 * 1024 * 1024,
                 catalog=None):
        self.catalog = catalog if catalog is not None else RuntimeCatalog(targets or {}, timeout=timeout)
        self.drafts = DraftService(Path(data_dir), self.catalog)
        self.files = FilesService(self.drafts, max_transfer_bytes=max_transfer_bytes)
        self.max_transfer_bytes = max_transfer_bytes
        self._prechecks: set[asyncio.Task] = set()

    def tool_definitions(self):
        specs = [
            ("get_authoring_capabilities", "Discover actual editing commands, targets and file transfer contracts.", object_schema()),
            ("list_operators", "Search a target's real operator summaries; discovery does not execute operators.",
             object_schema({**TARGET, "query": {"type": "string"}, "category": {"type": "string"}}, TARGET)),
            ("get_operator", "Read full parameter schemas, port specifications and provenance from a target.",
             object_schema({**TARGET, "operatorId": STRING}, ["targetId", "operatorId"])),
            ("list_device_types", "Read actual device type declarations without scanning or connecting devices.",
             object_schema(TARGET, TARGET)),
            ("get_device_type", "Read a target device type's complete configuration declaration.",
             object_schema({**TARGET, "typeId": STRING}, ["targetId", "typeId"])),
            ("create_project_draft", "Create an independent complete official project draft.",
             object_schema({"name": STRING})),
            ("list_project_drafts", "List independent drafts, including their current revision.", object_schema()),
            ("get_project_draft", "Read a detached project snapshot; reconnect and read before retrying uncertain edits.",
             object_schema(DRAFT, DRAFT)),
            ("import_project_draft", "Import a small official project JSON as a new draft; file/ZIP bytes use HTTP imports.",
             object_schema({"project": {"type": "object", "required": ["schemaVersion", "project", "workflows"]}}, ["project"])),
            ("apply_project_command", "Apply one semantic command atomically using the current draft revision.",
             object_schema({**REVISION, "command": command_schema()}, [*REVISION, "command"])),
            ("undo_project_edit", "Undo one semantic edit in this draft only.", object_schema(REVISION, REVISION)),
            ("redo_project_edit", "Redo one semantic edit in this draft only.", object_schema(REVISION, REVISION)),
            ("save_project_draft", "Save through the official project save path; incomplete legal drafts may be saved.",
             object_schema(REVISION, REVISION)),
            ("adopt_project_declarations", "Restore an existing draft's declarations from an explicit target at saved exact versions; does not change project content.",
             object_schema({**REVISION, **TARGET}, [*REVISION, *TARGET])),
            ("get_output_choices", "Discover bindable outputs from this draft, not the online project.", object_schema(DRAFT, DRAFT)),
            ("get_project_presentation", "Read only the detached presentation snapshot without rebuilding every workflow.", object_schema(DRAFT, DRAFT)),
            ("validate_project_draft", "Perform local, offline-capable validation; does not compile on the target or execute.",
             object_schema(REVISION, REVISION)),
            ("precheck_project_draft", "Compile an independent candidate on a specified target; never publish or execute it.",
             object_schema({**REVISION, **TARGET}, [*REVISION, *TARGET])),
            ("export_project_draft", "Freeze a project-file or complete project-zip and return authenticated download metadata.",
             object_schema({**REVISION, "format": {"type": "string", "enum": ["project-file", "project-zip"]}},
                           [*REVISION, "format"])),
        ]
        readers = {"get_authoring_capabilities", "list_operators", "get_operator", "list_device_types",
                   "get_device_type", "list_project_drafts", "get_project_draft", "get_output_choices",
                   "get_project_presentation", "validate_project_draft", "precheck_project_draft"}
        definitions = [{"name": name, "description": description, "inputSchema": schema,
                 "annotations": {"readOnlyHint": name in readers,
                                 "destructiveHint": False, "openWorldHint": name in {
                                     "list_operators", "get_operator", "list_device_types", "get_device_type",
                                     "precheck_project_draft", "adopt_project_declarations"}}}
                for name, description, schema in specs]
        for definition in definitions:
            if definition["name"] == "apply_project_command":
                definition["inputSchema"]["$defs"] = deepcopy(
                    definition["inputSchema"]["properties"]["command"].get("$defs", {}))
        return definitions

    def capabilities(self):
        return {"targets": [{"targetId": key, "address": profile.target}
                            for key, profile in self.catalog.targets.items()],
                "commands": deepcopy(command_schema()), "tools": self.tool_definitions(),
                "unsupportedOperations": ["designer.control", "roi.graphical_edit", "production.publish",
                                           "production.start", "operator.execute"],
                "readiness": "Separate declarations, implemented editing commands and revision-bound check reports.",
                "fileTransfer": {
                    "resourceUpload": {"available": True, "method": "POST",
                        "pathTemplate": "/api/project-drafts/{draftId}/resources",
                        "bodyEncoding": "raw-bytes", "contentType": "application/octet-stream",
                        "queryFields": {"name": {"required": True}, "purpose": {"required": True},
                                        "resourceId": {"required": False, "description": "Replace an existing resource"}},
                        "purposeValues": purpose_values(), "revisionHeader": "X-Expected-Draft-Revision",
                        "lengthHeader": "Content-Length", "checksumHeader": "X-Content-SHA256",
                        "maxSizeBytes": self.max_transfer_bytes,
                        "responseFields": ["draftId", "draftRevision", "resourceId", "name", "purpose", "sizeBytes", "sha256"]},
                    "import": {"method": "POST", "path": "/api/project-drafts/imports",
                               "bodyEncoding": "raw-bytes", "filenameQuery": "filename"},
                    "export": {"formats": ["project-file", "project-zip"],
                               "method": "POST", "pathTemplate": "/api/project-drafts/{draftId}/exports",
                               "downloadAuthentication": "workspace-bearer", "downloadMethod": "GET",
                               "downloadPathTemplate": "/api/project-exports/{exportId}/content"},
                    "clientAssistance": "MCP client + HTTP helper reads local files and transfers bytes with the same Bearer credential."}}

    async def invoke(self, name, arguments):
        definitions = {tool["name"]: tool for tool in self.tool_definitions()}
        if name not in definitions:
            raise AuthoringError("E_TOOL", "Unknown authoring tool", status=404)
        # The same input contract is enforced for HTTP and MCP.
        import jsonschema
        try:
            jsonschema.validate(arguments, definitions[name]["inputSchema"])
        except jsonschema.ValidationError as error:
            raise AuthoringError("E_INPUT", error.message, fieldPath=".".join(map(str, error.absolute_path))) from error
        a = arguments
        draft_id = a.get("draftId")
        revision = a.get("expectedDraftRevision")
        if name == "precheck_project_draft":
            async def precheck():
                async with self.files.async_snapshot_zip(draft_id, revision) as (path, metadata):
                    report = await self.catalog.precheck(
                        a["targetId"], path, metadata["draftRevision"],
                        size_bytes=metadata["sizeBytes"], sha256=metadata["sha256"])
                return dict(report, draftId=draft_id, targetId=a["targetId"])

            # The request may finish cancelling before its preparation/read
            # worker exits. This operation keeps owning its files until then.
            operation = asyncio.create_task(precheck())
            self._prechecks.add(operation)
            operation.add_done_callback(self._prechecks.discard)
            try:
                return await asyncio.shield(operation)
            except asyncio.CancelledError:
                operation.cancel()
                raise
        operations = {
            "get_authoring_capabilities": lambda: self.capabilities(),
            "list_operators": lambda: self.catalog.list_operators(a["targetId"], a.get("query"), a.get("category")),
            "get_operator": lambda: self.catalog.get_operator(a["targetId"], a["operatorId"]),
            "list_device_types": lambda: self.catalog.list_device_types(a["targetId"]),
            "get_device_type": lambda: self.catalog.get_device_type(a["targetId"], a["typeId"]),
            "create_project_draft": lambda: self.drafts.create(a.get("name", "project")),
            "list_project_drafts": lambda: {"drafts": self.drafts.list()},
            "get_project_draft": lambda: self.drafts.read(draft_id),
            "import_project_draft": lambda: self.drafts.create(payload=a["project"]),
            "apply_project_command": lambda: self.drafts.edit(draft_id, revision, a["command"]),
            "undo_project_edit": lambda: self.drafts.undo(draft_id, revision),
            "redo_project_edit": lambda: self.drafts.redo(draft_id, revision),
            "save_project_draft": lambda: self.drafts.save(draft_id, revision),
            "adopt_project_declarations": lambda: self.drafts.adopt_declarations(draft_id, revision, a["targetId"]),
            "get_output_choices": lambda: self.drafts.output_choices(draft_id),
            "get_project_presentation": lambda: self.drafts.read_presentation(draft_id),
            "validate_project_draft": lambda: self.drafts.validate(draft_id, revision),
            "export_project_draft": lambda: self.files.export(draft_id, revision, a["format"]),
        }
        return await asyncio.to_thread(operations[name])
