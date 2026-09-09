"""Exercise the actual async page loader with deliberately reordered responses."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class SearchLoadingTest(unittest.TestCase):
    def test_obsolete_responses_cannot_replace_current_query_or_append_wrong_page(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node unavailable")
        source = (Path(__file__).resolve().parents[1] / "static/index.html").read_text(encoding="utf-8")
        loader = source[source.index("    async function loadResults("):source.index("    async function loadIndex()")]
        invalidate = source[source.index("    function invalidateSearch()"):source.index("    async function apiJSON(")]
        params = source[source.index("    function searchParams("):source.index("    async function handleReadError(")]
        harness = '''
const PAGE_SIZE = 30;
const state = {query:"old",sources:new Set(),view:"sessions",range:"all",sort:"relevance",revision:"r1",records:[],groups:[]};
const elements = new Map();
const $ = (key) => { if (!elements.has(key)) elements.set(key,{setAttribute(){},innerHTML:"",textContent:"",disabled:false}); return elements.get(key); };
const copy = {readingIndex:"loading",searchFailed:"failed"};
let searchGeneration = 0, searchController = null, renders = 0, errors = 0;
const render = () => { renders++; };
const handleReadError = async () => { errors++; };
const requests = [];
const apiJSON = (path) => new Promise((resolve,reject) => requests.push({path,resolve,reject}));
const response = (ids,next=null) => ({records:ids.map(id=>({id})),total:45,total_records:45,total_projects:1,next_offset:next});
'''
        exercise = '''
const old = loadResults();
state.query = "new";
const current = loadResults();
requests[1].resolve(response(Array.from({length:30},(_,i)=>"new-"+i),30));
await current;
requests[0].resolve(response(["obsolete"]));
await old;
const afterReorder = state.records.length === 30 && state.records[0].id === "new-0" && renders === 1;
const more = loadResults(true);
const url = new URL(requests[2].path,"http://localhost");
requests[2].resolve(response(Array.from({length:15},(_,i)=>"new-"+(30+i))));
await more;
const pagination = state.records.length === 45 && new Set(state.records.map(r=>r.id)).size === 45 && state.nextOffset === null && url.searchParams.get("offset") === "30" && url.searchParams.get("revision") === "r1";
const interrupted = loadResults();
invalidateSearch();
requests[3].reject(new Error("obsolete network error"));
await interrupted;
console.log(JSON.stringify({afterReorder,pagination,obsoleteErrorIgnored:errors===0}));
'''
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "loader.mjs"
            script.write_text(harness + invalidate + params + loader + exercise, encoding="utf-8")
            result = subprocess.run([node, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"afterReorder": True, "pagination": True, "obsoleteErrorIgnored": True})
