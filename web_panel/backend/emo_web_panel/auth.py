import hashlib
import hmac
import secrets
import sqlite3
import time
from pathlib import Path

PERMISSIONS = {"viewer": ["view"], "operator": ["view", "control"], "admin": ["view", "control", "admin"]}

class AuthStore:
    def __init__(self, path: Path, ttl=3600):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.ttl = path, ttl
        with self.db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, salt TEXT, password TEXT, role TEXT);
            CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, username TEXT, csrf TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS audit (at REAL, username TEXT, action TEXT, target TEXT, outcome TEXT);""")
    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db
    @staticmethod
    def hash_password(password, salt):
        return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    def create_user(self, username, password, role):
        if not isinstance(role,str) or role not in PERMISSIONS or not isinstance(username,str) or not username or len(username) > 80 or not isinstance(password,str) or not 12 <= len(password) <= 1024:
            raise ValueError("valid username, role and password of at least 12 characters required")
        salt = secrets.token_hex(16)
        hashed = self.hash_password(password, salt)
        with self.db() as db:
            db.execute("INSERT INTO users VALUES (?,?,?,?)", (username, salt, hashed, role))
    def login(self, username, password):
        with self.db() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            salt = row["salt"] if row else "00" * 16
            computed = self.hash_password(password, salt)
            if row is None or not hmac.compare_digest(computed, row["password"]):
                return None
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), username, csrf, time.time()+self.ttl))
            return token
    def session(self, token):
        if not token: return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.db() as db:
            row = db.execute("SELECT u.username,u.role,s.csrf,s.expires FROM sessions s JOIN users u ON u.username=s.username WHERE digest=?", (digest,)).fetchone()
            if row is None or row["expires"] <= time.time(): return None
            return {"username": row["username"], "role": row["role"], "csrf": row["csrf"], "permissions": PERMISSIONS[row["role"]]}
    def logout(self, token):
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),))
    def users(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT username,role FROM users ORDER BY username")]
    def audit(self, username, action, target, outcome):
        with self.db() as db:
            db.execute("INSERT INTO audit VALUES (?,?,?,?,?)", (time.time(), username, action, target, outcome))
    def update_user(self, username, role=None, password=None):
        with self.db() as db:
            row=db.execute('SELECT role FROM users WHERE username=?',(username,)).fetchone()
            if row is None:raise KeyError('User not found')
            if role is not None:
                if not isinstance(role,str) or role not in PERMISSIONS:raise ValueError('Invalid role')
                if row['role']=='admin' and role!='admin' and db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]<=1:raise ValueError('Cannot remove last administrator')
                db.execute('UPDATE users SET role=? WHERE username=?',(role,username))
            if password is not None:
                if not isinstance(password,str) or len(password)<12 or len(password)>1024:raise ValueError('Password must be 12..1024 characters')
                salt=secrets.token_hex(16)
                db.execute('UPDATE users SET salt=?,password=? WHERE username=?',(salt,self.hash_password(password,salt),username))
            db.execute('DELETE FROM sessions WHERE username=?',(username,))
    def delete_user(self, username):
        with self.db() as db:
            row=db.execute('SELECT role FROM users WHERE username=?',(username,)).fetchone()
            if row is None:raise KeyError('User not found')
            if row['role']=='admin' and db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]<=1:raise ValueError('Cannot remove last administrator')
            db.execute('DELETE FROM sessions WHERE username=?',(username,));db.execute('DELETE FROM users WHERE username=?',(username,))
