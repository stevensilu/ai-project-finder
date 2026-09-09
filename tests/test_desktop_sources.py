from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

import app
import desktop_sources as sources


class DesktopSourcesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def jsonl(self, name, rows):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')
        return path

    def test_workbuddy_cleans_context_before_clipping_and_keeps_late_requests(self):
        path = self.jsonl('projects/atlas/session-abc.jsonl', [
            {'type': 'message', 'role': 'user', 'id': 'u1', 'sessionId': 'session-abc', 'cwd': '/srv/projects/atlas', 'timestamp': 1788846600000,
             'content': [{'type': 'input_text', 'text': '<system-reminder>' + 'x' * 1000 + '</system-reminder>opening request'}]},
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'irrelevant assistant text'}]},
            {'type': 'message', 'role': 'user', 'id': 'u2', 'content': [{'type': 'input_text', 'text': 'latest report.xlsx request'}]},
            {'type': 'message', 'role': 'user', 'id': 'u2', 'content': [{'type': 'input_text', 'text': 'latest report.xlsx request'}]},
            {'type': 'ai-title', 'aiTitle': 'Atlas report'},
        ])
        original = path.read_bytes()
        record = app.parse_desktop_source('workbuddy', path, 80)[0]
        self.assertEqual(record['message_count'], 2)
        self.assertEqual(record['truncated_turns'], 0)
        self.assertIn('opening request', record['excerpt'])
        self.assertIn('latest report.xlsx', record['excerpt'])
        self.assertNotIn('irrelevant', record['excerpt'])
        self.assertEqual(record['title'], 'Atlas report')
        self.assertEqual(app.build_open_target(record, 'session')[0], 'workbuddy://chat/session-abc')
        self.assertEqual(path.read_bytes(), original)

    def test_workbuddy_deleted_tasks_and_metadata_changes(self):
        path = self.jsonl('projects/atlas/session-abc.jsonl', [{'type':'message','role':'user','content':'request','sessionId':'session-abc'}])
        db_path = self.root / 'workbuddy.db'
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute('CREATE TABLE sessions (id TEXT, title TEXT, deleted_at INTEGER)')
            db.execute('INSERT INTO sessions VALUES (?, ?, ?)', ('session-abc', 'Renamed', None))
        before = sources.fingerprint('workbuddy', path)
        self.assertEqual(sources.read_workbuddy(path)[0]['title'], 'Renamed')
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute('UPDATE sessions SET deleted_at=1')
        self.assertNotEqual(before, sources.fingerprint('workbuddy', path))
        self.assertEqual(sources.read_workbuddy(path), [])

    def qwen_database(self):
        path = self.root / 'agents.db'
        db = sqlite3.connect(path)
        db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE chats (id TEXT, name TEXT, project_id TEXT, worktree_path TEXT, deleted_at INTEGER, created_at INTEGER, updated_at INTEGER);
          CREATE TABLE projects (id TEXT, path TEXT);
          CREATE TABLE sub_chats (id TEXT, chat_id TEXT, session_id TEXT, updated_at INTEGER, messages TEXT);
          CREATE TABLE messages (sub_chat_id TEXT, role TEXT, parts TEXT, searchable_text TEXT, sequence INTEGER);
          INSERT INTO projects VALUES ('proj', '/srv/projects/atlas');
          INSERT INTO chats VALUES ('chat-001','Atlas renamed','proj','/srv/projects/atlas',NULL,1788846600,1788846601);
          INSERT INTO sub_chats VALUES ('sub-001','chat-001','session-001',1788846601,'[]');
        ''')
        db.execute('INSERT INTO messages VALUES (?,?,?,?,?)', ('sub-001','user',json.dumps([{'type':'text','text':'first request'}]),None,1))
        db.commit()
        self.addCleanup(db.close)
        return path, db

    def test_qwen_reads_committed_wal_and_invalidates_cache_on_new_turn(self):
        path, db = self.qwen_database()
        before = sources.fingerprint('qwenwork', path)
        db.execute('INSERT INTO messages VALUES (?,?,?,?,?)', ('sub-001','user',json.dumps([{'type':'text','text':'late launch spreadsheet'}]),None,2))
        db.commit()
        self.assertNotEqual(before, sources.fingerprint('qwenwork', path))
        original = path.read_bytes()
        record = app.parse_desktop_source('qwenwork', path, 50000)[0]
        self.assertEqual(record['message_count'], 2)
        self.assertIn('late launch spreadsheet', record['excerpt'])
        self.assertEqual(record['title'], 'Atlas renamed')
        self.assertEqual(record['project'], 'atlas')
        target, _ = app.build_open_target(record, 'session')
        self.assertEqual(target, 'qwenwork-cn://notification-click?chatId=chat-001&subChatId=sub-001')
        self.assertEqual(path.read_bytes(), original)
        db.execute('UPDATE chats SET deleted_at=1'); db.commit()
        self.assertEqual(sources.read_qwenwork(path), [])

    def dsh_rows(self):
        return [
            {'type':'session','id':'session-abc','cwd':'/srv/projects/atlas','createdAt':1788846600000},
            {'type':'user/message','time':1788846601000,'data':{'id':'u1','source':{'kind':'user'},'content':[{'type':'text','text':'opening request'}]}},
            {'type':'session/title','data':{'title':'Harness report'}},
            {'type':'user/message','time':1788846602000,'data':{'id':'u2','source':{'kind':'user'},'content':[{'type':'text','text':'latest launch request'}]}},
            {'type':'user/message','data':{'id':'u3','source':{'kind':'subagent'},'content':'injected agent completion'}},
        ]

    def test_harness_indexes_user_turns_and_skips_subagents(self):
        path = self.jsonl('sessions/atlas/session-abc/session.jsonl', self.dsh_rows())
        record = app.parse_desktop_source('deepseek-harness', path, 50000)[0]
        self.assertEqual(record['message_count'], 2)
        self.assertIn('latest launch', record['excerpt'])
        self.assertNotIn('injected', record['excerpt'])
        self.assertEqual(record['title'], 'Harness report')
        self.assertEqual(record['open_scope'], 'app')
        self.assertEqual(record['updated_at'], '2026-09-08T05:50:02+00:00')
        with patch.object(app, 'load_config', return_value={'deepseek_harness_url':'http://127.0.0.1:3080/'}):
            self.assertEqual(app.build_open_target(record, 'session')[0], 'http://127.0.0.1:3080/')
        rows=self.dsh_rows(); rows[0]['parentSession']='parent-session'
        path=self.jsonl('child/session.jsonl', rows)
        self.assertEqual(sources.read_deepseek(path), [])

    def test_harness_concatenated_zstd_frames(self):
        try:
            from compression import zstd
        except ImportError:
            self.skipTest('Python 3.14 Zstd not available')
        rows=self.dsh_rows()
        path=self.root/'session.jsonl.zstd'
        path.write_bytes(b''.join(zstd.compress((json.dumps(r)+'\n').encode()) for r in rows))
        self.assertEqual(sources.read_deepseek(path)[0]['prompts'], ['opening request','latest launch request'])

    def test_source_discovery_skips_backup_and_helper_files(self):
        self.jsonl('sessions/atlas/main/session.jsonl', self.dsh_rows())
        self.jsonl('sessions/atlas/main/session.jsonl.bak', self.dsh_rows())
        self.assertEqual(len(list(sources.source_files('deepseek-harness', self.root))), 1)
        self.jsonl('projects/atlas/subagents/agent-abc.jsonl', [])
        self.jsonl('projects/atlas/main.jsonl', [])
        self.assertEqual(len(list(sources.source_files('workbuddy', self.root))), 1)

    def test_sources_can_be_disabled_and_old_config_discovers_new_sources(self):
        self.assertEqual(app.resolved_source_paths({'sources':{'workbuddy':False}})['workbuddy'], [])
        self.assertEqual(app.resolved_source_paths({'sources':{'deepseek-harness':[]}})['deepseek-harness'], [])
        self.assertTrue(app.resolved_source_paths({'sources':{'codex':'auto'}})['workbuddy'])
        with patch.object(app, 'IS_WINDOWS', True), patch.dict('os.environ', {'APPDATA':'C:/Users/Example/AppData/Roaming','LOCALAPPDATA':'C:/Users/Example/AppData/Local'}):
            paths=app.automatic_source_paths()
        self.assertTrue(any(p.as_posix().endswith('QwenWorkCN/data/agents.db') for p in paths['qwenwork']))
        self.assertTrue(any(str(p).endswith('DoubaoWork') for p in paths['doubao-work']))

    def test_manual_trace_with_native_source_label_stays_a_web_trace(self):
        for source in app.DEFAULT_CONFIG['sources']:
            record={'source':source,'manual':True,'session_id':'trace-001','session_path':'https://example.com/session/1'}
            self.assertEqual(app.build_open_target(record,'session'),('https://example.com/session/1','web'))

    def test_manual_kimi_trace_does_not_start_the_kimi_runtime(self):
        record={'source':'kimi','manual':True,'session_id':'trace-001','session_path':'https://example.com/session/1'}
        with patch.object(app,'ensure_kimi_web_origin',side_effect=AssertionError('started Kimi')), patch.object(app,'system_open_target') as opened, patch.object(app,'record_open_event'):
            self.assertEqual(app.launch_record(record,'session'),'web')
        opened.assert_called_once_with('https://example.com/session/1')

    def test_harness_origin_and_native_id_validation(self):
        path=self.jsonl('session.jsonl',self.dsh_rows())
        record=app.parse_desktop_source('deepseek-harness',path,50000)[0]
        for target in ['https://example.com/','http://127.0.0.1.evil.invalid/','file:///tmp/file','http://user:secret@localhost/']:
            with patch.object(app,'load_config',return_value={'deepseek_harness_url':target}):
                with self.assertRaises(ValueError):app.build_open_target(record,'session')
        record['session_id']='../../oops'
        with self.assertRaises(ValueError):app.build_open_target(record,'session')

    def test_doubao_cache_merges_revisions_and_excludes_bot_welcome(self):
        user={'conversation_id':'conv-001','message_id':'user-1','user_type':1,'message_body_version':'1','index_in_conv':'1','create_time':'1788846600','content':'[{"content":{"text_block":{"text":"first draft"}}}]'}
        welcome={'conversation_id':'conv-002','message_id':'bot-1','user_type':2,'content':'Welcome','index_in_conv':'1'}
        values=[{'data':{'cells':[
            {'conversation':{'conversation_id':'conv-001','name':'Atlas report','messages':[user]}},
            {'conversation':{'conversation_id':'conv-002','name':'Built-in bot','messages':[welcome]}}
        ]}}, {'conversation_id':'conv-001','name':'Renamed report','conv_version':'2','messages':[{**user,'message_body_version':'2','content':'[{"content":{"text_block":{"text":"corrected draft"}}}]'}]}]
        rows=sources.doubao_conversations(values)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['prompts'],['corrected draft'])
        self.assertEqual(rows[0]['title'],'Renamed report')
        self.assertEqual(rows[0]['coverage'],'cached')

    def test_indexeddb_live_selection_honors_tombstones_and_compaction(self):
        from types import SimpleNamespace as Obj
        from vendor.chromium.storage_formats.ccl_leveldb import KeyState
        (self.root/'CURRENT').write_text('MANIFEST-000001\n')
        class Manifest:
            def __init__(self, path): pass
            def __iter__(self):
                yield Obj(new_files=[Obj(file_no=4),Obj(file_no=5)],deleted_files=[],log_number=6,prev_log_number=0)
                yield Obj(new_files=[],deleted_files=[Obj(file_no=4)],log_number=None,prev_log_number=None)
            def close(self): pass
        def row(key,seq,live,file):
            return Obj(user_key=key,seq=seq,state=KeyState.Live if live else KeyState.Deleted,origin_file=self.root/file)
        rows=[row(b'old',1,True,'000004.ldb'),row(b'removed',2,True,'000005.ldb'),row(b'keep',3,True,'000005.ldb'),
              row(b'removed',4,False,'000006.log'),row(b'keep',5,True,'000006.log'),row(b'obsolete-log',9,True,'000003.log')]
        raw=Obj(in_dir_path=self.root,iterate_records_raw=lambda: iter(rows))
        with patch('vendor.chromium.storage_formats.ccl_leveldb.ManifestFile',Manifest):
            result=sources.current_leveldb_records(raw)
        self.assertEqual([(r.user_key,r.seq) for r in result],[(b'keep',5)])

    def test_numeric_string_timestamps(self):
        self.assertEqual(app.iso_from_value('1788846600000'), app.iso_from_value(1788846600))

    def test_cached_database_records_status_and_failure_isolation(self):
        path, db=self.qwen_database()
        data=self.root/'data';data.mkdir()
        config={'sources':{name:False for name in app.DEFAULT_CONFIG['sources']}}
        config['sources'].update({'qwenwork':str(path),'workbuddy':str(self.root/'empty')})
        (self.root/'empty').mkdir()
        for name,value in [('DATA_DIR',data),('INDEX_FILE',data/'index.json'),('PARSE_CACHE_FILE',data/'parse-cache.json'),('MANUAL_FILE',data/'manual.json'),('PROJECTS_FILE',data/'projects.json'),('DEMO_MODE',False),('INDEX_CACHE',{'key':None,'payload':None})]:
            p=patch.object(app,name,value);p.start();self.addCleanup(p.stop)
        with patch.object(app,'load_config',return_value=config),patch.object(app,'load_claude_desktop_session_map',return_value={}):
            first=app.build_index()
            self.assertEqual(first['summary']['records'],1)
            self.assertEqual(first['source_status']['workbuddy']['state'],'empty')
            self.assertEqual(first['source_status']['codex']['state'],'disabled')
            with patch.object(sources,'read_qwenwork',side_effect=AssertionError('reread unchanged DB')):
                # READERS holds the original function, patch the actual registration.
                with patch.dict(sources.READERS,{'qwenwork':lambda _:self.fail('reread unchanged DB')}):
                    second=app.build_index()
            self.assertEqual(second['summary']['records'],1)
            self.assertEqual(second['reused_records'],1)
            db.execute("UPDATE chats SET name='New title'");db.commit()
            third=app.build_index()
            self.assertEqual(third['records'][0]['title'],'New title')
            with patch.dict(sources.READERS,{'qwenwork':lambda _:(_ for _ in ()).throw(ValueError('damaged'))}),patch.object(app,'load_parse_cache',return_value={}):
                failed=app.build_index()
            self.assertEqual(failed['source_status']['qwenwork']['state'],'error')
            self.assertEqual(failed['source_status']['qwenwork']['errors'],1)
