import { useEffect, useState } from 'react';
import { api } from '../data/api';
interface User {
    username: string;
    role: string;
}
export function Admin({ current }: {
    current: string;
}) {
    const [users, setUsers] = useState<User[]>([]);
    const [runtimes, setRuntimes] = useState<Record<string, unknown>[]>([]);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    async function refresh() { setUsers(await api('/api/admin/users')); setRuntimes(await api('/api/admin/runtimes')); }
    useEffect(() => { refresh().catch(e => setError(e.message)); }, []);
    async function mutate(path: string, method: string, body?: unknown) { setBusy(true); setError(''); try {
        await api(path, { method, body: body ? JSON.stringify(body) : undefined });
        await refresh();
    }
    catch (e) {
        setError((e as Error).message);
    }
    finally {
        setBusy(false);
    } }
    return <details className="admin"><summary>账户与 Runtime 管理</summary>{error && <p role="alert">{error}</p>}<h3>用户权限</h3><p>查看者只读；操作员可控制；管理员具有管理和操作权限。最终权限由后端校验。</p><div className="table-scroll"><table><thead><tr><th>账号</th><th>角色</th><th>操作</th></tr></thead><tbody>{users.map(u => <tr key={u.username}><td>{u.username}</td><td><select aria-label={`${u.username} 角色`} value={u.role} disabled={busy || u.username === current} onChange={e => mutate(`/api/admin/users/${encodeURIComponent(u.username)}`, 'PATCH', { role: e.target.value })}>{['viewer', 'operator', 'admin'].map(r => <option key={r}>{r}</option>)}</select></td><td><button disabled={busy || u.username === current} onClick={() => { if (confirm(`删除用户 ${u.username} 并撤销其会话？`))
        mutate(`/api/admin/users/${encodeURIComponent(u.username)}`, 'DELETE'); }}>删除</button></td></tr>)}</tbody></table></div><form className="toolbar" onSubmit={e => { e.preventDefault(); const f = new FormData(e.currentTarget); mutate('/api/admin/users', 'POST', { username: f.get('username'), password: f.get('password'), role: f.get('role') }); }}><label>新用户名<input name="username" required autoComplete="off"/></label><label>初始密码<input name="password" type="password" minLength={12} required autoComplete="new-password"/></label><label>角色<select name="role"><option>viewer</option><option>operator</option><option>admin</option></select></label><button disabled={busy}>创建用户</button></form><h3>Runtime 配置</h3><p>目标由管理员的部署配置指定，本页只读。不允许浏览器填写任意 RPC 地址。</p>{runtimes.map((r, i) => <pre key={i}>{JSON.stringify(r, null, 2)}</pre>)}</details>;
}
