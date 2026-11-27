"""Private, bounded GitHub API adapter. Disabled writes until explicit preflight."""
import json
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

PRODUCT="jsdfhasuh/emo_master"
PUBLIC="jsdfhasuh/emo_master_ci"
PRIVATE="jsdfhasuh/test"
IDS={PRODUCT:1343796337,PUBLIC:1468905732,PRIVATE:1235125559}
LEDGER="private-ci-ledger"
HOST="https://api.github.com"
SHA=re.compile(r"[0-9a-f]{40}")
RUN=re.compile(r"[1-9][0-9]{0,17}")
NAME=re.compile(r"[a-z0-9][a-z0-9._-]{0,110}")
MAX_BODY=1_048_576


class Refused(ValueError):
    pass


class Rejected(Refused):
    """An explicit rejected operation, not proof that prior writes did not land."""


class UnknownWrite(Refused):
    """No conclusive HTTP result; hold claim and independently reconcile."""


def need(ok, why):
    if not ok:
        raise Refused(why)


def sha(value):
    need(type(value) is str and SHA.fullmatch(value) and value!="0"*40,"invalid SHA")
    return value


def ident(value):
    need(type(value) is int and RUN.fullmatch(str(value)), "invalid ID")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*_args,**_kwargs):
        return None


class GitHub:
    def __init__(self, token, opener=None):
        need(type(token) is str and token and token.isascii() and not any(c.isspace() for c in token),
             "missing existing private credential")
        self._token=token
        self._http=opener or build_opener(NoRedirect())
        self._authorized=False
        self._writer_id=None
        self._contexts=frozenset()

    def _request(self, method, path, payload=None, expected=200):
        need(path.startswith("/") and not path.startswith("//") and "\\" not in path and "\n" not in path,
             "unexpected API path")
        if method!="GET":
            need(self._authorized,"writes disabled until protected preflight")
        body=None if payload is None else json.dumps(payload,ensure_ascii=True,separators=(",",":")).encode("ascii")
        need(body is None or len(body)<=16384,"oversized request")
        req=Request(HOST+path,method=method,data=body,headers={
            "Accept":"application/vnd.github+json","Authorization":"Bearer "+self._token,
            "User-Agent":"emo-ci-private-broker","X-GitHub-Api-Version":"2022-11-28",
            "Content-Type":"application/json"})
        try:
            with self._http.open(req,timeout=15) as r:
                code=r.status
                raw=r.read(MAX_BODY+1)
        except HTTPError as e:
            code=e.code
            e.close()
            if method=="GET":
                raise Rejected(f"read HTTP {code}") from None
            # 408, 429, redirects or any 5xx are not a decisive delivery result.
            if code in (400,401,403,404,409,422):
                raise Rejected(f"write rejected HTTP {code}") from None
            raise UnknownWrite(f"write result not proven HTTP {code}") from None
        except Exception:
            if method=="GET":
                raise Refused("read not confirmed") from None
            raise UnknownWrite("write result not confirmed") from None
        if code!=expected or len(raw)>MAX_BODY:
            if method!="GET":
                raise UnknownWrite("write response not confirmed")
            raise Refused("read response not confirmed")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (UnicodeError,ValueError):
            if method!="GET":
                raise UnknownWrite("write response could not be decoded") from None
            raise Refused("read response could not be decoded") from None

    def read_repo(self, repo):
        need(repo in IDS,"unexpected repository")
        v=self._request("GET",f"/repos/{repo}")
        need(type(v) is dict and v.get("id")==IDS[repo] and v.get("private") is (repo==PRIVATE) and
             v.get("archived") is False,
             "repository policy mismatch; product Private transition needs separate integration")
        return v

    def read_protection(self,repo,branch):
        need(repo in IDS and branch in ("main",LEDGER) and (branch!=LEDGER or repo==PRIVATE),"unexpected policy subject")
        endpoint=f"/repos/{repo}/branches/{branch}"
        item=self._request("GET",endpoint)
        need(item.get("name")==branch and item.get("protected") is True,"unprotected state or deployment")
        return self._request("GET",endpoint+"/protection")

    def read_run(self,repo,run):
        need(repo in (PUBLIC,PRODUCT),"unexpected run repo")
        return self._request("GET",f"/repos/{repo}/actions/runs/{ident(run)}")

    def read_workflow(self,workflow_id,expected_path):
        r=self._request("GET",f"/repos/{PUBLIC}/actions/workflows/{ident(workflow_id)}")
        need(r.get("id")==workflow_id and r.get("state")=="active" and
             r.get("path")==expected_path and re.fullmatch(r"\.github/workflows/dispatch-[a-z0-9-]+\.yml",expected_path),
             "workflow not registered or wrong path")
        return r

    def worker_tag(self,tag):
        need(type(tag) is str and NAME.fullmatch(tag) and tag.startswith("ci-worker-"),"bad worker tag")
        o=self._request("GET",f"/repos/{PUBLIC}/git/ref/tags/{quote(tag,safe='')}")["object"]
        # Only a directly pinned commit is approved here. No ambiguous dereference.
        need(o.get("type")=="commit","worker tag is not a fixed commit")
        return sha(o.get("sha"))

    def protected_preflight(self, *, enabled, actor_id, contexts, review):
        """review is independent verified actor/protection/immutability evidence.

        It must be produced by the reviewed private entrypoint, never by a
        repository_dispatch input, artifact or untrusted caller. No such entrypoint
        is enabled by this adapter.
        """
        self._authorized=False
        self._writer_id=None
        self._contexts=frozenset()
        if enabled is not True:
            return "dark"
        need(type(review) is dict and review=={"sole_writer":True,"immutable_tags":True,
              "protected_private_environment":True,"private_deployment_reviewed":True},
              "independent deployment review incomplete")
        actor=self._request("GET","/user")
        need(actor.get("id")==ident(actor_id),"wrong broker actor")
        for repo in (PRODUCT,PUBLIC,PRIVATE):
            self.read_repo(repo)
        for repo,branch in ((PRIVATE,LEDGER),(PRIVATE,"main"),(PUBLIC,"main")):
            p=self.read_protection(repo,branch)
            for key,val in (("allow_force_pushes",False),("allow_deletions",False),("enforce_admins",True)):
                need((p.get(key) or {}).get("enabled") is val,"protection not closed")
            if branch==LEDGER:
                need(p.get("required_pull_request_reviews") is None and
                     p.get("required_status_checks") is None and
                     (p.get("lock_branch") or {}).get("enabled") is not True,"ledger not appendable")
        need(type(contexts) is tuple and 0<len(contexts)<=8 and len(set(contexts))==len(contexts) and
             all(type(c) is str and not c.startswith("emo-ci-shadow/dispatch/") and re.fullmatch(r"emo-ci-shadow/[a-z0-9][a-z0-9/_-]{0,80}",c) for c in contexts),
             "only optional allowlisted staging contexts are supported")
        self._contexts=frozenset(contexts)
        self._writer_id=actor_id
        self._authorized=True
        return "eligible_shadow_only"

    def statuses(self, commit, page=1):
        need(type(page) is int and 1<=page<=5,"status search budget")
        result=self._request("GET",f"/repos/{PRODUCT}/commits/{sha(commit)}/statuses?per_page=100&page={page}")
        need(type(result) is list and len(result)<=100,"invalid status history")
        return result

    def post_status(self, commit, context, state, worker_run, description, *, origin_run=None):
        need(self._authorized and context in self._contexts and state in ("pending","success","failure","error")
             and type(description) is str and re.fullmatch(r"[A-Za-z0-9 _:/-]{1,140}",description),
             "status target or message refused")
        url=self.target_url(worker_run,origin_run)
        out=self._request("POST",f"/repos/{PRODUCT}/statuses/{sha(commit)}",
                           dict(context=context,state=state,target_url=url,description=description),expected=201)
        if (not isinstance(out,dict) or out.get("context")!=context or out.get("state")!=state or
            out.get("target_url")!=url or out.get("description")!=description or
            out.get("creator",{}).get("id")!=self._writer_id):
            raise UnknownWrite("status POST receipt does not match exact claim")
        # Caller still has to read back independently under the same writer claim.
        return out


    def target_url(self,worker_run,origin_run):
        if worker_run is None:
            return f"https://github.com/{PRODUCT}/actions/runs/{ident(origin_run)}"
        return f"https://github.com/{PUBLIC}/actions/runs/{ident(worker_run)}"


class StatusWriter:
    """Private bridge to broker_tick. Requires an upstream authenticated origin."""
    def __init__(self, gh, origin_run):
        self.gh=gh
        self.origin_run=ident(origin_run)

    def _description(self,g,state):
        need(g.valid(),"invalid status generation")
        attempt = g.attempt if g.attempt else 0
        return f"emo-ci {g.binding} rev {g.revision} attempt {attempt} {state}"

    def read(self, g, decision):
        need(self.gh._authorized and g.context in self.gh._contexts,"unapproved status context")
        desc=self._description(g,decision[0])
        url=self.gh.target_url(g.worker_run,self.origin_run)
        seen=set()
        for page in range(1,6):
            rows=self.gh.statuses(g.check_sha,page)
            for row in rows:
                key=ident(row.get("id"))
                if key in seen:
                    return "ambiguous"
                seen.add(key)
                if row.get("context")!=g.context:
                    continue
                if row.get("creator",{}).get("id")!=self.gh._writer_id:
                    return "foreign"
                if row.get("target_url")!=url:
                    return "foreign"
                if row.get("state")==decision[0] and row.get("description")==desc:
                    return "exact"
                if row.get("state")=="pending" and row.get("description")==self._description(g,"pending"):
                    return "own_pending"
                return "foreign"
            if len(rows)<100:
                return "absent"
        return "ambiguous"

    def post(self,g,decision):
        from broker_tick import DefiniteRejection
        try:
            self.gh.post_status(g.check_sha,g.context,decision[0],g.worker_run,
                                self._description(g,decision[0]),origin_run=self.origin_run)
        except Rejected:
            # Explicit HTTP rejection; no assumption about any older claim.
            raise DefiniteRejection("this status POST was rejected") from None
