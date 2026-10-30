"""Personal academy ranking notebooks. No teacher roles or member-management API."""
import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from http.cookies import SimpleCookie
from database import connect, init_db, STORE, POSTGRES, IntegrityError, lock_account

ROOT = Path(__file__).resolve().parent
SECURE = os.environ.get('COOKIE_SECURE','0') == '1'
KST = timezone(timedelta(hours=9))
PASSWORD_ROUNDS = 600000

def now(): return datetime.now(KST).isoformat(timespec='microseconds')
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def password_hash(p):
    salt = secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256',p.encode(),salt.encode(),PASSWORD_ROUNDS).hex()
def password_ok(p,stored):
    try:
        salt, hashed = stored.split(':')
        return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',p.encode(),salt.encode(),PASSWORD_ROUNDS).hex(),hashed)
    except (ValueError,AttributeError): return False

class Problem(Exception):
    def __init__(self,text,status=400): self.text,self.status=text,status
def require(value,message,status=400):
    if not value: raise Problem(message,status)

def valid_password(value):
    require(isinstance(value,str) and 10 <= len(value) <= 200,'비밀번호는 10~200자로 입력해 주세요.')
    return value
def valid_account(p):
    email = str(p.get('email','')).strip().lower()
    name = str(p.get('name','')).strip()
    require(len(email)<=200 and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email),'이메일 주소를 확인해 주세요.')
    require(1 <= len(name) <= 60,'이름을 60자 이내로 입력해 주세요.')
    return email,name,valid_password(p.get('password',''))

def throttle(c,key,limit=15,seconds=900):
    stamp = time.time()
    row = c.execute('''INSERT INTO attempts(key,count,expires) VALUES(?,1,?)
        ON CONFLICT(key) DO UPDATE SET
        count=CASE WHEN attempts.expires>? THEN attempts.count+1 ELSE 1 END,
        expires=CASE WHEN attempts.expires>? THEN attempts.expires ELSE ? END
        RETURNING count''',(key,stamp+seconds,stamp,stamp,stamp+seconds)).fetchone()
    c.commit()
    require(row['count']<=limit,'시도가 많습니다. 잠시 후 다시 이용해 주세요.',429)

def normalized(p):
    name = str(p.get('name','')).strip()
    blog = str(p.get('blog','')).strip()
    if 'blog.naver.com/' in blog:
        blog=blog.split('blog.naver.com/',1)[1].split('/')[0].split('?')[0]
    require(not blog or re.fullmatch(r'[A-Za-z0-9_-]{2,80}',blog),'네이버 블로그 ID 또는 기본 블로그 주소를 입력해 주세요.')
    place = str(p.get('place','')).strip()
    if place:
        require(re.fullmatch(r'\d{3,30}',place) or re.fullmatch(r'https://(?:map\.naver\.com|m\.place\.naver\.com|pcmap\.place\.naver\.com)/[^\s]{1,400}',place),'플레이스 상세 주소 또는 숫자 ID를 입력해 주세요.')
        require(re.search(r'(?:place/|/)(\d{3,30})(?:[/?#]|$)',place) or place.isdigit(),'숫자 ID가 포함된 플레이스 상세 주소를 입력해 주세요. 단축 링크는 지원하지 않습니다.')
    def words(key):
        raw=p.get(key,[])
        if isinstance(raw,str): raw=raw.splitlines()
        require(isinstance(raw,list),'키워드 형식을 확인해 주세요.')
        out=list(dict.fromkeys(str(x).strip() for x in raw if str(x).strip()))
        require(len(out)<=40 and all(len(x)<=80 for x in out),'종류별 키워드는 40개 이하, 각 80자 이하로 입력해 주세요.')
        return out
    b,k=words('blog_keywords'),words('place_keywords')
    require(1 <= len(name) <= 80,'학원명을 입력해 주세요.')
    require(not b or blog,'블로그 키워드를 사용하려면 블로그 ID가 필요합니다.')
    require(not k or place,'플레이스 키워드를 사용하려면 플레이스 상세 주소가 필요합니다.')
    return dict(name=name,blog=blog.lower(),place=place,blog_keywords=b,place_keywords=k)

def user_public(u): return {k:u[k] for k in ('id','email','name')}
def run_public(r):
    if not r: return None
    d=dict(r)
    d.pop('user_id',None)
    d['config']=json.loads(d['config'])
    d['results']=json.loads(d['results'])
    # Local diagnostics never go to the client or public pages.
    d.pop('log',None)
    return d
def public_snapshot(r):
    data=run_public(r)
    keys=('kind','keyword','rank','status','count','url','title','ranks','blog_rank')
    rows=[{k:row[k] for k in keys if k in row} for row in data['results']]
    return dict(academy_name=data['config']['name'],created=data['created'],finished=data['finished'],
                status=data['status'],results=rows)

def make_session(c,uid):
    token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
    c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(token),uid,csrf,time.time()+86400*7))
    cookie=f'rank_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800'+('; Secure' if SECURE else '')
    return csrf,cookie

def dispatch(c,method,path,p,env):
    # Retired administrative and submission routes never expose member information.
    if path.startswith(('/api/teacher','/api/setup','/api/submit')):
        raise Problem('페이지를 찾을 수 없습니다.',404)
    if path=='/api/health' and method=='GET':
        c.execute('SELECT 1')
        return {'ok':True},[]
    if path.startswith('/api/public/') and method=='GET':
        key=path.rsplit('/',1)[-1]
        report=c.execute('SELECT snapshot,created FROM reports WHERE share_token=? AND enabled=1',(key,)).fetchone()
        if report: return {'snapshot':json.loads(report['snapshot']),'shared_at':report['created'],'fixed':True},[]
        a=c.execute('SELECT user_id FROM academies WHERE share_token=? AND sharing=1',(key,)).fetchone()
        require(a,'공유가 종료되었거나 존재하지 않는 링크입니다.',404)
        r=c.execute("SELECT * FROM runs WHERE user_id=? AND status IN ('succeeded','partial') ORDER BY created DESC,id DESC LIMIT 1",(a['user_id'],)).fetchone()
        require(r,'아직 공유할 결과가 없습니다.',404)
        return {'snapshot':public_snapshot(r),'fixed':False},[]
    cookie=SimpleCookie()
    try: cookie.load(env.get('HTTP_COOKIE',''))
    except Exception: pass
    token=cookie.get('rank_session')
    s=c.execute('''SELECT s.token,s.user_id,s.csrf,u.id,u.email,u.name,u.password,u.recovery_hash
         FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?''',
         (digest(token.value) if token else '',time.time())).fetchone()
    uid=s['user_id'] if s else None
    if path=='/api/session' and method=='GET':
        return {'user':user_public(s) if s else None,'csrf':s['csrf'] if s else None},[]
    if path in ('/api/login','/api/register','/api/recover') and method=='POST':
        email=str(p.get('email','')).strip().lower()[:200]
        throttle(c,'auth:'+digest(email),15)
        if path=='/api/register':
            throttle(c,'signup:'+env.get('REMOTE_ADDR','unknown'),50,3600)
            email,name,password=valid_account(p)
            require(not c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone(),'이미 사용 중인 이메일입니다.')
            code=secrets.token_urlsafe(24)
            try:
                uid=c.execute('''INSERT INTO users(email,name,password,role,recovery_hash,created)
                     VALUES(?,?,?,'member',?,?) RETURNING id''',(email,name,password_hash(password),digest(code),now())).fetchone()['id']
            except IntegrityError:
                raise Problem('이미 사용 중인 이메일입니다.',409)
            cfg=dict(name='',blog='',place='',blog_keywords=[],place_keywords=[])
            c.execute('INSERT INTO academies VALUES(?,?,?,0)',(uid,json.dumps(cfg),secrets.token_urlsafe(24)))
        else:
            u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
            if path=='/api/login':
                require(u and password_ok(str(p.get('password',''))[:201],u['password']),'이메일 또는 비밀번호가 일치하지 않습니다.',401)
                code=None
            else:
                code_given=str(p.get('recovery_code','')).strip()
                require(u and u['recovery_hash'] and hmac.compare_digest(digest(code_given),u['recovery_hash']),'이메일 또는 복구 코드가 일치하지 않습니다.',401)
                password=valid_password(p.get('password',''))
                code=secrets.token_urlsafe(24)
                # Prevent reuse of the same recovery code by concurrent requests.
                updated=c.execute('UPDATE users SET password=?,recovery_hash=? WHERE id=? AND recovery_hash=?',
                    (password_hash(password),digest(code),u['id'],u['recovery_hash']))
                require(updated.rowcount==1,'이미 사용된 복구 코드입니다. 새 코드로 다시 시도해 주세요.',409)
                c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],))
            uid=u['id']
        csrf,cookie_header=make_session(c,uid)
        u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        data={'user':user_public(u),'csrf':csrf}
        if code: data['recovery_code']=code
        return data,[('Set-Cookie',cookie_header)]
    require(s,'로그인이 필요합니다.',401)
    if method!='GET':
        require(hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN',''),s['csrf']),'로그인 상태를 새로고침하고 다시 시도해 주세요.',403)
    if path=='/api/logout' and method=='POST':
        c.execute('DELETE FROM sessions WHERE token=?',(s['token'],))
        return {'ok':True},[('Set-Cookie','rank_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0')]
    if path=='/api/account' and method=='POST':
        require(password_ok(str(p.get('current_password',''))[:201],s['password']),'현재 비밀번호를 확인해 주세요.',403)
        if p.get('new_password'):
            c.execute('UPDATE users SET password=? WHERE id=?',(password_hash(valid_password(p['new_password'])),uid))
        code=secrets.token_urlsafe(24)
        c.execute('UPDATE users SET recovery_hash=? WHERE id=?',(digest(code),uid))
        c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        csrf,header=make_session(c,uid)
        return {'ok':True,'csrf':csrf,'recovery_code':code},[('Set-Cookie',header)]
    if path=='/api/dashboard' and method=='GET':
        a=c.execute('SELECT * FROM academies WHERE user_id=?',(uid,)).fetchone()
        runs=c.execute('SELECT * FROM runs WHERE user_id=? ORDER BY created DESC,id DESC LIMIT 40',(uid,)).fetchall()
        reports=c.execute('SELECT id,run_id,created,share_token,enabled FROM reports WHERE user_id=? ORDER BY created DESC,id DESC',(uid,)).fetchall()
        return {'config':json.loads(a['config']),'sharing':bool(a['sharing']),'share_token':a['share_token'],
                'runs':[run_public(r) for r in runs],'reports':[dict(x) for x in reports]},[]
    if path=='/api/config' and method=='POST':
        cfg=normalized(p)
        c.execute('UPDATE academies SET config=? WHERE user_id=?',(json.dumps(cfg,ensure_ascii=False),uid))
        return {'ok':True},[]
    if path=='/api/check' and method=='POST':
        lock_account(c,uid)
        cfg=normalized(json.loads(c.execute('SELECT config FROM academies WHERE user_id=?',(uid,)).fetchone()['config']))
        require(cfg['blog_keywords'] or cfg['place_keywords'],'설정에서 키워드를 먼저 등록해 주세요.')
        require(not c.execute("SELECT id FROM runs WHERE user_id=? AND status IN ('queued','running')",(uid,)).fetchone(),'이미 진행 중인 순위 체크가 있습니다.',409)
        active=c.execute("SELECT COUNT(*) AS n FROM runs WHERE status IN ('queued','running')").fetchone()['n']
        require(active<100,'대기 중인 작업이 많습니다. 잠시 후 다시 실행해 주세요.',429)
        daily=c.execute('SELECT COUNT(*) AS n FROM runs WHERE user_id=? AND created LIKE ?',(uid,now()[:10]+'%')).fetchone()['n']
        require(daily<3,'하루 순위 체크는 계정당 3회까지 가능합니다.',429)
        rid=secrets.token_hex(12)
        c.execute('INSERT INTO runs(id,user_id,status,created,config) VALUES(?,?,?,?,?)',(rid,uid,'queued',now(),json.dumps(cfg,ensure_ascii=False)))
        return {'id':rid},[]
    if path=='/api/share' and method=='POST':
        if p.get('enabled'):
            require(c.execute("SELECT id FROM runs WHERE user_id=? AND status IN ('succeeded','partial')",(uid,)).fetchone(),'순위 체크가 완료된 뒤 공유할 수 있습니다.')
        if p.get('rotate'):
            c.execute('UPDATE academies SET share_token=? WHERE user_id=?',(secrets.token_urlsafe(24),uid))
        c.execute('UPDATE academies SET sharing=? WHERE user_id=?',(int(bool(p.get('enabled'))),uid))
        return {'ok':True},[]
    if path=='/api/reports' and method=='POST':
        lock_account(c,uid)
        r=c.execute('SELECT * FROM runs WHERE id=? AND user_id=?',(str(p.get('run_id','')),uid)).fetchone()
        require(r and r['status'] in ('succeeded','partial'),'성공한 수집 결과가 있는 완료 기록을 선택해 주세요.')
        old=c.execute('SELECT * FROM reports WHERE user_id=? AND run_id=?',(uid,r['id'])).fetchone()
        if old:
            require(old['enabled'],'종료한 링크입니다. 공유 기록에서 다시 켜 주세요.',409)
            return {'share_token':old['share_token']},[]
        key=secrets.token_urlsafe(24)
        c.execute('INSERT INTO reports VALUES(?,?,?,?,?,?,1)',(secrets.token_hex(12),uid,r['id'],now(),json.dumps(public_snapshot(r),ensure_ascii=False),key))
        return {'share_token':key},[]
    if path=='/api/report-share' and method=='POST':
        report=c.execute('SELECT * FROM reports WHERE id=? AND user_id=?',(str(p.get('id','')),uid)).fetchone()
        require(report,'공유 기록을 찾을 수 없습니다.',404)
        key=secrets.token_urlsafe(24) if p.get('rotate') else report['share_token']
        c.execute('UPDATE reports SET enabled=?,share_token=? WHERE id=? AND user_id=?',(int(bool(p.get('enabled'))),key,report['id'],uid))
        return {'share_token':key},[]
    raise Problem('페이지를 찾을 수 없습니다.',404)

def application(env,start_response):
    path,method=env.get('PATH_INFO','/'),env.get('REQUEST_METHOD','GET')
    headers=[('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),('X-Frame-Options','DENY'),
        ('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),('Cache-Control','no-store')]
    status=200
    try:
        require(method in ('GET','POST'),'지원하지 않는 요청입니다.',405)
        if path.startswith('/api/'):
            headers.append(('Content-Type','application/json; charset=utf-8'))
            if method=='POST':
                require(env.get('CONTENT_TYPE','').split(';')[0].strip().lower()=='application/json','JSON 요청만 지원합니다.')
                origin=env.get('HTTP_ORIGIN','')
                if origin:
                    from urllib.parse import urlparse
                    expected=os.environ.get('PUBLIC_ORIGIN','').rstrip('/')
                    require(origin==expected if expected else urlparse(origin).netloc==env.get('HTTP_HOST',''),'허용되지 않은 요청 출처입니다.',403)
            length=int(env.get('CONTENT_LENGTH') or 0)
            require(0<=length<=65536,'요청 크기가 너무 큽니다.',413)
            p=json.loads(env['wsgi.input'].read(length) or b'{}') if method=='POST' else {}
            require(isinstance(p,dict),'요청 형식이 잘못되었습니다.')
            with connect() as c: result,extra=dispatch(c,method,path,p,env)
            headers+=extra
            body=json.dumps(result,ensure_ascii=False).encode()
        else:
            require(method=='GET','지원하지 않는 요청입니다.',405)
            assets={'/app.js':('app.js','application/javascript'),'/style.css':('style.css','text/css')}
            fn,typ=assets.get(path,('index.html','text/html'))
            require(path in assets or path in ('/','/login') or re.fullmatch(r'/s/[A-Za-z0-9_-]{20,80}',path),'페이지를 찾을 수 없습니다.',404)
            body=(ROOT/'static'/fn).read_bytes()
            headers.append(('Content-Type',typ+'; charset=utf-8'))
    except (Problem,ValueError,TypeError) as e:
        status=e.status if isinstance(e,Problem) else 400
        body=json.dumps({'error':e.text if isinstance(e,Problem) else '입력 형식을 확인해 주세요.'},ensure_ascii=False).encode()
        headers=[(k,v) for k,v in headers if k!='Content-Type']+[('Content-Type','application/json; charset=utf-8')]
    except Exception:
        # Avoid logging requests, emails, passwords, connection strings or rank data.
        print('Application request failed; private details omitted.',file=sys.stderr)
        status,body=500,b'{"error":"Service temporarily unavailable"}'
        headers=[(k,v) for k,v in headers if k!='Content-Type']+[('Content-Type','application/json; charset=utf-8')]
    headers.append(('Content-Length',str(len(body))))
    reasons={200:'OK',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',405:'Method Not Allowed',409:'Conflict',413:'Payload Too Large',429:'Too Many Requests',500:'Internal Server Error'}
    start_response(str(status)+' '+reasons.get(status,'Error'),headers)
    return [body]

def claim_worker(owner):
    stamp=time.time()
    with connect() as c:
        row=c.execute('''INSERT INTO leases(name,owner,expires) VALUES('collector',?,?)
             ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires=excluded.expires
             WHERE leases.expires<? OR leases.owner=? RETURNING owner''',
             (owner,stamp+45,stamp,owner)).fetchone()
    return bool(row)

def worker_loop(stop=None):
    stop=stop or threading.Event()
    owner=secrets.token_hex(16)
    def heartbeat():
        while not stop.wait(10):
            try:
                if not claim_worker(owner): stop.set();return
            except Exception: stop.set();return
    heartbeat_started=False
    try:
        while not stop.is_set():
            if not claim_worker(owner):
                stop.wait(5)
                continue
            if not heartbeat_started:
                threading.Thread(target=heartbeat,daemon=True).start()
                heartbeat_started=True
                with connect() as c:
                    c.execute("UPDATE runs SET status='failed',finished=?,error='수집 중 서비스가 재시작되었습니다. 다시 체크해 주세요.' WHERE status='running'",(now(),))
            with connect() as c:
                if not POSTGRES: c.execute('BEGIN IMMEDIATE')
                r=c.execute("SELECT * FROM runs WHERE status='queued' ORDER BY created,id LIMIT 1"+(' FOR UPDATE SKIP LOCKED' if POSTGRES else '')).fetchone()
                if r: c.execute("UPDATE runs SET status='running' WHERE id=?",(r['id'],))
            if not r:
                stop.wait(2)
                continue
            results,state,err=[],'failed','수집 환경이나 네이버 응답을 확인할 수 없어 완료하지 못했습니다. 잠시 후 다시 실행해 주세요.'
            # Ephemeral inputs disappear when the task completes; only results persist in DB.
            try:
                with tempfile.TemporaryDirectory(prefix='rank-task-',dir=STORE) as temp:
                    folder=Path(temp)
                    (folder/'input.json').write_text(r['config'],encoding='utf-8')
                    with (folder/'worker.log').open('w',encoding='utf-8') as log:
                        subprocess.run([sys.executable,'-u',str(ROOT/'collector.py'),str(folder)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1800)
                    results=json.loads((folder/'output.json').read_text(encoding='utf-8'))
                    good=sum(x['status']!='error' for x in results)
                    state='succeeded' if good==len(results) and good else ('partial' if good else 'failed')
                    err='' if state=='succeeded' else '일부 또는 전체 검색결과를 읽지 못했습니다. 수집 실패는 순위 밖과 구분됩니다.'
            except Exception: pass
            with connect() as c:
                lease=c.execute("SELECT owner,expires FROM leases WHERE name='collector'").fetchone()
                if lease and lease['owner']==owner and lease['expires']>time.time():
                    c.execute('UPDATE runs SET status=?,results=?,error=?,finished=? WHERE id=? AND status=?',
                        (state,json.dumps(results,ensure_ascii=False),err,now(),r['id'],'running'))
    finally:
        with connect() as c: c.execute("DELETE FROM leases WHERE name='collector' AND owner=?",(owner,))

init_db()

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--port',type=int,default=8787)
    parser.add_argument('--open',action='store_true')
    args=parser.parse_args()
    if args.worker: worker_loop()
    else:
        from wsgiref.simple_server import make_server,WSGIServer,WSGIRequestHandler
        from socketserver import ThreadingMixIn
        class ThreadedServer(ThreadingMixIn,WSGIServer): daemon_threads=True
        class QuietHandler(WSGIRequestHandler):
            def log_message(self,*args): pass
        stop=threading.Event()
        threading.Thread(target=worker_loop,args=(stop,),daemon=True).start()
        url=f'http://127.0.0.1:{args.port}/'
        print('Academy Rank: '+url,flush=True)
        if args.open:
            import webbrowser
            threading.Timer(0.5,lambda:webbrowser.open(url)).start()
        try: make_server('127.0.0.1',args.port,application,server_class=ThreadedServer,handler_class=QuietHandler).serve_forever()
        except KeyboardInterrupt: pass
        finally: stop.set()
