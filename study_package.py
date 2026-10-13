"""Portable, data-only subtitle packages, matched by exact PDF SHA-256."""
import hashlib,json,math,re,zipfile
from pathlib import Path
from margins import cache_folder,study_parts,VERSION as CACHE_VERSION,norm

FORMAT='molten-daily-study';FORMAT_VERSION=1;MAX_BYTES=50*1024*1024

def pdf_hash(path):
    with open(path,'rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def _text(value,limit):
    if not isinstance(value,str) or len(value)>limit:raise ValueError('精读包的文字字段无效或过长。')
    return value

def _rect(value):
    if not isinstance(value,list) or len(value)!=4 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>100000 for v in value):raise ValueError('精读包的高亮坐标无效。')
    if value[2]<=value[0] or value[3]<=value[1]:raise ValueError('精读包的高亮区域无效。')
    return value

def clean_state(state):
    """Allowlist scientific data; never export paths, settings, keys or user notes."""
    if not isinstance(state,dict):raise ValueError('精读包数据无效。')
    doc=state.get('document',{});digest=doc.get('hash','');count=doc.get('page_count',0)
    if not re.fullmatch('[0-9a-f]{64}',digest) or type(count) is not int or not 1<=count<=100:raise ValueError('精读包的原文指纹或页数无效。')
    units=doc.get('units',[])
    if not isinstance(units,list) or len(units)>20000:raise ValueError('精读包的原文段落无效或过多。')
    safe=[];seen=set()
    for u in units:
        uid=_text(u['id'],160);page=u['page']
        if not uid or uid in seen or type(page) is not int or not 0<=page<count:raise ValueError('精读包存在重复段落或错误页码。')
        seen.add(uid);safe.append(dict(id=uid,page=page,bbox=_rect(u['bbox']),text=_text(u['text'],200000),kind='formula' if u.get('kind')=='formula' else 'text'))
    document=dict(hash=digest,page_count=count,units=safe,source='')
    parts={u['id']:u for u in study_parts(document)};results=state.get('results',{})
    if not isinstance(results,dict) or len(results)>len(parts):raise ValueError('精读包的译文段落数量不符。')
    cleaned={};total_notes=0
    for uid,r in results.items():
        if uid not in parts:raise ValueError('精读包的译文与原文编号不符。')
        zh=_text(r.get('zh',''),100000)
        if not zh.strip():continue
        items=r.get('items',[])
        if not isinstance(items,list) or len(items)>100:raise ValueError('精读包的词句批注数量无效。')
        notes=[]
        for n in items:
            if n.get('kind') not in ('word','phrase','sentence'):raise ValueError('精读包包含未知批注类型。')
            quote=_text(n.get('quote',''),1600);rects=n.get('rects',[])
            if not quote or norm(quote).casefold() not in norm(parts[uid]['text']).casefold():raise ValueError('精读包批注的引用不属于对应原文。')
            if not isinstance(rects,list) or not 1<=len(rects)<=200:raise ValueError('精读包高亮区域数量无效。')
            notes.append(dict(unit=uid,kind=n['kind'],quote=quote,ipa=_text(n.get('ipa',''),150),zh=_text(n.get('zh',''),100000),rects=[_rect(r) for r in rects]))
        total_notes+=len(notes)
        if total_notes>50000:raise ValueError('精读包批注数量超过上限。')
        cleaned[uid]=dict(zh=zh,items=notes)
    return dict(version=CACHE_VERSION,document=document,results=cleaned,warnings=[])

def export_package(store,pdf_path,target):
    digest=pdf_hash(pdf_path);cache=cache_folder(store,{'hash':digest})/'margins.json'
    if not cache.exists():raise ValueError('这篇论文尚无精读缓存，请先生成精读。')
    state=clean_state(json.loads(cache.read_text('utf-8')))
    if state['document']['hash']!=digest:raise ValueError('缓存与原文指纹不匹配，不能导出。')
    if not state['results']:raise ValueError('还没有已完成的段落。')
    manifest=dict(format=FORMAT,version=FORMAT_VERSION,pdf_sha256=digest,pages=state['document']['page_count'],paragraphs=len(state['results']))
    data=json.dumps(state,ensure_ascii=False).encode('utf-8')
    if len(data)>MAX_BYTES:raise ValueError('精读包内容超过 50 MB。')
    target=Path(target)
    if target.resolve()==Path(pdf_path).resolve():raise ValueError('精读包不能覆盖原文 PDF。')
    if target.suffix.lower()!='.mdstudy':target=target.with_name(target.name+'.mdstudy')
    temp=target.with_name(target.name+'.tmp')
    try:
        with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifest.json',json.dumps(manifest));z.writestr('study.json',data)
        temp.replace(target)
    finally:
        if temp.exists():temp.unlink()
    return manifest

def import_package(store,path):
    try:
        with zipfile.ZipFile(path) as z:
            infos=z.infolist()
            if len(infos)!=2 or {i.filename for i in infos}!={'manifest.json','study.json'} or sum(i.file_size for i in infos)>MAX_BYTES or any(i.flag_bits&1 for i in infos):raise ValueError('精读包包含未知文件、加密内容或体积超限。')
            manifest=json.loads(z.read('manifest.json'));state=clean_state(json.loads(z.read('study.json')))
        if manifest.get('format')!=FORMAT or manifest.get('version')!=FORMAT_VERSION:raise ValueError('精读包版本不支持，请使用兼容版本的软件。')
        digest=state['document']['hash']
        if manifest.get('pdf_sha256')!=digest or manifest.get('pages')!=state['document']['page_count'] or manifest.get('paragraphs')!=len(state['results']):raise ValueError('精读包校验失败，内容不完整或被修改。')
    except (KeyError,TypeError,AttributeError,zipfile.BadZipFile,UnicodeError,json.JSONDecodeError,RuntimeError) as e:
        raise ValueError('无法读取精读包：格式损坏或不是有效的 .mdstudy 文件。') from e
    folder=cache_folder(store,state['document']);folder.mkdir(exist_ok=True);cache=folder/'margins.json';added=len(state['results']);kept=0
    if cache.exists():
        local=json.loads(cache.read_text('utf-8'));clean_state(local)
        if local['document']['hash']!=digest:raise ValueError('本地缓存指纹冲突，未覆盖。')
        existing=local.get('results',{});kept=len(existing);added=len(set(state['results'])-set(existing))
        state['results'].update(existing)
        state['document']=local['document']
    temp=cache.with_suffix('.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2),'utf-8');temp.replace(cache)
    return dict(hash=digest,added=added,kept=kept,total=len(state['results']),cache=str(cache))
