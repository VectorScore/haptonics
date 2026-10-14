import http.cookiejar
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

class CommunityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        os.environ['HAPTONICS_DB']=str(Path(cls.temp.name)/'test.sqlite3')
        spec=importlib.util.spec_from_file_location('community_server',Path(__file__).parents[1]/'community_server.py')
        cls.mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.mod)
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),cls.mod.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.temp.cleanup()
    def client(self): return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def req(self,client,path,data=None):
        req=urllib.request.Request(self.base+'/api/'+path,data=None if data is None else json.dumps(data).encode(),headers={} if data is None else {'Content-Type':'application/json'})
        try:
            with client.open(req) as r: return r.status,json.load(r)
        except urllib.error.HTTPError as e: return e.code,json.load(e)
    def test_flow(self):
        a,b=self.client(),self.client()
        ua=self.req(a,'register',{'email':'a@example.com','name':'Alice','password':'password123'})[1]['user']
        ub=self.req(b,'register',{'email':'b@example.com','name':'Bob','password':'password123'})[1]['user']
        self.assertEqual(self.req(a,'me')[1]['user']['id'],ua['id'])
        self.assertEqual(self.req(a,'profile/'+str(ub['id']))[1]['profile']['stats'],None)
        self.req(a,'stats',{'days':{'2026-10-15':{'rhythmMs':3600000,'breaths':2}},'meditationMs':120000,'mood':'😌'})
        self.assertIsNone(self.req(b,'profile/'+str(ua['id']))[1]['profile']['stats'])
        self.req(a,'privacy',{'share_stats':True,'share_mood':False})
        stats=self.req(b,'profile/'+str(ua['id']))[1]['profile']['stats']
        self.assertEqual(stats['listening_ms'],3600000);self.assertIsNone(stats['recent_mood'])
        group=self.req(a,'communities',{'name':'Quiet minds','description':'Hello','discoverable':False})[1]
        self.assertEqual(self.req(b,'communities')[1]['communities'],[])
        self.assertEqual(self.req(b,'messages?kind=group&target='+str(group['id']))[0],403)
        self.assertEqual(self.req(b,'join',{'code':group['code']})[0],200)
        msg=self.req(a,'messages',{'kind':'group','target':group['id'],'body':'hello shit'})[1]
        self.assertIn('＊',msg['body'])
        self.assertEqual(len(self.req(b,'messages?kind=group&target='+str(group['id']))[1]['messages']),1)
        self.req(a,'friends',{'id':ub['id']})
        self.assertEqual(self.req(a,'messages',{'kind':'direct','target':ub['id'],'body':'hi'})[0],403)
        self.req(b,'friends',{'id':ua['id']})
        self.assertEqual(self.req(a,'messages',{'kind':'direct','target':ub['id'],'body':'hi'})[0],200)
        self.assertEqual(self.req(b,'messages?kind=direct&target='+str(ua['id']))[1]['messages'][0]['body'],'hi')
        self.req(a,'logout',{})
        self.assertEqual(self.req(a,'me')[0],401)

if __name__=='__main__': unittest.main()
