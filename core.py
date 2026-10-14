"""Local state, verified journal metrics and Windows per-user secret protection."""
from __future__ import annotations
import base64, csv, ctypes, hashlib, json, os, re, sqlite3, sys, threading
from ctypes import wintypes
from datetime import date, datetime
from pathlib import Path

VERSION = '1.4.1'
APP = '熔盐文献日课'
DEFAULTS = dict(base_url='https://api.deepseek.com', model='deepseek-flash', api_secret='',
                openalex_secret='', email='', days=90, daily_count=3, max_pages=8,
                min_if=10.0, metric_year=0, nuclear_only=False, strict_pages=False, search_pages=3,
                schedule=True, schedule_time='09:00', autostart=False,
                ai_ranking=True, daily_token_limit=1000000, last_auto_day='', last_success='',
                topic_mode='molten', research_topic='',
                queries=['molten salt electrochemical', 'molten salt uranium',
                         'molten salt nuclear electrorefining', 'molten salt electrodeposition'])
METRICS = [
 dict(journal='Nature Communications', issn='2041-1723', year=2024, value=15.7,
      source='https://www.nature.com/ncomms/journal-information', checked='2026-10-12'),
 dict(journal='Advanced Science', issn='2198-3844', year=2024, value=14.1,
      source='https://onlinelibrary.wiley.com/journal/21983844', checked='2026-10-12'),
 dict(journal='Angewandte Chemie International Edition', issn='1521-3773;1433-7851', year=2024, value=16.9,
      source='https://onlinelibrary.wiley.com/page/journal/15213773/homepage/productinformation.html', checked='2026-10-12'),
]
from metric_data import BUNDLED_METRICS
METRICS += BUNDLED_METRICS
SEEDS = [
 dict(doi='10.1038/s41467-021-26119-9', title='In-situ anodic precipitation process for highly efficient separation of aluminum alloys',
      journal='Nature Communications', issn=['2041-1723'], date='2021-10-01', pages=6,
      abstract='Electrochemical separation of uranium aluminum alloys by in-situ anodic precipitation in molten NaAlCl4; relevant to spent nuclear fuel pyroprocessing.',
      url='https://doi.org/10.1038/s41467-021-26119-9', pdf_url='https://www.nature.com/articles/s41467-021-26119-9.pdf',
      source='人工核实的入门文献（摘要栏为导读，非原文摘要）', seed=True),
 dict(doi='10.1002/advs.202502345', title='Operando Characterization of Uranium Dendrite Growth in High-Temperature Molten Salt Electrochemistry by Synchrotron X-Ray Tomography and Diffraction',
      journal='Advanced Science', issn=['2198-3844'], date='2025-06-11', pages=12,
      abstract='Operando synchrotron X-ray tomography and diffraction reveal uranium dendrite electrodeposition in LiCl-KCl molten salt, relevant to spent nuclear fuel pyroprocessing.',
      url='https://doi.org/10.1002/advs.202502345', pdf_url='https://advanced.onlinelibrary.wiley.com/doi/pdf/10.1002/advs.202502345',
      source='人工核实的入门文献（摘要栏为导读，非原文摘要）', seed=True),
]

def home_dir():
    override=os.environ.get('MOLTEN_DAILY_HOME')
    return Path(override) if override else (Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent)/'userdata'

def canonical_doi(s):
    s=re.sub(r'^https?://(?:dx\.)?doi.org/','',str(s).strip(),flags=re.I)
    s=re.sub(r'^doi:\s*','',s,flags=re.I)
    return s.lower().rstrip('.')

def safe_error(e):
    s=str(e)
    s=re.sub(r'(?i)(sk-[A-Za-z0-9_-]+|Bearer\s+\S+)', '[密钥已隐藏]', s)
    s=re.sub(r'(?i)(api_key=)[^&\s]+',r'\1[隐藏]',s)
    return s[:600]

class Blob(ctypes.Structure):
    _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_byte))]

def _crypt(data:bytes, decrypt=False, machine=False):
    if os.name!='nt': raise RuntimeError('密钥加密需要 Windows DPAPI。')
    buf=ctypes.create_string_buffer(data); inp=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_byte))); out=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True); kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    if not fn(ctypes.byref(inp),None,None,None,None,1|(4 if machine else 0),ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(),'Windows 密钥解密失败。请重新输入密钥。' if decrypt else 'Windows 密钥加密失败。')
    try: return ctypes.string_at(out.pbData,out.cbData)
    finally:
        kernel.LocalFree.argtypes=[ctypes.c_void_p]; kernel.LocalFree(out.pbData)

def protect(s):
    if not s:return ''
    try:return 'user:'+base64.b64encode(_crypt(s.encode())).decode()
    except OSError as e:
        # Restricted Windows execution tokens can lack a current-user master key.
        # Machine-bound DPAPI still protects the key at rest; disclose its scope.
        if e.errno not in (2,5,1312):raise
        return 'machine:'+base64.b64encode(_crypt(s.encode(),machine=True)).decode()
def unprotect(s):
    if not s:return ''
    scope,blob=s.split(':',1) if ':' in s else ('user',s)
    return _crypt(base64.b64decode(blob),True,machine=scope=='machine').decode()

class Store:
    def __init__(self, root=None):
        self.root=Path(root or home_dir()); self.root.mkdir(parents=True,exist_ok=True)
        for sub in ('pdfs','translations','exports'): (self.root/sub).mkdir(exist_ok=True)
        self.lock=threading.RLock(); self.path=self.root/'library.sqlite3'
        with self.db() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS papers(doi TEXT PRIMARY KEY, payload TEXT NOT NULL,
                added TEXT NOT NULL, seen INTEGER DEFAULT 0, favorite INTEGER DEFAULT 0, notified INTEGER DEFAULT 0,
                pdf TEXT DEFAULT '', note TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY,tokens INTEGER DEFAULT 0,calls INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events(ts TEXT,kind TEXT,message TEXT);
                CREATE TABLE IF NOT EXISTS collections(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS paper_collections(doi TEXT NOT NULL,collection_id INTEGER NOT NULL,
                    PRIMARY KEY(doi,collection_id));
                CREATE TABLE IF NOT EXISTS assessments(doi TEXT,topic TEXT,evidence TEXT,payload TEXT,
                    PRIMARY KEY(doi,topic,evidence));
                CREATE TABLE IF NOT EXISTS search_papers(doi TEXT PRIMARY KEY,payload TEXT NOT NULL);''')
            columns={r['name'] for r in c.execute('PRAGMA table_info(papers)')}
            if 'deleted' not in columns:c.execute('ALTER TABLE papers ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
            if 'deleted_at' not in columns:c.execute("ALTER TABLE papers ADD COLUMN deleted_at TEXT DEFAULT ''")
        if not (self.root/'settings.json').exists(): self.save_settings(dict(DEFAULTS))
        if not (self.root/'journal_metrics.csv').exists(): self.write_metrics(METRICS)
        self.merge_metrics(METRICS)
        settings=self.settings()
        if settings.get('search_schema',0)<2:self.update_settings(metric_year=0,search_schema=2)
        if settings.get('budget_schema',0)<2:
            self.update_settings(daily_token_limit=max(1000000,int(settings['daily_token_limit'])),budget_schema=2,schedule=False)
        self.budget_handler=None
    def db(self):
        c=sqlite3.connect(self.path,timeout=20); c.row_factory=sqlite3.Row; return c
    def settings(self):
        v=dict(DEFAULTS)
        try: v.update(json.loads((self.root/'settings.json').read_text('utf-8')))
        except (OSError,ValueError): pass
        return v
    def save_settings(self,v):
        with self.lock:
            f=self.root/'settings.tmp'; f.write_text(json.dumps(v,ensure_ascii=False,indent=2),'utf-8'); f.replace(self.root/'settings.json')
    def update_settings(self,**kw):
        with self.lock:
            v=self.settings(); v.update(kw); self.save_settings(v)
    def write_metrics(self, rows):
        validated=[]
        for r in rows:
            v={k:str(r.get(k,'')) for k in ['journal','issn','year','value','source','checked']}
            if not v['journal'] or not re.fullmatch(r'20\d{2}',v['year']): raise ValueError('期刊名或 JIF 年份不正确。')
            if not 0<float(v['value'])<300: raise ValueError('影响因子数值必须在 0–300 之间。')
            if not v['source'].startswith('https://'): raise ValueError('指标来源需填写 https 来源页面。')
            validated.append(v)
        with open(self.root/'journal_metrics.csv','w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=['journal','issn','year','value','source','checked']); w.writeheader(); w.writerows(validated)
    def metrics(self):
        file=self.root/'journal_metrics.csv';stamp=(file.stat().st_mtime_ns,file.stat().st_size)
        if getattr(self,'_metrics_stamp',None)!=stamp:
            with open(file,encoding='utf-8-sig',newline='') as f:self._metrics_rows=list(csv.DictReader(f))
            self._metrics_stamp=stamp
        return [dict(r) for r in self._metrics_rows]
    def merge_metrics(self,rows):
        from journal_metrics import journal_key
        existing=self.metrics();keys={(journal_key(r['journal']),int(r['year'])) for r in existing};added=0
        for row in rows:
            key=(journal_key(row['journal']),int(row['year']))
            if key not in keys:existing.append(row);keys.add(key);added+=1
        if added:self.write_metrics(existing)
        return added
    def search_results(self):
        try:return json.loads((self.root/'search_results.json').read_text('utf-8'))
        except (OSError,ValueError):return {}
    def stage_search_papers(self,papers):
        with self.db() as c:
            c.execute('DELETE FROM search_papers')
            c.executemany('INSERT INTO search_papers VALUES(?,?)',[(p['doi'],json.dumps(p,ensure_ascii=False)) for p in papers])
    def found_paper(self,doi):
        local=self.paper(doi,include_deleted=True)
        if local and local.get('deleted'):return None
        with self.db() as c:r=c.execute('SELECT payload FROM search_papers WHERE doi=?',(canonical_doi(doi),)).fetchone()
        if not r:return local
        p=json.loads(r['payload'])
        for key,default in [('pdf',''),('note',''),('seen',0),('favorite',0),('notified',0),('deleted',0)]:p[key]=local.get(key,default) if local else default
        return p
    def add_search_to_library(self,dois):
        allowed=set(self.search_results().get('dois',[]));added=0
        for doi in dict.fromkeys(dois):
            if doi not in allowed or self.paper(doi,include_deleted=True):continue
            p=self.found_paper(doi)
            if p:added+=self.put(p)
        return added
    def cached_assessment(self,p,settings):
        from topics import topic_key,evidence_hash
        with self.db() as c:r=c.execute('SELECT payload FROM assessments WHERE doi=? AND topic=? AND evidence=?',(p['doi'],topic_key(settings),evidence_hash(p))).fetchone()
        return json.loads(r['payload']) if r else {}
    def cache_assessment(self,p,a,settings):
        from topics import topic_key,evidence_hash
        with self.db() as c:c.execute('INSERT OR REPLACE INTO assessments VALUES(?,?,?,?)',(p['doi'],topic_key(settings),evidence_hash(p),json.dumps(a,ensure_ascii=False)))
    def save_search_results(self,settings,dois,**stats):
        from topics import search_key
        data=dict(key=search_key(settings),dois=list(dict.fromkeys(dois)),created=datetime.now().isoformat(timespec='seconds'),stats=stats)
        with self.lock:
            tmp=self.root/'search_results.tmp';tmp.write_text(json.dumps(data,ensure_ascii=False),'utf-8');tmp.replace(self.root/'search_results.json')
    def metric(self,p,year):
        from journal_metrics import journal_key
        matches=[]
        for m in self.metrics():
            try:
                # Supplied ISSNs must match; a conflicting ISSN cannot fall back to a journal title.
                known={x.strip().upper() for x in m['issn'].split(';') if x.strip()}
                actual={x.strip().upper() for x in p.get('issn',[]) if x.strip()}
                match=bool(actual & known) if actual and known else journal_key(m['journal'])==journal_key(p.get('journal',''))
                if match and 2020<=int(m['year'])<date.today().year and (not int(year) or int(m['year'])==int(year)):matches.append(m)
            except (ValueError,KeyError,TypeError):continue
        if not matches:return None
        newest=max(int(m['year']) for m in matches);matches=[m for m in matches if int(m['year'])==newest]
        try:
            if any(not (0<float(m['value'])<300 and m['source'].startswith('https://') and date.fromisoformat(m['checked'])<=date.today()) for m in matches):return None
            if len({float(m['value']) for m in matches})!=1:return None
        except (ValueError,KeyError,TypeError):return None
        return matches[0]
    def put(self,p):
        p=dict(p); p['doi']=canonical_doi(p['doi']); p['fetched_at']=datetime.now().isoformat(timespec='seconds')
        with self.db() as c:
            prior=c.execute('SELECT payload FROM papers WHERE doi=?',(p['doi'],)).fetchone()
            if prior:
                old=json.loads(prior[0]); old.update({k:v for k,v in p.items() if v not in (None,'',[],{})}); p=old
            c.execute('INSERT INTO papers(doi,payload,added) VALUES(?,?,?) ON CONFLICT(doi) DO UPDATE SET payload=excluded.payload',
                      (p['doi'],json.dumps(p,ensure_ascii=False),date.today().isoformat()))
        return prior is None
    @staticmethod
    def _paper_row(r):
        return dict(json.loads(r['payload']),**{k:r[k] for k in ['seen','favorite','notified','pdf','note','added','deleted','deleted_at']}) if r else None
    def papers(self,include_deleted=False):
        with self.db() as c: rows=c.execute('SELECT * FROM papers '+('' if include_deleted else 'WHERE deleted=0 ')+'ORDER BY added DESC').fetchall()
        return [self._paper_row(r) for r in rows]
    def paper(self,doi,include_deleted=False):
        with self.db() as c:r=c.execute('SELECT * FROM papers WHERE doi=?'+('' if include_deleted else ' AND deleted=0'),(canonical_doi(doi),)).fetchone()
        return self._paper_row(r)
    def delete_papers(self,dois):return self._set_deleted(dois,True)
    def restore_papers(self,dois):return self._set_deleted(dois,False)
    def _set_deleted(self,dois,deleted):
        count=0
        with self.db() as c:
            for doi in set(dois):
                count+=c.execute('UPDATE papers SET deleted=?,deleted_at=? WHERE doi=? AND deleted!=?',
                    (int(deleted),datetime.now().isoformat(timespec='seconds') if deleted else '',canonical_doi(doi),int(deleted))).rowcount
        return count
    def set_flag(self,doi,key,value):
        if key not in {'seen','favorite','notified','pdf','note'}: raise ValueError(key)
        with self.db() as c: c.execute(f'UPDATE papers SET {key}=? WHERE doi=?',(value,doi))
    def tokens(self):
        with self.db() as c: row=c.execute('SELECT tokens FROM usage WHERE day=?',(date.today().isoformat(),)).fetchone()
        return row[0] if row else 0
    def collections(self):
        with self.db() as c:
            return [dict(r) for r in c.execute('SELECT c.id,c.name,count(p.doi) AS count FROM collections c LEFT JOIN paper_collections pc ON pc.collection_id=c.id LEFT JOIN papers p ON p.doi=pc.doi AND p.deleted=0 GROUP BY c.id ORDER BY c.name')]
    def save_collection(self,name,collection_id=None):
        name=name.strip()
        if not 1<=len(name)<=80:raise ValueError('分类名称需为 1–80 个字符。')
        with self.db() as c:
            if any(r['name'].casefold()==name.casefold() and r['id']!=collection_id for r in c.execute('SELECT * FROM collections')):raise ValueError('分类名称已存在。')
            if collection_id is None:return c.execute('INSERT INTO collections(name) VALUES(?)',(name,)).lastrowid
            c.execute('UPDATE collections SET name=? WHERE id=?',(name,collection_id));return collection_id
    def delete_collection(self,collection_id):
        with self.db() as c:
            c.execute('DELETE FROM paper_collections WHERE collection_id=?',(collection_id,));c.execute('DELETE FROM collections WHERE id=?',(collection_id,))
    def memberships(self):
        with self.db() as c:rows=c.execute('SELECT * FROM paper_collections').fetchall()
        result={}
        for r in rows:result.setdefault(r['doi'],set()).add(r['collection_id'])
        return result
    def assign_collection(self,dois,collection_id,remove=False):
        with self.db() as c:
            if not c.execute('SELECT id FROM collections WHERE id=?',(collection_id,)).fetchone():raise ValueError('分类不存在。')
            for doi in dois:
                if not c.execute('SELECT doi FROM papers WHERE doi=?',(doi,)).fetchone():raise ValueError('文献不存在。')
                if remove:c.execute('DELETE FROM paper_collections WHERE doi=? AND collection_id=?',(doi,collection_id))
                else:c.execute('INSERT OR IGNORE INTO paper_collections VALUES(?,?)',(doi,collection_id))
    def merge_papers(self,source_doi,target_doi):
        if source_doi==target_doi:return
        with self.db() as c:
            source=c.execute('SELECT * FROM papers WHERE doi=?',(source_doi,)).fetchone();target=c.execute('SELECT * FROM papers WHERE doi=?',(target_doi,)).fetchone()
            if not source or not target:raise ValueError('合并记录不存在。')
            old=json.loads(source['payload']);p=json.loads(target['payload'])
            attachments=list(dict.fromkeys([x for x in [target['pdf'],source['pdf'],*p.get('pdf_alternates',[]),*old.get('pdf_alternates',[])] if x]))

            if not target['pdf'] and source['pdf'] and old.get('pdf_sha256'):p['pdf_sha256']=old['pdf_sha256']
            p['pdf_alternates']=attachments[1:];p['merged_ids']=list(dict.fromkeys(p.get('merged_ids',[])+[source_doi]))
            notes='\n'.join(dict.fromkeys(x for x in [target['note'],source['note']] if x))
            c.execute('UPDATE papers SET payload=?,pdf=?,seen=?,favorite=?,notified=?,note=? WHERE doi=?',
                      (json.dumps(p,ensure_ascii=False),attachments[0] if attachments else '',max(source['seen'],target['seen']),max(source['favorite'],target['favorite']),max(source['notified'],target['notified']),notes,target_doi))
            c.execute('INSERT OR IGNORE INTO paper_collections SELECT ?,collection_id FROM paper_collections WHERE doi=?',(target_doi,source_doi))
            c.execute('DELETE FROM paper_collections WHERE doi=?',(source_doi,));c.execute('DELETE FROM papers WHERE doi=?',(source_doi,))
    def add_usage(self,n):
        with self.db() as c: c.execute('INSERT INTO usage(day,tokens,calls) VALUES(?,?,1) ON CONFLICT(day) DO UPDATE SET tokens=tokens+excluded.tokens,calls=calls+1',(date.today().isoformat(),int(n)))
    def daily_budget(self):
        s=self.settings()
        return int(s['daily_token_limit'])+(int(s.get('token_extra',0)) if s.get('token_extra_day')==date.today().isoformat() else 0)
    def extend_budget(self,amount):
        with self.lock:
            s=self.settings(); today=date.today().isoformat()
            prior=int(s.get('token_extra',0)) if s.get('token_extra_day')==today else 0
            self.update_settings(token_extra_day=today,token_extra=prior+int(amount))
    def event(self,kind,message):
        with self.db() as c: c.execute('INSERT INTO events VALUES(?,?,?)',(datetime.now().isoformat(timespec='seconds'),kind,safe_error(message)))
    def events(self):
        with self.db() as c: return [dict(x) for x in c.execute('SELECT * FROM events ORDER BY ts DESC LIMIT 100')]

def set_autostart(enabled):
    if not getattr(sys,'frozen',False):
        if enabled: raise RuntimeError('请在打包后的 EXE 中启用开机启动。')
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as k:
        if enabled: winreg.SetValueEx(k,'MoltenDaily',0,winreg.REG_SZ,f'"{sys.executable}" --tray')
        else:
            try: winreg.DeleteValue(k,'MoltenDaily')
            except FileNotFoundError: pass
