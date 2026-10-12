"""Local state, verified journal metrics and Windows per-user secret protection."""
from __future__ import annotations
import base64, csv, ctypes, hashlib, json, os, re, sqlite3, sys, threading
from ctypes import wintypes
from datetime import date, datetime
from pathlib import Path

VERSION = '1.2.0'
APP = '熔盐文献日课'
DEFAULTS = dict(base_url='https://api.deepseek.com', model='deepseek-flash', api_secret='',
                openalex_secret='', email='', days=90, daily_count=3, max_pages=8,
                min_if=10.0, metric_year=2024, nuclear_only=False, strict_pages=False,
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
                CREATE TABLE IF NOT EXISTS events(ts TEXT,kind TEXT,message TEXT);''')
        if not (self.root/'settings.json').exists(): self.save_settings(dict(DEFAULTS))
        if not (self.root/'journal_metrics.csv').exists(): self.write_metrics(METRICS)
        settings=self.settings()
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
        with open(self.root/'journal_metrics.csv',encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))
    def metric(self,p,year):
        for m in self.metrics():
            match=bool(set(p.get('issn',[])) & set(m['issn'].split(';'))) or m['journal'].casefold()==p.get('journal','').casefold()
            if match and int(m['year'])==int(year): return m
        return None
    def put(self,p):
        p=dict(p); p['doi']=canonical_doi(p['doi']); p['fetched_at']=datetime.now().isoformat(timespec='seconds')
        with self.db() as c:
            prior=c.execute('SELECT payload FROM papers WHERE doi=?',(p['doi'],)).fetchone()
            if prior:
                old=json.loads(prior[0]); old.update({k:v for k,v in p.items() if v not in (None,'',[],{})}); p=old
            c.execute('INSERT INTO papers(doi,payload,added) VALUES(?,?,?) ON CONFLICT(doi) DO UPDATE SET payload=excluded.payload',
                      (p['doi'],json.dumps(p,ensure_ascii=False),date.today().isoformat()))
        return prior is None
    def papers(self):
        with self.db() as c: rows=c.execute('SELECT * FROM papers ORDER BY added DESC').fetchall()
        return [dict(json.loads(r['payload']),**{k:r[k] for k in ['seen','favorite','notified','pdf','note','added']}) for r in rows]
    def paper(self,doi): return next((p for p in self.papers() if p['doi']==doi),None)
    def set_flag(self,doi,key,value):
        if key not in {'seen','favorite','notified','pdf','note'}: raise ValueError(key)
        with self.db() as c: c.execute(f'UPDATE papers SET {key}=? WHERE doi=?',(value,doi))
    def tokens(self):
        with self.db() as c: row=c.execute('SELECT tokens FROM usage WHERE day=?',(date.today().isoformat(),)).fetchone()
        return row[0] if row else 0
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
