"""Account isolation, self-service recovery and owner-controlled sharing."""
import io
import json
import os
import tempfile
import unittest

TEMP=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=TEMP.name
os.environ.pop('DATABASE_URL',None)
import server as app

class Client:
    def __init__(self): self.cookie='';self.csrf=''
    def call(self,path,p=None,csrf=True,origin=None):
        raw=json.dumps(p).encode() if p is not None else b''
        env={'PATH_INFO':'/api/'+path,'REQUEST_METHOD':'POST' if p is not None else 'GET',
             'CONTENT_LENGTH':str(len(raw)),'CONTENT_TYPE':'application/json','wsgi.input':io.BytesIO(raw),
             'HTTP_COOKIE':self.cookie,'HTTP_X_CSRF_TOKEN':self.csrf if csrf else '',
             'REMOTE_ADDR':'test-'+str(id(self)),'HTTP_HOST':'localhost'}
        if origin: env['HTTP_ORIGIN']=origin
        response={}
        def start(status,headers): response.update(status=int(status.split()[0]),headers=headers)
        body=b''.join(app.application(env,start))
        for k,v in response['headers']:
            if k=='Set-Cookie':self.cookie=v.split(';')[0]
        data=json.loads(body)
        if 'csrf' in data:self.csrf=data['csrf']
        return response['status'],data

class ServiceTests(unittest.TestCase):
    def setUp(self):
        with app.connect() as c:
            for table in ('reports','runs','academies','sessions','users','attempts','leases'):
                c.execute('DELETE FROM '+table)
        self.clients=[];self.recovery=[]
        for i in range(2):
            client=Client()
            status,data=client.call('register',dict(email=f'owner{i}@example.com',name=f'원장님{i}',password='test-password-123'))
            self.assertEqual(status,200)
            self.assertNotIn('role',data['user'])
            self.clients.append(client);self.recovery.append(data['recovery_code'])
            self.assertEqual(client.call('config',dict(name=f'학원{i}',blog='myacademy',place='123456789',blog_keywords=['태권도'],place_keywords=['노원 태권도']))[0],200)

    def completed_run(self,client,rank=3):
        status,out=client.call('check',{})
        self.assertEqual(status,200)
        row=dict(kind='place',keyword='노원 태권도',rank=rank,status='ranked',count=50,error='private diagnostic')
        with app.connect() as c:
            c.execute("UPDATE runs SET status='succeeded',results=?,finished=?,log=? WHERE id=?",(json.dumps([row]),app.now(),'private log',out['id']))
        return out['id']

    def test_tenants_and_retired_management_routes(self):
        a,b=self.clients
        self.assertEqual(a.call('dashboard')[1]['config']['name'],'학원0')
        self.assertEqual(b.call('dashboard')[1]['config']['name'],'학원1')
        self.assertEqual(Client().call('dashboard')[0],401)
        for endpoint in ('teacher','teacher/invite','setup','submit'):
            self.assertEqual(a.call(endpoint,{})[0],404)
        self.assertNotIn('submissions',a.call('dashboard')[1])

    def test_registration_needs_no_invite(self):
        other=Client()
        status,data=other.call('register',dict(email='free@example.com',name='새 원장님',password='test-password-123'))
        self.assertEqual(status,200)
        self.assertEqual(other.call('dashboard')[1]['config']['name'],'')
        self.assertEqual(len(data['recovery_code']),32)
        with app.connect() as c:
            stored=c.execute('SELECT recovery_hash,password FROM users WHERE email=?',('free@example.com',)).fetchone()
            self.assertNotEqual(stored['recovery_hash'],data['recovery_code'])
            self.assertNotEqual(stored['password'],'test-password-123')

    def test_csrf_and_cross_origin(self):
        a=self.clients[0]
        self.assertEqual(a.call('share',{'enabled':True},csrf=False)[0],403)
        self.assertEqual(a.call('logout',{},origin='https://evil.example')[0],403)
        self.assertEqual(Client().call('login',dict(email='owner0@example.com',password='test-password-123'),origin='https://evil.example')[0],403)

    def test_queue_duplicate_and_daily_limit(self):
        a=self.clients[0]
        self.assertEqual(a.call('check',{})[0],200)
        self.assertEqual(a.call('check',{})[0],409)
        with app.connect() as c:c.execute("UPDATE runs SET status='failed'")
        self.completed_run(a);self.completed_run(a)
        self.assertEqual(a.call('check',{})[0],429)

    def test_fixed_share_private_fields_and_snapshot(self):
        a,b=self.clients
        rid=self.completed_run(a)
        self.assertEqual(b.call('reports',{'run_id':rid})[0],400)
        shared=a.call('reports',{'run_id':rid})[1]['share_token']
        self.assertEqual(a.call('reports',{'run_id':rid})[1]['share_token'],shared)
        out=Client().call('public/'+shared)[1]
        self.assertTrue(out['fixed'])
        snap=out['snapshot']
        self.assertEqual(snap['academy_name'],'학원0')
        for key in ('config','email','blog','place','log','user_id','id'):
            self.assertNotIn(key,snap)
        self.assertNotIn('error',snap['results'][0])
        with app.connect() as c:c.execute('UPDATE runs SET results=? WHERE id=?',('[]',rid))
        self.assertEqual(Client().call('public/'+shared)[1]['snapshot']['results'][0]['rank'],3)
        self.assertNotIn('log',a.call('dashboard')[1]['runs'][0])
        self.assertEqual(b.call('dashboard')[1]['reports'],[])

    def test_fixed_share_revocation_rotation_and_ownership(self):
        a,b=self.clients;rid=self.completed_run(a)
        key=a.call('reports',{'run_id':rid})[1]['share_token']
        report=a.call('dashboard')[1]['reports'][0]
        self.assertEqual(b.call('report-share',{'id':report['id'],'enabled':False})[0],404)
        self.assertEqual(a.call('report-share',{'id':report['id'],'enabled':False})[0],200)
        self.assertEqual(Client().call('public/'+key)[0],404)
        self.assertEqual(a.call('reports',{'run_id':rid})[0],409)
        new=a.call('report-share',{'id':report['id'],'enabled':True,'rotate':True})[1]['share_token']
        self.assertNotEqual(key,new)
        self.assertEqual(Client().call('public/'+key)[0],404)
        self.assertEqual(Client().call('public/'+new)[0],200)

    def test_latest_share_does_not_publish_failed_run(self):
        a=self.clients[0]
        self.assertEqual(a.call('share',{'enabled':True})[0],400)
        self.completed_run(a)
        self.assertEqual(a.call('share',{'enabled':True})[0],200)
        key=a.call('dashboard')[1]['share_token']
        a.call('check',{})
        with app.connect() as c:c.execute("UPDATE runs SET status='failed' WHERE status='queued'")
        self.assertEqual(Client().call('public/'+key)[1]['snapshot']['status'],'succeeded')
        a.call('share',{'enabled':False})
        self.assertEqual(Client().call('public/'+key)[0],404)
        a.call('share',{'enabled':True,'rotate':True})
        self.assertEqual(Client().call('public/'+key)[0],404)

    def test_self_service_recovery_rotates_code_and_invalidates_sessions(self):
        a=self.clients[0];new=Client()
        status,data=new.call('recover',dict(email='owner0@example.com',recovery_code=self.recovery[0],password='new-password-123'))
        self.assertEqual(status,200)
        self.assertNotEqual(data['recovery_code'],self.recovery[0])
        self.assertEqual(a.call('dashboard')[0],401)
        self.assertEqual(Client().call('recover',dict(email='owner0@example.com',recovery_code=self.recovery[0],password='other-password-123'))[0],401)
        self.assertEqual(Client().call('login',dict(email='owner0@example.com',password='test-password-123'))[0],401)
        self.assertEqual(Client().call('login',dict(email='owner0@example.com',password='new-password-123'))[0],200)

    def test_password_change_and_new_recovery_code(self):
        a=self.clients[0]
        self.assertEqual(a.call('account',{'current_password':'wrong'})[0],403)
        self.assertEqual(a.call('account',{'current_password':'test-password-123','new_password':'new-password-123'})[0],200)
        self.assertEqual(a.call('dashboard')[0],200)
        self.assertEqual(Client().call('login',dict(email='owner0@example.com',password='new-password-123'))[0],200)

    def test_input_validation_and_logout(self):
        a=self.clients[0]
        self.assertEqual(a.call('config',dict(name='x',place='https://evil.example/123456',place_keywords=['x']))[0],400)
        self.assertEqual(a.call('config',dict(name='x',blog='abc',blog_keywords=['k'+str(i) for i in range(41)]))[0],400)
        self.assertEqual(a.call('logout',{})[0],200)
        self.assertEqual(a.call('dashboard')[0],401)

    def test_single_collector_lease(self):
        self.assertTrue(app.claim_worker('worker-a'))
        self.assertTrue(app.claim_worker('worker-a'))
        self.assertFalse(app.claim_worker('worker-b'))
        with app.connect() as c:c.execute("UPDATE leases SET expires=0")
        self.assertTrue(app.claim_worker('worker-b'))

    def test_queue_worker_completion_and_ephemeral_task_files(self):
        from unittest.mock import patch
        import threading,time
        from pathlib import Path
        a=self.clients[0]
        rid=a.call('check',{})[1]['id']
        folders=[]
        def fake_collect(args,**kwargs):
            folder=Path(args[-1]);folders.append(folder)
            self.assertTrue((folder/'input.json').exists())
            (folder/'output.json').write_text(json.dumps([dict(kind='blog',keyword='태권도',rank=2,status='ranked',count=10)]),encoding='utf-8')
        stop=threading.Event()
        with patch.object(app.subprocess,'run',side_effect=fake_collect):
            thread=threading.Thread(target=app.worker_loop,args=(stop,),daemon=True);thread.start()
            try:
                end=time.monotonic()+5
                while time.monotonic()<end:
                    with app.connect() as c:r=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
                    if r['status']=='succeeded':break
                    time.sleep(.02)
                self.assertEqual(r['status'],'succeeded')
                self.assertEqual(json.loads(r['results'])[0]['rank'],2)
                self.assertTrue(folders)
                self.assertFalse(folders[0].exists())
            finally:stop.set();thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

    def test_real_http(self):
        from wsgiref.simple_server import make_server,WSGIRequestHandler
        import threading,http.client
        class Quiet(WSGIRequestHandler):
            def log_message(self,*args):pass
        httpd=make_server('127.0.0.1',0,app.application,handler_class=Quiet)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=5)
            conn.request('GET','/api/health');response=conn.getresponse()
            self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read()),{'ok':True});conn.close()
            conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=5)
            conn.request('GET','/');response=conn.getresponse()
            self.assertIn('학원 랭크',response.read().decode())
            self.assertIn("script-src 'self'",response.getheader('Content-Security-Policy'));conn.close()
        finally:
            httpd.shutdown();httpd.server_close();thread.join(timeout=3)

if __name__=='__main__':unittest.main(verbosity=2)
