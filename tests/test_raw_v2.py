import csv
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from importlib.util import spec_from_file_location, module_from_spec

SCRIPT=Path(__file__).resolve().parents[1]/'raw_v2'/'collector.py'
VERIFY=Path(__file__).resolve().parents[1]/'raw_v2'/'verify.py'
spec=spec_from_file_location('rawcollector',SCRIPT); m=module_from_spec(spec);spec.loader.exec_module(m)
vspec=spec_from_file_location('rawverify',VERIFY); v=module_from_spec(vspec);vspec.loader.exec_module(v)


def source(path):
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f);w.writerow(['account_key','username','country','image_url'])
        for country,count in [('KR',100),('US',101),('JP',93)]:
            for i in range(1,count+1):w.writerow([f'{country}_{i:03}',f'{country.lower()}_nail_{i}',country,'https://example.invalid/x'])


def post(n,likes_marker='value'):
    x={'id':str(10**17+n),'username':'kr_nail_1','caption':'  원문\n✨  ','timestamp':'2026-10-18T01:02:03+0000','permalink':'https://example.invalid/p',
       'media_type':'IMAGE','media_product_type':'FEED','comments_count':0}
    if likes_marker=='value':x['like_count']=n
    elif likes_marker=='null':x['like_count']=None
    return x


def payload(ids,after='NEXT',followers=200):
    return {'business_discovery':{'id':'987654321','username':'kr_nail_1','followers_count':followers,'media_count':100,
        'media':{'data':[post(n) for n in ids],'paging':{'cursors':{'after':after}} if after else {}}}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.dir=Path(self.temp.name);self.src=self.dir/'accounts.csv';source(self.src)
        self.rows,self.sha=m.load_accounts(self.src)
        self.db=m.connect(self.dir/'new_db'/'collection.sqlite',self.rows,self.sha)
        self.addCleanup(self.db.close)
    def test_roster_294_and_tamper(self):
        self.assertEqual((len(self.rows),self.rows[-1]['key']),(294,'JP_093'))
        with self.src.open('a', encoding='utf-8') as f:f.write('bogus')
        with self.assertRaises(ValueError):m.load_accounts(self.src)
    def test_db_hash_immutability(self):
        with self.assertRaises(ValueError):m.connect(self.dir/'new_db'/'collection.sqlite',self.rows,'different')
    def test_request_cursor(self):
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone());a['backfill_cursor']='CURSOR_1'
        op=m.make_op(a,'123','history')
        self.assertIn('after%28CURSOR_1%29',op['relative_url'])
        self.assertEqual(op['method'],'GET')
    def test_first_page_and_reobservation(self):
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone())
        self.assertEqual(m.insert_success(self.db,a,'front',payload(range(1,6)),'R1','r1',m.now(),365),(5,5,True))
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone())
        self.assertEqual(a['backfill_cursor'],'NEXT')
        p=payload(range(1,6),after='N2');p['business_discovery']['media']['data'][0]['like_count']=123
        self.assertEqual(m.insert_success(self.db,a,'front',p,'R2','r2',m.now(),365)[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM posts').fetchone()[0],5)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM post_observations').fetchone()[0],10)
        self.assertEqual(self.db.execute('SELECT like_count FROM post_observations WHERE request_id=? AND post_id=?',('r2',str(10**17+1))).fetchone()[0],123)
    def test_like_missing_vs_zero_and_unicode(self):
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone())
        p=payload([1,2,3],after=None)
        p['business_discovery']['media']['data']=[post(1,'missing'),post(2,'null'),post(3,'value')]
        m.insert_success(self.db,a,'front',p,'R1','r1',m.now(),365)
        obs=list(self.db.execute('SELECT like_present,like_count FROM post_observations ORDER BY post_id'))
        self.assertEqual([(r[0],r[1]) for r in obs],[(0,None),(0,None),(1,3)])
        self.assertEqual(self.db.execute('SELECT caption FROM posts LIMIT 1').fetchone()[0],'  원문\n✨  ')
        fields=list(self.db.execute("SELECT status FROM field_observations WHERE field_name='like_count' AND resource_type='posts' ORDER BY item_id"))
        self.assertEqual([x[0] for x in fields],['absent','null','returned'])
    def test_carousel_children_only(self):
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone())
        p=payload([1],after=None);x=p['business_discovery']['media']['data'][0];x['media_type']='CAROUSEL_ALBUM'
        x['children']={'data':[{'id':str(10**18+i),'media_type':'IMAGE','media_url':f'https://ex/{i}'} for i in (1,2,3)]}
        m.insert_success(self.db,a,'front',p,'R1','r1',m.now(),365)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM posts').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM post_media').fetchone()[0],3)
        self.assertEqual([x[0] for x in self.db.execute('SELECT item_order FROM post_media ORDER BY item_order')],[1,2,3])
    def test_front_gap_resumes(self):
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone());m.insert_success(self.db,a,'front',payload(range(1,6)),'R1','r1',m.now(),365)
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone())
        m.insert_success(self.db,a,'front',payload(range(6,11),after='GAP'),'R2','r2',m.now(),365)
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone())
        self.assertEqual((a['catchup_cursor'],a['catchup_anchor']),('GAP',str(10**17+1)))
        self.assertEqual([phase for phase,r in m.select_tasks(self.db,'balanced',294) if r['account_key']=='KR_001'],['catchup'])
    def test_corrupt_cursor_is_rejected_no_advance(self):
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone())
        p=payload(range(1,6),after='BAD!')
        with self.assertRaises(ValueError):m.insert_success(self.db,a,'front',p,'R1','r1',m.now(),365)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM posts').fetchone()[0],0)
    def test_raw_redaction(self):
        original={'paging':{'next':'https://graph.facebook.com/path?access_token=SECRET&after=XYZ'},'caption':'안녕 😀','auth':'SECRET','access_token':'SECRET'}
        path=m.persist_response(self.dir,'RUN','r1',original,'SECRET');stored=path.read_text(encoding='utf-8')
        self.assertNotIn('SECRET',stored);self.assertIn('안녕 😀',stored)
    def test_offline_dryrun_no_write(self):
        args=Namespace(source=self.src,env_file=None,output_dir=self.dir/'untouched',live=False,mode='balanced',batch_size=5,
                       max_batches=1,limit=5,window_days=7,history_days=365,cycle_minutes=70,stop_usage=70,extended_fields=False)
        self.assertEqual(m.run(args),0);self.assertFalse(args.output_dir.exists())
    def test_fake_live_one_batch_and_manifest(self):
        env=self.dir/'local.env';env.write_text('META_ACCESS_TOKEN=SECRET\nMETA_IG_USER_ID=12345\nMETA_GRAPH_VERSION=v26.0', encoding='utf-8')
        def fake(version,token,ops):
            self.assertEqual(token,'SECRET');self.assertEqual(len(ops),5)
            return 200,[{'code':200,'body':json.dumps(payload(range(i*5+1,i*5+6))),'headers':[]} for i,_ in enumerate(ops)],{'X-App-Usage':'{"total_time":3}'}
        args=Namespace(source=self.src,env_file=env,output_dir=self.dir/'out',live=True,mode='balanced',batch_size=5,
                       max_batches=1,limit=5,window_days=7,history_days=365,cycle_minutes=70,stop_usage=70,extended_fields=False)
        self.assertEqual(m.run(args,fake),0)
        runs=list((args.output_dir/'runs').iterdir());self.assertEqual(len(runs),1)
        manifest=json.loads((runs[0]/'run_manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['run']['successes'],5)
        self.assertEqual(manifest['row_count']['posts.csv'],25)
        self.assertFalse('SECRET' in ''.join(p.read_text(encoding='utf-8') for p in (runs[0]/'raw').iterdir()))
        self.assertEqual(v.verify(args.output_dir)['checks'],'PASS')
        self.assertEqual(m.run(args,fake),0)  # 70m cooldown prevents requests
    def test_seven_day_bucket_tracks_oldest(self):
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone())
        p=payload([1],after=None);p['business_discovery']['media']['data'][0]['timestamp']='2026-10-08T00:00:00+0000'
        m.insert_success(self.db,a,'front',p,'R1','r1',m.now(),365,window_days=7)
        row=self.db.execute("SELECT window_start,window_end FROM accounts WHERE account_key='KR_001'").fetchone()
        self.assertIsNotNone(row[0]);self.assertIsNotNone(row[1])
        self.assertEqual((m.parse_date(row[1]+'T00:00:00+00:00')-m.parse_date(row[0]+'T00:00:00+00:00')).days,7)
    def test_cross_account_duplicate_id_rolls_back(self):
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone())
        b=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_002'").fetchone())
        m.insert_success(self.db,a,'front',payload([1],after=None),'R1','a',m.now(),365)
        with self.assertRaisesRegex(ValueError,'another account'):
            m.insert_success(self.db,b,'front',payload([1],after=None),'R1','b',m.now(),365)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM post_observations').fetchone()[0],1)
    def test_carousel_more_children_is_partial(self):
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone())
        p=payload([1],after=None);x=p['business_discovery']['media']['data'][0];x['media_type']='CAROUSEL_ALBUM'
        x['children']={'data':[{'id':str(10**18+1),'media_type':'IMAGE'}], 'paging':{'cursors':{'after':'Y'}}}
        m.insert_success(self.db,a,'front',p,'R1','r1',m.now(),365)
        self.assertEqual(self.db.execute('SELECT coverage FROM carousel_status').fetchone()[0],'partial')
    def test_rate_limit_halts_without_cursor_advance(self):
        env=self.dir/'local.env';env.write_text('META_ACCESS_TOKEN=SECRET\nMETA_IG_USER_ID=12345', encoding='utf-8')
        args=Namespace(source=self.src,env_file=env,output_dir=self.dir/'quota',live=True,mode='balanced',batch_size=5,
                       max_batches=1,limit=5,window_days=7,history_days=365,cycle_minutes=70,stop_usage=70,extended_fields=False)
        def fake(v,t,ops):
            return 200,[{'code':400,'body':json.dumps({'error':{'code':4,'message':'limit'}})} for _ in ops],{'X-App-Usage':'{"total_time":92}'}
        self.assertEqual(m.run(args,fake),2)
        with sqlite3.connect(args.output_dir/'collection.sqlite') as d:
            self.assertEqual(d.execute('SELECT count(*) FROM posts').fetchone()[0],0)
            self.assertEqual(d.execute('SELECT failures FROM runs').fetchone()[0],5)
    def test_bad_hash_detected_by_verifier(self):
        env=self.dir/'local.env';env.write_text('META_ACCESS_TOKEN=SECRET\nMETA_IG_USER_ID=12345', encoding='utf-8')
        args=Namespace(source=self.src,env_file=env,output_dir=self.dir/'forcheck',live=True,mode='balanced',batch_size=1,
                       max_batches=1,limit=5,window_days=7,history_days=365,cycle_minutes=70,stop_usage=70,extended_fields=False)
        def fake(version,token,ops):return 200,[{'code':200,'body':json.dumps(payload([1],after=None))}],{'X-App-Usage':'{"total_time":3}'}
        self.assertEqual(m.run(args,fake),0)
        run=next((args.output_dir/'runs').iterdir()); (run/'posts.csv').write_text('corrupted', encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'SHA mismatch'):v.verify(args.output_dir)
    def test_one_process_lock(self):
        base=self.dir/'concurrent';base.mkdir()
        with m.process_lock(base):
            with self.assertRaises(ValueError):
                with m.process_lock(base):pass
    def test_per_post_field_diagnostics_distinguish_not_tested(self):
        a=dict(self.db.execute("SELECT * FROM accounts WHERE account_key='KR_001'").fetchone())
        m.insert_success(self.db,a,'front',payload([1],after=None),'R1','r1',m.now(),365)
        vals=dict(self.db.execute("SELECT field_name,status FROM field_observations WHERE resource_type='posts' AND item_id=?",(str(10**17+1),)))
        self.assertEqual(vals['like_count'],'returned')
        self.assertEqual(vals['view_count'],'not_tested')
    def test_csv_optional_fields_headers_are_present(self):
        env=self.dir/'local.env';env.write_text('META_ACCESS_TOKEN=SECRET\nMETA_IG_USER_ID=12345', encoding='utf-8')
        args=Namespace(source=self.src,env_file=env,output_dir=self.dir/'headers',live=True,mode='balanced',batch_size=1,
                       max_batches=1,limit=5,window_days=7,history_days=365,cycle_minutes=70,stop_usage=70,extended_fields=False)
        def fake(v,t,ops):return 200,[{'code':200,'body':json.dumps(payload([1],after=None))}],{'X-App-Usage':'{"total_time":2}'}
        self.assertEqual(m.run(args,fake),0)
        folder=next((args.output_dir/'runs').iterdir())
        with (folder/'posts.csv').open(encoding='utf-8-sig',newline='') as fp:
            rows=list(csv.DictReader(fp))
        self.assertEqual(rows[0]['shortcode'],'')
        self.assertEqual(rows[0]['view_count'],'')
        with (folder/'post_media.csv').open(encoding='utf-8-sig',newline='') as fp:
            self.assertIn('collected_at',next(csv.reader(fp)))
        self.assertEqual(v.verify(args.output_dir)['checks'],'PASS')
    def test_outer_error_safe(self):
        self.assertEqual(m.usage({'X-App-Usage':'{"total_time":72}'}),72.0)
        self.assertIsNone(m.usage({}))

if __name__=='__main__':unittest.main()
