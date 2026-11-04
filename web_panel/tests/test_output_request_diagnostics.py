"""Generic quota facts survive ordinary gRPC JSON without SDK/proto changes."""
import json
import unittest

import grpc
from emo_panel_protocol.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_web_panel.runtime_client import GrpcRuntimeClient


class QuotaService(rpc.RuntimeServiceServicer):
    async def GetRuntimeInfo(self, request, context):
        return pb.RuntimeInfoReply(ok=True, runtime_instance_id='runtime-quota', project_id='project-quota', document_revision=7,
            protocol_version='1.0', capabilities=['runtime_info_v1'])

    async def ValidateOutputRequests(self, request, context):
        self.request = request
        return pb.OutputRequestsReply(ok=False, code='INVALID_ARGUMENT', message='工作流合并图片超限',
            state_json=json.dumps({'runtimeInstanceId': 'runtime-quota', 'projectId': 'project-quota',
                'candidateConfigRevision': 'candidate', 'diagnostics': [self.diagnostic]}, ensure_ascii=False))


class OutputRequestDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_json_preserves_addresses_and_origins_over_grpc(self):
        service = QuotaService()
        service.diagnostic = {'code': 'CAPTURE_IMAGES_LIMIT', 'message': 'Qt 与外部共用两路图片额度',
            'workflowId': 'main', 'workflowName': '检测', 'budget': 'images', 'count': 3, 'limit': 2,
            'sourceIds': ['web-mask', 'qt-mask', 'web-overlay'], 'scopeIds': ['web-main', 'qt-main'],
            'imageGroups': [{'sourceIds': ['web-mask', 'qt-mask']}, {'sourceIds': ['web-overlay']},
                            {'sourceIds': ['web-original', 'qt-original']}],
            'sources': [{'sourceId': 'qt-mask', 'origins': ['qt'], 'workflowId': 'main', 'nodeId': 'blob', 'port': 'mask'}]}
        server = grpc.aio.server()
        rpc.add_RuntimeServiceServicer_to_server(service, server)
        port = server.add_insecure_port('127.0.0.1:0')
        await server.start()
        client = GrpcRuntimeClient(f'127.0.0.1:{port}')
        try:
            await client.start()
            result = await client.validate_output_requests({'expectedConfigRevision': 'saved',
                'config': {'schemaVersion': '1.0', 'sources': {}, 'scopes': {}}})
            self.assertFalse(result['ok'])
            self.assertEqual(result['code'], 'INVALID_ARGUMENT')
            self.assertEqual(result['diagnostics'], [service.diagnostic])
            self.assertEqual(service.request.project_id, 'project-quota')
            self.assertEqual(service.request.expected_document_revision, 7)
            self.assertEqual(service.request.expected_config_revision, 'saved')
            self.assertNotIn('componentId', service.request.config_json)
        finally:
            await client.close()
            await server.stop(None)
