"""Private transport adapter for an existing, explicitly approved *draft* only.
Inputs: trusted private recovery receipt and already assembled six assets.
No tag creation, publish, unpublish, deletion, replacement, or secret printing.
"""
import hashlib
import re
from pathlib import Path
from urllib.parse import quote
import http.client
import json

REPO = 'jsdfhasuh/emo_master'
REPO_ID = 1343796337
def require(ok, reason):
    if not ok: raise ValueError(reason)

def asset_names(tag):
    require(re.fullmatch(r'v[0-9]+(?:\.[0-9]+){1,3}(?:[-.][A-Za-z0-9._-]+)?', tag), 'invalid release tag')
    return {f'{prefix}-{kind}-{tag}.{ext}' for prefix in ('emo-master','emo-master-runtime')
            for kind,ext in [('windows','zip'),('setup','exe')]} | {'manifest.json','runtime-manifest.json'}

def stage(client, folder, receipt, draft_id, approved_product_sha, approved_run, approved_versions):
    tag = receipt['tag']
    names = asset_names(tag)
    require(receipt['repo']==REPO and receipt['v2_recovered'] is True and receipt['source_gate_passed'] is True, 'unverified receipt')
    require(all(re.fullmatch('[0-9a-f]{40}',receipt[k]) for k in ('product_sha','controller_sha','packager_sha','reusable_sha')), 'invalid identity')
    require(approved_product_sha==receipt['product_sha'] and approved_run==(receipt['run_id'],receipt['attempt']) and all(type(x) is int and x>0 for x in approved_run), 'unapproved version')
    pins=tuple(receipt[k] for k in ('controller_sha','packager_sha','reusable_sha'))
    require(pins==approved_versions and all(x!='0'*40 for x in (approved_product_sha,*pins)), 'unapproved controller or packager')
    require(type(draft_id) is int and draft_id>0,'invalid draft')
    require(set(receipt['assets'])==names,'incomplete release mapping')
    def check_target():
        repository=client.get('')
        require(repository.get('id')==REPO_ID and repository.get('full_name')==REPO
                and repository.get('private') is True,'fixed product is not Private')
        d=client.get(f'/releases/{draft_id}')
        require(d['id']==draft_id and d['draft'] is True and d['tag_name']==tag,'draft/tag mismatch')
        obj=client.get('/git/ref/tags/'+quote(tag,safe=''))['object']
        for _ in range(8):
            if obj['type']!='tag': break
            require(re.fullmatch('[0-9a-f]{40}',obj['sha']), 'invalid tag object')
            obj=client.get('/git/tags/'+obj['sha'])['object']
        require(obj=={'type':'commit','sha':approved_product_sha} or (obj.get('type')=='commit' and obj.get('sha')==approved_product_sha),'tag moved')
    check_target()
    files={}
    for name in sorted(names):
        path=Path(folder)/name
        require(not path.is_symlink() and path.is_file(), 'missing regular asset')
        h=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
        digest=h.hexdigest()
        size=path.stat().st_size
        require(size>0 and receipt['assets'][name]=={'size':size,'sha256':digest},'local hash or size mismatch')
        files[name]=(path,size,'sha256:'+digest)
    def inventory():
        entries=client.get(f'/releases/{draft_id}/assets?per_page=100')
        require(len(entries)<=len(names) and len({e['name'] for e in entries})==len(entries),'unexpected inventory')
        for e in entries:
            require(e['name'] in files,'unknown asset')
            _,size,digest=files[e['name']]
            require((e.get('state'),e.get('size'),e.get('digest'))==('uploaded',size,digest),'remote asset collision or missing digest')
        return {e['name'] for e in entries}
    for name in sorted(names):
        check_target()
        if name not in inventory(): client.upload(draft_id,name,files[name][0])
    check_target()
    require(inventory()==names,'incomplete draft')
    return sorted(names)

class GitHub:
    """Instantiate only in protected private release job after separate approval."""
    def __init__(self,token): self._token=token
    def request(self,host,method,path,body=None,size=None):
        conn=http.client.HTTPSConnection(host,timeout=240)
        headers={'Authorization':'Bearer '+self._token,'User-Agent':'dot-private-release','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
        if size is not None: headers.update({'Content-Length':str(size),'Content-Type':'application/octet-stream'})
        try:
            conn.request(method,path,body,headers)
            resp=conn.getresponse()
            require(resp.status==(201 if method=='POST' else 200),f'GitHub HTTP {resp.status}; inspect draft and retry without replacement')
            body=resp.read(1024*1024+1)
            require(len(body)<=1024*1024,'GitHub response exceeded bound')
            return json.loads(body)
        finally: conn.close()
    def get(self,suffix): return self.request('api.github.com','GET','/repos/'+REPO+suffix)
    def upload(self,id,name,path):
        with path.open('rb') as f:
            return self.request('uploads.github.com','POST',f'/repos/{REPO}/releases/{id}/assets?name='+quote(name,safe=''),f,path.stat().st_size)
