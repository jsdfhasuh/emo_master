"""Independent entry point and Designer read-only preview handoff."""
import argparse
import getpass
import json
import logging
import os
import secrets
import socket
import sqlite3
import stat
import tempfile
from pathlib import Path

from .auth import AuthStore
from .config import Config
from .local_log import GatewayLog


def read_password_file(path: Path) -> str:
    """Read one private UTF-8 password without changing or disclosing the file."""
    # 1024 Unicode characters, an optional UTF-8 BOM and one CRLF terminator.
    limit = 4 * 1024 + 3 + 2
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('password file must be a regular file, not a symbolic link')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        flags |= getattr(os, 'O_BINARY', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('password file must be a regular file')
            if os.name == 'posix':
                if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise ValueError('password file must be owned by the current user with no group/other permissions (use 0600 or 0400)')
            if info.st_size > limit:
                raise ValueError('password file is too large')
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('password file is too large')
        password = raw.decode('utf-8-sig')
    except UnicodeError:
        raise ValueError('password file must contain UTF-8 text') from None
    except OSError:
        # OS and decoder errors can carry file details; never print their payload.
        raise ValueError('cannot read password file; check its path and access permissions') from None
    if password.endswith('\r\n'):
        password = password[:-2]
    elif password.endswith('\n'):
        password = password[:-1]
    if any(character in password for character in ('\r', '\n', '\x00')):
        raise ValueError('password file must contain a single line without NUL characters')
    if not 12 <= len(password) <= 1024:
        raise ValueError('password must be 12..1024 characters')
    return password


def validate_preview_context(path, manifest):
    from emo_panel_protocol.preview import PreviewContext
    if path.stat().st_size > 65536:
        raise ValueError('Preview context too large')
    context = PreviewContext.model_validate_json(path.read_bytes())
    for field in ('projectId', 'packageRevision', 'historyDefinitionRevision'):
        if getattr(context, field) != manifest.get(field):
            raise ValueError('Preview context does not match package: ' + field)
    return context


def main():
    parser = argparse.ArgumentParser(description='Independent Web operator panel')
    parser.add_argument('--data-dir', type=Path, default=Path('.panel-data'))
    sub = parser.add_subparsers(dest='command', required=True)
    user = sub.add_parser('create-user')
    user.add_argument('username')
    user.add_argument('--role', choices=['admin', 'operator', 'viewer'], default='admin')
    user.add_argument('--password-file', type=Path,
                      help='Read a password from a private UTF-8 file instead of prompting; file is not deleted')
    serve = sub.add_parser('serve')
    serve.add_argument('--mode', choices=['mock', 'grpc'], required=True)
    serve.add_argument('--runtime-address')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8780)
    serve.add_argument('--allowed-origin', action='append', default=[])
    serve.add_argument('--secure-cookie', action='store_true')
    preview = sub.add_parser('preview')
    preview.add_argument('--package', type=Path, required=True)
    preview.add_argument('--context', type=Path, required=True)
    preview.add_argument('--host', choices=['127.0.0.1', 'localhost', '::1'], default='127.0.0.1')
    preview.add_argument('--port', type=int, default=0)
    preview.add_argument('--json', action='store_true', help='Machine-readable ready line (always enabled for preview)')
    preview.add_argument('--no-control', action='store_true', help='Read-only preview (always enforced)')
    args = parser.parse_args()
    if args.command == 'create-user':
        try:
            if args.password_file is not None:
                password = read_password_file(args.password_file)
            else:
                password = getpass.getpass('Password (12+ characters): ')
                if password != getpass.getpass('Confirm password: '):
                    parser.error('passwords differ')
            AuthStore(args.data_dir / 'web.sqlite').create_user(args.username, password, args.role)
        except ValueError as error:
            parser.error(str(error))
        except sqlite3.IntegrityError:
            parser.error('user already exists; create-user does not reset existing passwords')
        print('User created. No default password or account is installed.')
        if args.password_file is not None:
            print('Password file was not deleted. Remove it when no longer needed.')
        return
    import uvicorn
    from .app import create_app
    if args.command != 'preview':
        config = Config(args.data_dir, mode=args.mode, host=args.host, port=args.port,
                        runtime_address=args.runtime_address, allowed_origins=tuple(args.allowed_origin),
                        secure_cookie=args.secure_cookie)
        gateway_log=GatewayLog(config.data_dir)
        gateway_log.record('gateway.starting','Starting Web gateway',mode=config.mode)
        try:
            app=create_app(config,gateway_log=gateway_log)
        except Exception:
            gateway_log.record('gateway.startup.failed','Web gateway initialization failed',level=logging.ERROR)
            gateway_log.close()
            raise
        try:
            uvicorn.run(app, host=args.host, port=args.port, workers=1, access_log=False)
        finally:
            gateway_log.close()
        return

    from .packages import PackageStore
    from .runtime_client import GrpcRuntimeClient
    from .fixture_runtime import FixtureRuntime
    if not 0 <= args.port <= 65535:
        parser.error('port must be 0..65535')
    # No state is shared with production; cleanup never stops a Job.
    with tempfile.TemporaryDirectory(prefix='emo-panel-preview-') as directory:
        store = PackageStore(Path(directory) / 'packages', database=Path(directory) / 'web.sqlite')
        try:
            installed = store.install(args.package.read_bytes())
            item = store.get(installed['packageId'])
            context = validate_preview_context(args.context, item['manifest'])
            store.activate(installed['packageId'], runtime_id='configured')
        except (ValueError, OSError) as error:
            parser.error(str(error))
        host = '127.0.0.1' if args.host == 'localhost' else args.host
        family = socket.AF_INET6 if host == '::1' else socket.AF_INET
        with socket.socket(family) as sock:
            sock.bind((host, args.port))
            sock.listen(128)
            port = sock.getsockname()[1]
            token = secrets.token_urlsafe(32)
            config = Config(Path(directory), mode='grpc' if context.mode == 'runtime' else 'mock',
                            host=host, port=port, preview=True,
                            runtime_address=context.runtime.target if context.runtime else None,
                            preview_context=context.model_dump(mode='json'), preview_token=token)
            runtime = (GrpcRuntimeClient(context.runtime.target, preview_context=context)
                       if context.runtime else FixtureRuntime(context))
            gateway_log=GatewayLog(config.data_dir)
            gateway_log.record('gateway.starting','Starting preview Web gateway',mode=config.mode)
            try:
                app = create_app(config, runtime=runtime,gateway_log=gateway_log)
            except Exception:
                gateway_log.record('gateway.startup.failed','Preview gateway initialization failed',level=logging.ERROR)
                gateway_log.close()
                raise

            class PreviewServer(uvicorn.Server):
                async def startup(self, sockets=None):
                    await super().startup(sockets=sockets)
                    if self.started and not self.should_exit:
                        authority = f'[{host}]' if family == socket.AF_INET6 else host
                        print(json.dumps({'event': 'ready', 'protocolVersion': '1',
                                          'url': f'http://{authority}:{port}/?token={token}'}), flush=True)

            server = PreviewServer(uvicorn.Config(app, host=host, port=port, workers=1, access_log=False))
            try:
                server.run(sockets=[sock])
            finally:
                gateway_log.close()
            if not server.started:
                raise SystemExit(1)


if __name__ == '__main__':
    main()
