"""Fail-closed boundary for explicitly enabled local passwordless access.

The supported CLI disables proxy header processing. Do not expose this service
through a reverse proxy or tunnel: a local intermediary can impersonate a local
client, which cannot be distinguished from a direct connection at this layer.
"""
import ipaddress

from starlette.responses import JSONResponse


class LocalAccessGuard:
    def __init__(self, app, config):
        self.app = app
        self.config = config

    def allowed(self, scope):
        peer = scope.get('client')
        try:
            if not peer or not ipaddress.ip_address(peer[0]).is_loopback:
                return False
        except ValueError:
            return False
        headers = {}
        for key, value in scope.get('headers', []):
            key = key.lower()
            if key in {b'forwarded', b'x-real-ip', b'via'} or key.startswith(b'x-forwarded-'):
                return False
            if key in {b'host', b'origin', b'sec-fetch-site'} and key in headers:
                return False
            headers[key] = value
        authority = '[::1]' if self.config.host == '::1' else self.config.host
        scheme = {'ws': 'http', 'wss': 'https'}.get(scope.get('scheme'), scope.get('scheme'))
        if scheme not in {'http', 'https'}:
            return False
        hosts = {f'{host}:{self.config.port}'.encode() for host in (authority, 'localhost')}
        if self.config.port == (443 if scheme == 'https' else 80):
            hosts.update(host.encode() for host in (authority, 'localhost'))
        host, origin = headers.get(b'host'), headers.get(b'origin')
        if host not in hosts or headers.get(b'sec-fetch-site') in {b'cross-site', b'same-site'}:
            return False
        if origin is not None and origin != scheme.encode() + b'://' + host:
            return False
        if (scope['type'] == 'websocket' or scope.get('method') not in {'GET', 'HEAD', 'OPTIONS'}) and origin is None:
            return False
        return True

    async def __call__(self, scope, receive, send):
        if scope['type'] in {'http', 'websocket'} and not self.allowed(scope):
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            else:
                await JSONResponse({'detail': 'Local passwordless access requires a direct same-origin loopback connection'},
                                   status_code=403, headers={'Cache-Control': 'no-store'})(scope, receive, send)
            return
        await self.app(scope, receive, send)
