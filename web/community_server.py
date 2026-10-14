"""Haptonics local/community server. Run: python3 community_server.py [port]."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from http import cookies
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('HAPTONICS_DB', ROOT / 'community.sqlite3')).resolve()
SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password TEXT NOT NULL, created INTEGER NOT NULL, share_stats INTEGER NOT NULL DEFAULT 0, share_mood INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS communities(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, code TEXT UNIQUE NOT NULL, discoverable INTEGER NOT NULL, owner_id INTEGER NOT NULL REFERENCES users(id), created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS members(community_id INTEGER NOT NULL REFERENCES communities(id), user_id INTEGER NOT NULL REFERENCES users(id), PRIMARY KEY(community_id,user_id));
CREATE TABLE IF NOT EXISTS friends(user_id INTEGER NOT NULL REFERENCES users(id), friend_id INTEGER NOT NULL REFERENCES users(id), PRIMARY KEY(user_id,friend_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target_id INTEGER NOT NULL, sender_id INTEGER NOT NULL REFERENCES users(id), body TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS stats(user_id INTEGER PRIMARY KEY REFERENCES users(id), listening_ms INTEGER NOT NULL DEFAULT 0, meditation_count INTEGER NOT NULL DEFAULT 0, meditation_ms INTEGER NOT NULL DEFAULT 0, recent_mood TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS stat_days(user_id INTEGER NOT NULL REFERENCES users(id), day TEXT NOT NULL, rhythm_ms INTEGER NOT NULL DEFAULT 0, breaths INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,day));
'''
BAD = re.compile(r'(?i)\b(?:fuck|shit|bitch|asshole|damn|crap)\b|操你妈|傻逼|妈的')

def db():
    connection = sqlite3.connect(DB, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.executescript(SCHEMA)
    return connection

def clean_message(value):
    value = str(value).strip()
    if not value or len(value) > 2000:
        raise ValueError('消息需为 1–2000 个字符')
    return BAD.sub(lambda match: '＊' * len(match.group()), value)

def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def send_json(self, status, data, token=None, clear=False):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        if token or clear:
            flags = '; HttpOnly; SameSite=Strict; Path=/'
            if self.headers.get('X-Forwarded-Proto') == 'https':
                flags += '; Secure'
            self.send_header('Set-Cookie', 'haptonics_session=' + (token or '') + flags + ('; Max-Age=0' if clear else '; Max-Age=2592000'))
        self.end_headers()
        self.wfile.write(body)

    def current_user(self, con):
        jar = cookies.SimpleCookie()
        try: jar.load(self.headers.get('Cookie', ''))
        except cookies.CookieError: return None
        raw = jar.get('haptonics_session')
        if not raw: return None
        token_hash = hashlib.sha256(raw.value.encode()).hexdigest()
        return con.execute('SELECT users.* FROM users JOIN sessions ON sessions.user_id=users.id WHERE sessions.token_hash=? AND sessions.expires>?', (token_hash, int(time.time()))).fetchone()

    def body(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 50000: raise ValueError('请求过大')
        return json.loads(self.rfile.read(size) or b'{}')

    def do_GET(self):
        if not urlparse(self.path).path.startswith('/api/'):
            if self.path == '/': self.path = '/musex.html'
            if '/.' in self.path or '.sqlite3' in self.path or self.path.endswith('.py') or self.path.endswith('.pyc'):
                return self.send_error(404)
            return super().do_GET()
        self.api('GET')

    def do_POST(self): self.api('POST')

    def api(self, method):
        path = urlparse(self.path).path
        try:
            if method == 'POST' and self.headers.get('Origin') not in (None, 'http://' + self.headers.get('Host'), 'https://' + self.headers.get('Host')):
                return self.send_json(403, {'error':'来源不符'})
            con = db()
            with con:
                user = self.current_user(con)
                data = self.body() if method == 'POST' else {}
                if path in ('/api/register','/api/login') and method == 'POST':
                    email = str(data.get('email','')).strip()
                    if '@' in email: email=email.lower()
                    password = str(data.get('password',''))
                    if path.endswith('register'):
                        name = str(data.get('name','')).strip()
                        if not re.fullmatch(r'[\w\-]{3,24}', name, re.UNICODE) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(password)<8 or len(password)>128:
                            raise ValueError('请填写有效邮箱、3–24 字符用户名及至少 8 位密码')
                        salt=secrets.token_hex(16)
                        try: con.execute('INSERT INTO users(email,name,salt,password,created) VALUES(?,?,?,?,?)',(email,name,salt,password_hash(password,salt),int(time.time())))
                        except sqlite3.IntegrityError: raise ValueError('邮箱或用户名已被使用')
                        user=con.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                    else:
                        user=con.execute('SELECT * FROM users WHERE email=? OR name=?',(email,email)).fetchone()
                        if not user or not hmac.compare_digest(user['password'], password_hash(password,user['salt'])):
                            return self.send_json(401,{'error':'账号或密码错误'})
                    token=secrets.token_urlsafe(32)
                    con.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],int(time.time())+2592000))
                    return self.send_json(200,{'user':self.user_info(user)},token)
                if not user: return self.send_json(401,{'error':'请先登录'})
                uid=user['id']
                if path=='/api/me' and method=='GET':
                    return self.send_json(200,{'user':self.user_info(user)})
                if path=='/api/logout' and method=='POST':
                    jar=cookies.SimpleCookie();jar.load(self.headers.get('Cookie',''))
                    if jar.get('haptonics_session'): con.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(jar['haptonics_session'].value.encode()).hexdigest(),))
                    return self.send_json(200,{'ok':True},clear=True)
                if path=='/api/privacy' and method=='POST':
                    con.execute('UPDATE users SET share_stats=?,share_mood=? WHERE id=?',(int(data.get('share_stats') is True),int(data.get('share_mood') is True),uid))
                    return self.send_json(200,{'ok':True})
                if path=='/api/stats' and method=='POST':
                    days=data.get('days',{})
                    if not isinstance(days,dict) or len(days)>400: raise ValueError('统计数据过多')
                    for day,row in days.items():
                        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day) or not isinstance(row,dict): continue
                        rhythm=max(0,min(int(row.get('rhythmMs',0)),86400000));breaths=max(0,min(int(row.get('breaths',0)),500))
                        con.execute('INSERT INTO stat_days VALUES(?,?,?,?) ON CONFLICT(user_id,day) DO UPDATE SET rhythm_ms=max(rhythm_ms,excluded.rhythm_ms),breaths=max(breaths,excluded.breaths)',(uid,day,rhythm,breaths))
                    totals=con.execute('SELECT coalesce(sum(rhythm_ms),0) r,coalesce(sum(breaths),0) b FROM stat_days WHERE user_id=?',(uid,)).fetchone()
                    meditation_ms=max(0,min(int(data.get('meditationMs',0)),100000000000))
                    mood=str(data.get('mood',''))[:24]
                    con.execute('INSERT INTO stats(user_id,listening_ms,meditation_count,meditation_ms,recent_mood) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET listening_ms=excluded.listening_ms,meditation_count=excluded.meditation_count,meditation_ms=excluded.meditation_ms,recent_mood=excluded.recent_mood',(uid,totals['r'],totals['b'],meditation_ms,mood))
                    return self.send_json(200,{'ok':True})
                if path=='/api/communities' and method=='GET':
                    rows=con.execute('SELECT c.id,c.name,c.description,c.discoverable,c.code,c.owner_id,(SELECT count(*) FROM members WHERE community_id=c.id) members,EXISTS(SELECT 1 FROM members WHERE community_id=c.id AND user_id=?) joined FROM communities c WHERE c.discoverable=1 OR EXISTS(SELECT 1 FROM members WHERE community_id=c.id AND user_id=?) ORDER BY joined DESC,c.id DESC',(uid,uid)).fetchall()
                    return self.send_json(200,{'communities':[dict(r)|{'code':r['code'] if r['joined'] else None} for r in rows]})
                if path=='/api/communities' and method=='POST':
                    name=str(data.get('name','')).strip();description=str(data.get('description','')).strip()
                    if not 2<=len(name)<=60 or len(description)>240: raise ValueError('群名需为 2–60 字符，简介最多 240 字符')
                    code=secrets.token_hex(4).upper()
                    cur=con.execute('INSERT INTO communities(name,description,code,discoverable,owner_id,created) VALUES(?,?,?,?,?,?)',(name,description,code,int(data.get('discoverable') is True),uid,int(time.time())))
                    con.execute('INSERT INTO members VALUES(?,?)',(cur.lastrowid,uid))
                    return self.send_json(200,{'id':cur.lastrowid,'code':code})
                if path=='/api/join' and method=='POST':
                    code=str(data.get('code','')).strip().upper()
                    group=con.execute('SELECT id FROM communities WHERE code=?',(code,)).fetchone() if code else con.execute('SELECT id FROM communities WHERE id=? AND discoverable=1',(int(data.get('id',0)),)).fetchone()
                    if not group: return self.send_json(404,{'error':'未找到可加入的群'})
                    con.execute('INSERT OR IGNORE INTO members VALUES(?,?)',(group['id'],uid))
                    return self.send_json(200,{'id':group['id']})
                if path=='/api/users' and method=='GET':
                    q=parse_qs(urlparse(self.path).query).get('q',[''])[0].strip()[:50]
                    rows=con.execute('SELECT id,name,created FROM users WHERE id<>? AND name LIKE ? ORDER BY name LIMIT 30',(uid,'%'+q+'%')).fetchall()
                    return self.send_json(200,{'users':[dict(r) for r in rows]})
                if path=='/api/friends' and method=='GET':
                    rows=con.execute('SELECT u.id,u.name,u.created FROM friends f JOIN users u ON u.id=f.friend_id WHERE f.user_id=? ORDER BY u.name',(uid,)).fetchall()
                    return self.send_json(200,{'friends':[dict(r) for r in rows]})
                if path=='/api/friends' and method=='POST':
                    other=int(data.get('id',0))
                    if other==uid or not con.execute('SELECT 1 FROM users WHERE id=?',(other,)).fetchone(): raise ValueError('用户不存在')
                    con.execute('INSERT OR IGNORE INTO friends VALUES(?,?)',(uid,other))
                    return self.send_json(200,{'ok':True})
                if path.startswith('/api/profile/') and method=='GET':
                    other=con.execute('SELECT * FROM users WHERE id=?',(int(path.rsplit('/',1)[1]),)).fetchone()
                    if not other: return self.send_json(404,{'error':'用户不存在'})
                    info=self.user_info(other);info['stats']=None
                    if other['id']==uid or other['share_stats']:
                        row=con.execute('SELECT listening_ms,meditation_count,meditation_ms,recent_mood FROM stats WHERE user_id=?',(other['id'],)).fetchone()
                        info['stats']=dict(row) if row else {'listening_ms':0,'meditation_count':0,'meditation_ms':0,'recent_mood':''}
                        if other['id']!=uid and not other['share_mood']: info['stats']['recent_mood']=None
                    return self.send_json(200,{'profile':info})
                if path=='/api/messages' and method=='GET':
                    args=parse_qs(urlparse(self.path).query);kind=args.get('kind',[''])[0];target=int(args.get('target',['0'])[0]);since=max(0,int(args.get('since',['0'])[0]))
                    if not self.can_chat(con,uid,kind,target): return self.send_json(403,{'error':'无权查看此聊天'})
                    if kind=='group': rows=con.execute('SELECT m.id,m.body,m.created,u.name sender,m.sender_id FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.kind=? AND m.target_id=? AND m.id>? ORDER BY m.id LIMIT 100',(kind,target,since)).fetchall()
                    else: rows=con.execute('SELECT m.id,m.body,m.created,u.name sender,m.sender_id FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.kind=? AND m.id>? AND ((m.sender_id=? AND m.target_id=?) OR (m.sender_id=? AND m.target_id=?)) ORDER BY m.id LIMIT 100',(kind,since,uid,target,target,uid)).fetchall()
                    return self.send_json(200,{'messages':[dict(r) for r in rows]})
                if path=='/api/messages' and method=='POST':
                    kind=str(data.get('kind',''));target=int(data.get('target',0))
                    if not self.can_chat(con,uid,kind,target): return self.send_json(403,{'error':'无权发送到此聊天'})
                    body=clean_message(data.get('body',''))
                    cur=con.execute('INSERT INTO messages(kind,target_id,sender_id,body,created) VALUES(?,?,?,?,?)',(kind,target,uid,body,int(time.time())))
                    return self.send_json(200,{'id':cur.lastrowid,'body':body})
                return self.send_json(404,{'error':'未知接口'})
        except (ValueError,TypeError,OverflowError,json.JSONDecodeError) as e:
            self.send_json(400,{'error':str(e)})
        except Exception:
            self.send_json(500,{'error':'服务器错误'})

    @staticmethod
    def user_info(user):
        return {'id':user['id'],'name':user['name'],'created':user['created'],'share_stats':bool(user['share_stats']),'share_mood':bool(user['share_mood'])}

    @staticmethod
    def can_chat(con,uid,kind,target):
        if kind=='group': return bool(con.execute('SELECT 1 FROM members WHERE community_id=? AND user_id=?',(target,uid)).fetchone())
        if kind=='direct' and target!=uid: return bool(con.execute('SELECT 1 FROM friends WHERE user_id=? AND friend_id=?',(uid,target)).fetchone() and con.execute('SELECT 1 FROM friends WHERE user_id=? AND friend_id=?',(target,uid)).fetchone())
        return False

if __name__=='__main__':
    port=int(sys.argv[1]) if len(sys.argv)>1 else 8000
    print(f'Haptonics: http://127.0.0.1:{port}/')
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
