import argparse
import getpass
import json
import tempfile
from pathlib import Path
from .auth import AuthStore
from .config import Config

def main():
    parser=argparse.ArgumentParser(description='Independent Web operator panel; explicit mock mode until official SDK handoff')
    parser.add_argument('--data-dir',type=Path,default=Path('.panel-data'))
    sub=parser.add_subparsers(dest='command',required=True)
    user=sub.add_parser('create-user');user.add_argument('username');user.add_argument('--role',choices=['admin','operator','viewer'],default='admin')
    serve=sub.add_parser('serve');serve.add_argument('--mode',choices=['mock','grpc'],required=True);serve.add_argument('--runtime-address');serve.add_argument('--host',default='127.0.0.1');serve.add_argument('--port',type=int,default=8780);serve.add_argument('--allowed-origin',action='append',default=[]);serve.add_argument('--secure-cookie',action='store_true')
    preview=sub.add_parser('preview');preview.add_argument('--package',type=Path,required=True);preview.add_argument('--port',type=int,default=0)
    args=parser.parse_args()
    if args.command=='create-user':
        password=getpass.getpass('Password (12+ characters): ')
        if password != getpass.getpass('Confirm password: '): parser.error('passwords differ')
        AuthStore(args.data_dir/'web.sqlite').create_user(args.username,password,args.role)
        print('User created. No default password or account is installed.')
        return
    import uvicorn
    from .app import create_app
    if args.command=='preview':
        # Preview uses isolated state and never obtains production control permissions.
        import secrets, socket
        from .packages import PackageStore
        with tempfile.TemporaryDirectory(prefix='emo-panel-preview-') as d:
            sock=socket.socket();sock.bind(('127.0.0.1',args.port));sock.listen(128)
            port=sock.getsockname()[1]
            config=Config(Path(d),mode='mock',port=port,preview=True)
            store=PackageStore(config.data_dir/'packages');item=store.install(args.package.read_bytes());store.activate(item['packageId'])
            password=secrets.token_urlsafe(24)
            AuthStore(config.data_dir/'web.sqlite').create_user('preview',password,'viewer')
            print(json.dumps({'url':f'http://127.0.0.1:{port}','username':'preview','password':password,'expires':'process exit','mode':'draft-mock-preview'}),flush=True)
            try:uvicorn.Server(uvicorn.Config(create_app(config),host='127.0.0.1',port=port,workers=1,access_log=False)).run(sockets=[sock])
            finally:sock.close()
    else:
        config=Config(args.data_dir,mode=args.mode,host=args.host,port=args.port,runtime_address=args.runtime_address,allowed_origins=tuple(args.allowed_origin),secure_cookie=args.secure_cookie)
        uvicorn.run(create_app(config),host=args.host,port=args.port,workers=1,access_log=False)
if __name__=='__main__': main()
