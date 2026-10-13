"""Traceable block translation, protected scientific tokens and dual-column PDFs.

Source pages stay vector-identical; translated pages reflow instead of shrinking.
No OCR or image-text translation is silently claimed.
"""
from __future__ import annotations
import hashlib, html, json, re
from collections import Counter
from datetime import datetime
from pathlib import Path
import fitz
from core import safe_error
from services import DeepSeek, ApiError, check_cancel
from topics import custom_topic

PROMPT_VERSION='2026-10-v4-general'
GLOSSARY={
 'dendrite':('/ˈdendraɪt/','枝晶；树枝状晶体'),
 'dendritic':('/denˈdrɪtɪk/','枝晶状的'),
 'electrodeposition':('/ɪˌlektroʊˌdepəˈzɪʃən/','电沉积'),
 'electrolyte':('/ɪˈlektrəlaɪt/','电解质'),
 'precipitation':('/prɪˌsɪpɪˈteɪʃən/','沉淀；固相从溶液或熔盐中析出'),
 'eutectic':('/juːˈtektɪk/','共晶的；共晶混合物'),
 'anodic':('/æˈnɒdɪk/','阳极的'),
 'cathodic':('/kæˈθɒdɪk/','阴极的'),
 'uranium':('/jʊˈreɪniəm/','铀，化学元素 U'),
 'lanthanide':('/ˈlænθənaɪd/','镧系元素'),
 'actinide':('/ˈæktɪnaɪd/','锕系元素'),
 'anisotropic':('/ˌænaɪsəˈtrɒpɪk/','各向异性的'),
 'diffraction':('/dɪˈfrækʃən/','衍射'),
 'tomography':('/təˈmɒɡrəfi/','断层成像'),
 'synchrotron':('/ˈsɪŋkrətrɒn/','同步加速器；文中用于指代同步辐射装置'),
 'nucleation':('/ˌnjuːkliˈeɪʃən/','成核'),
 'molten':('/ˈmoʊltən/','熔融的'),
 'stoichiometry':('/ˌstɔɪkiˈɒmətri/','化学计量关系'),
 'solubility':('/ˌsɒljəˈbɪləti/','溶解度'),
 'passivation':('/ˌpæsɪˈveɪʃən/','钝化'),
}

def tidy(s):
    s=s.replace('\u00ad','').replace('ﬁ','fi').replace('ﬂ','fl')
    return re.sub(r'\s+',' ',s).strip()

def protect_text(text):
    tokens=[]
    # Chemical strings, scientific notation, all numbers and explicit scripts.
    pattern=r'(?:\b(?:Li|Na|K|Ca|Mg|Al|Cl|U|Pu|Th|La|Nd|Sm|Eu|Yb|O|C|F|H|N|Sn|Ba|Fe|Ni|Cr|Be|Zr)(?:[A-Z][a-z]?|[0-9₀-₉⁰-⁹⁺⁻()+−-])+(?![a-z]))|(?:[−-]?\d+(?:[.,]\d+)*(?:[eE][+−-]?\d+)?(?:[⁰¹²³⁴⁵⁶⁷⁸⁹₀-₉⁺⁻]+)?)|(?:[₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+)'
    def sub(m):
        key=f'⟦K{len(tokens):03d}⟧'; tokens.append({'key':key,'text':m[0]}); return key
    return re.sub(pattern,sub,text),tokens

def validate_translation(text,tokens):
    actual=Counter(re.findall(r'⟦K\d{3,}⟧',text)); expected=Counter(x['key'] for x in tokens)
    if actual!=expected: return '数值/化学式保护标记缺失、重复或被修改'
    if not text.strip(): return '译文为空'
    return ''
def restore(text,tokens):
    for t in tokens: text=text.replace(t['key'],t['text'])
    return text

def order_blocks(blocks,width):
    """Full-width text divides a page into bands; left column then right column."""
    mid=width/2
    wide=sorted([b for b in blocks if b['bbox'][0]<mid-35 and b['bbox'][2]>mid+35 and b['bbox'][2]-b['bbox'][0]>.62*width],key=lambda b:b['bbox'][1])
    out=[]; rest=[b for b in blocks if b not in wide]
    for wide_b in wide+[None]:
        boundary=wide_b['bbox'][1] if wide_b else float('inf')
        band=[b for b in rest if b['bbox'][1]<boundary]; rest=[b for b in rest if b not in band]
        out+=sorted([b for b in band if (b['bbox'][0]+b['bbox'][2])/2<mid],key=lambda b:b['bbox'][1])
        out+=sorted([b for b in band if (b['bbox'][0]+b['bbox'][2])/2>=mid],key=lambda b:b['bbox'][1])
        if wide_b: out.append(wide_b)
    return out

def extract_document(path):
    path=Path(path)
    if path.stat().st_size>100*1024*1024: raise ValueError('单篇 PDF 限制 100 MB。')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    units=[]; warnings=[]; pages=[]
    with fitz.open(path) as doc:
        if doc.needs_pass: raise ValueError('请先用合法密码解锁 PDF，再导入。')
        if doc.page_count>100: raise ValueError('本版面向研究论文，单次最多 100 页；请拆分长文档。')
        for pi,page in enumerate(doc):
            raw=page.get_text('dict',flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES); blocks=[]
            for b in raw['blocks']:
                if b.get('type')!=0: continue
                lines=[]
                for line in b['lines']:
                    spans=line['spans']; normal=max((s['size'] for s in spans),default=10)
                    normal_spans=[s for s in spans if s['size']>=normal*.95]
                    baseline=max(normal_spans,key=lambda s:len(s['text']))['origin'][1] if normal_spans else 0
                    parts=[]
                    for sp in spans:
                        v=sp['text']
                        if (sp['size']<normal*.88 or sp['flags']&1) and baseline:
                            super_map=str.maketrans('0123456789+-','⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻'); sub_map=str.maketrans('0123456789+-','₀₁₂₃₄₅₆₇₈₉₊₋')
                            if sp['origin'][1]<baseline-1 or sp['flags']&1: v=v.translate(super_map)
                            elif sp['origin'][1]>baseline+.5: v=v.translate(sub_map)
                        parts.append(v)
                    lines.append(''.join(parts))
                t='\n'.join(lines); t=re.sub(r'([a-z])-\n([a-z])',r'\1\2',t); t=tidy(t)
                if t: blocks.append({'text':t,'bbox':list(b['bbox'])})
            if sum(len(b['text']) for b in blocks)<40:
                warnings.append(f'第 {pi+1} 页缺少可靠文本层：扫描图/纯图页保留原页，无法宣称文字已完整翻译。')
            ordered=order_blocks(blocks,page.rect.width)
            for bi,b in enumerate(ordered):
                txt=b['text']; math=bool(re.search(r'[=∑∫√∂]',txt)) and len(re.findall(r'\b[A-Za-z]{3,}\b',txt))<5
                protected,tokens=protect_text(txt)
                b.update(id=f'P{pi+1:03d}-B{bi+1:03d}',page=pi,kind='formula' if math else 'text',protected=protected,tokens=tokens)
                units.append(b)
            pages.append({'page':pi,'width':page.rect.width,'height':page.rect.height,'blocks':len(ordered)})
    if not units: raise ValueError('这是无文本层的扫描 PDF。本版不会用不可靠 OCR 冒充完整翻译；请导入带文本层的 PDF。')
    return {'hash':digest,'source':str(path.resolve()),'page_count':len(pages),'pages':pages,'units':units,'warnings':warnings,'created':datetime.now().isoformat()}

class Translator:
    def __init__(self,store,cancel=None,progress=lambda x:None,client=None):
        self.store=store; self.cancel=cancel; self.progress=progress; self.client=client or DeepSeek(store,cancel=cancel)
    def run(self,path,page_limit=None):
        doc=extract_document(path); s=self.store.settings()
        key=hashlib.sha256((doc['hash']+s['model']+s['base_url']+PROMPT_VERSION+str(custom_topic(s))).encode()).hexdigest()[:24]
        folder=self.store.root/'translations'/key; folder.mkdir(exist_ok=True); statefile=folder/'translation.json'
        if statefile.exists(): state=json.loads(statefile.read_text('utf-8')); state['document']=doc
        else: state={'document':doc,'model':s['model'],'prompt_version':PROMPT_VERSION,'results':{},'errors':{},'updated':''}
        units=[u for u in doc['units'] if page_limit is None or u['page']<page_limit]
        def save():
            state['updated']=datetime.now().isoformat(); tmp=statefile.with_suffix('.tmp'); tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2),'utf-8'); tmp.replace(statefile)
        for i,u in enumerate(units):
            check_cancel(self.cancel)
            if u['id'] in state['results']:
                cached=state['results'][u['id']]
                if cached.get('protected_zh'):
                    if validate_translation(cached['protected_zh'],u['tokens']):state['results'].pop(u['id'])
                    else:cached['zh']=restore(cached['protected_zh'],u['tokens']);continue
                else:continue
            self.progress(f'翻译 {i+1}/{len(units)} · 原文第 {u["page"]+1} 页 · {u["id"]}')
            if u['kind']=='formula':
                state['results'][u['id']]={'zh':'【公式以原文矢量片段保留】','glossary':[],'checks':[],'type':'formula'}; save(); continue
            if not re.search(r'[A-Za-z]{3,}',u['text']):
                state['results'][u['id']]={'zh':u['text'],'glossary':[],'checks':[],'type':'source'};save();continue
            system=('你是跨学科专业学术翻译。根据原文所属领域确定术语含义，不强加其他领域的含义。逐句忠实译为简体中文，不总结、不增删推理或数据，'
                    '保留may/suggest等不确定性及因果关系。人名、文献题录可保留英文；页眉等也不得漏掉。'
                    '在电化学语境下的术语参考：molten salt=熔盐，uranium=铀，actinide=锕系元素，lanthanide=镧系元素，'
                    'anode=阳极，cathode=阴极，precipitation=沉淀，electrodeposition=电沉积，'
                    'current efficiency=电流效率，recovery yield=回收率，不得混淆。'
                    '所有 ⟦K000⟧ 一类保护标记必须原样出现一次，禁止改写或翻译。'
                    '只返回 {"id":"相同id","zh":"完整逐句译文","glossary":[{"term":"原文实际出现的英文单词",'
                    '"ipa":"国际音标或空字符串","meaning":"本段语境中的中文释义"}]}。最多选2个难词；不确定读音则留空。')
            try:
                # A malformed response is repaired once; never silently accept missing formulas/numbers.
                result=None
                for attempt in range(2):
                    payload={'id':u['id'],'text':u['protected']}
                    if attempt: payload['repair']='上一响应的保护标记或段落编号不合格，重新完整翻译。'
                    r=self.client.json(system,payload,max_tokens=min(7500,max(1300,len(u['text'])*2)))
                    issue=validate_translation(str(r.get('zh','')),u['tokens'])
                    if r.get('id')!=u['id']: issue='段落编号错配'
                    if not issue: result=r; break
                if result is None: raise ApiError(issue)
                zh=restore(result['zh'],u['tokens']); checks=[]
                if len(zh)<len(u['text'])*.12 and len(u['text'])>200: checks.append('译文异常短，可能遗漏信息')
                if '\ufffd' in u['text']: checks.append('原文存在无法识别字符')
                gloss=[]
                for term,(ipa,meaning) in ({} if custom_topic(s) else GLOSSARY).items():
                    if re.search(r'\b'+re.escape(term)+r'\b',u['text'],re.I): gloss.append(dict(term=term,ipa=ipa,meaning=meaning,origin='内置专业词表'))
                for g in result.get('glossary',[])[:2]:
                    term=str(g.get('term',''))[:80]
                    if term and term.casefold() in u['text'].casefold() and term.casefold() not in {x['term'].casefold() for x in gloss}:
                        gloss.append(dict(term=term,ipa=str(g.get('ipa',''))[:120],meaning=str(g.get('meaning',''))[:240],origin='AI 语境注释；音标未接入词典核验'))
                state['results'][u['id']]={'zh':zh,'protected_zh':result['zh'],'glossary':gloss[:6],'checks':checks,'type':'text'}
                state['errors'].pop(u['id'],None)
            except Exception as e:
                state['errors'][u['id']]=safe_error(e); save(); raise
            save()
        save(); self.progress('翻译已缓存；生成对照 PDF 和原文批注…')
        outputs=export_translation(state,folder,self.cancel)
        return dict(statefile=str(statefile),folder=str(folder),**outputs)

CSS='body {font-family: sans-serif; font-size: 10pt; line-height:1.5; color:#223b40;} p {margin:0 0 7pt;} .id {font-size:8pt;color:#118278;} .warn {color:#b55e2e;} .word {font-size:9pt;color:#46636a;}'
def chunks(text,n=480):
    while len(text)>n:
        stop=max(text.rfind('。',0,n),text.rfind('；',0,n),text.rfind('. ',0,n))
        if stop<n*.4: stop=n-1
        yield text[:stop+1]; text=text[stop+1:]
    if text: yield text

def scientific_html(text):
    # Unicode super/subscripts become actual scripts, not arbitrary font glyphs.
    s=html.escape(text)
    chemical=r'\b(?:Li|Na|K|Ca|Mg|Al|Cl|U|Pu|Th|La|Nd|Sm|Eu|Yb|O|C|F|H|N|Sn|Ba|Fe|Ni|Cr|Be|Zr)(?:[A-Z][a-z]?|\d|[()+−-])+(?![a-z])'
    def chem(m):
        token=m[0]
        ion=re.fullmatch(r'([A-Z][a-z]?)(\d*[+−-])',token)
        if ion:return ion[1]+'<sup>'+ion[2]+'</sup>'
        # Plain multi-atom strings such as CO32- are ambiguous. Preserve them
        # instead of inventing a 32- charge; explicit Unicode scripts below are safe.
        if re.search(r'\d[+−-]$',token):return token
        return re.sub(r'\d+',r'<sub>\g<0></sub>',token)
    s=re.sub(chemical,chem,s)
    for chars,tag,normal in [('₀₁₂₃₄₅₆₇₈₉₊₋','sub','0123456789+-'),('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻','sup','0123456789+-')]:
        mapping=str.maketrans(chars,normal)
        s=re.sub('['+chars+']+',lambda m:'<'+tag+'>'+m[0].translate(mapping)+'</'+tag+'>',s)
    return s

class TwoColumn:
    def __init__(self,doc,title,source_page):
        self.doc=doc; self.title=title; self.source_page=source_page; self.count=0; self.page=None; self.new_page()
    def new_page(self):
        self.page=self.doc.new_page(width=595.28,height=841.89); self.count+=1; self.col=0; self.y=62
        self.page.insert_htmlbox(fitz.Rect(34,18,562,52),f'<b>原文第 {self.source_page+1} 页 · 中文对照</b> <span style="font-size:8pt"> / {self.count}</span>',css=CSS)
        self.page.draw_line((34,53),(561,53),color=(.05,.4,.36),width=.7)
        self.page.insert_htmlbox(fitz.Rect(34,813,562,837),'按原文文本块编号对应 · 图表与公式见相邻原文页 · AI 翻译',css=CSS)
    def advance(self):
        if self.col==0: self.col=1; self.y=62
        else: self.new_page()
    def add_html(self,body):
        x=34 if self.col==0 else 309
        for _ in range(3):
            rect=fitz.Rect(x,self.y,x+252,801)
            spare,scale=self.page.insert_htmlbox(rect,body,css=CSS,scale_low=1)
            if spare>=0:
                self.y=801-spare+6; return
            self.advance(); x=34 if self.col==0 else 309
        raise ValueError('译文段落过长，无法在双栏中排版。')
    def add_formula(self,source,unit):
        bbox=fitz.Rect(unit['bbox']); w=252; h=min(180,max(28,bbox.height*w/max(1,bbox.width)))
        if self.y+h+25>801: self.advance()
        self.add_html(f'<div class="id">{unit["id"]} · 公式原样保留</div>')
        if self.y+h>801: self.advance()
        x=34 if self.col==0 else 309
        self.page.show_pdf_page(fitz.Rect(x,self.y,x+w,self.y+h),source,unit['page'],clip=bbox)
        self.y+=h+10

def export_translation(state,folder,cancel=None):
    folder=Path(folder); docinfo=state['document']; source=fitz.open(docinfo['source']); annotated=fitz.open(); bilingual=fitz.open()
    completed=state['results']; glossary={}; missing=[u['id'] for u in docinfo['units'] if u['id'] not in completed]
    diagnostics=list(docinfo['warnings']); toc=[]
    try:
        annotated.insert_pdf(source)
        for pi,page in enumerate(annotated):
            check_cancel(cancel)
            us=[u for u in docinfo['units'] if u['page']==pi]
            for u in us:
                r=completed.get(u['id'])
                a=page.add_text_annot((max(8,u['bbox'][0]-12),max(8,u['bbox'][1])),u['id']+'\n'+(r['zh'] if r else '该段尚未翻译'))
                a.set_info(title='MoltenDaily 段落对照'); a.set_flags(a.flags|fitz.PDF_ANNOT_IS_NO_VIEW); a.update()
                if not r: continue
                diagnostics.extend(u['id']+' '+x for x in r.get('checks',[]))
                for g in r.get('glossary',[]):
                    glossary[g['term'].casefold()]=g
                    for rect in page.search_for(g['term'],clip=fitz.Rect(u['bbox']))[:4]:
                        a=page.add_highlight_annot(rect); a.set_info(title=g['term'],content=g['ipa']+'\n'+g['meaning']+'\n'+g['origin']); a.set_opacity(.22); a.update()
            # Original PDF graphics, glyph placement and vector formulas stay untouched.
            toc.append([1,f'原文第 {pi+1} 页',len(bilingual)+1]); bilingual.insert_pdf(annotated,from_page=pi,to_page=pi)
            if not any(u['id'] in completed for u in us): continue
            toc.append([2,f'第 {pi+1} 页中文对照',len(bilingual)+1]); layout=TwoColumn(bilingual,'中文对照',pi)
            short=[]
            def flush_short():
                if short:
                    layout.add_html(''.join(short));short.clear()
            for u in us:
                r=completed.get(u['id'])
                if not r:
                    flush_short()
                    layout.add_html(f'<p class="warn">{u["id"]} 尚未翻译。本文件为部分结果。</p>'); continue
                if u['kind']=='formula': flush_short();layout.add_formula(source,u);continue
                if len(r['zh'])<=85:
                    short.append(f'<p style="margin:0 0 4pt"><span class="id">{u["id"]}</span>　{scientific_html(r["zh"])}</p>')
                    if len(short)>=8:flush_short()
                    continue
                flush_short()
                for ci,part in enumerate(chunks(r['zh'])):
                    layout.add_html(f'<div class="id">{u["id"]}{" · 续" if ci else ""}</div><p>{scientific_html(part)}</p>')
            flush_short()
            pagegloss={g['term'].casefold():g for u in us for g in completed.get(u['id'],{}).get('glossary',[])}
            if pagegloss:
                layout.add_html('<p><b>本页词汇</b></p>')
                for g in pagegloss.values():
                    layout.add_html('<p class="word"><b>'+html.escape(g['term'])+'</b> '+html.escape(g['ipa'])+'<br>'+html.escape(g['meaning'])+'<br><small>'+html.escape(g['origin'])+'</small></p>')
        if not bilingual.page_count: raise ValueError('没有可导出的页面。')
        status='部分翻译' if missing or docinfo['warnings'] else '文本层翻译完成'
        bilingual.set_toc(toc); bilingual.set_metadata({'title':Path(docinfo['source']).stem+' 中英对照','subject':status+'；自动检查不代替科学核验','creator':'MoltenDaily'})
        names={'annotated':folder/'原文_段落与生词批注.pdf','bilingual':folder/'中英对照_中文双栏.pdf'}
        for pdf,name in [(annotated,names['annotated']),(bilingual,names['bilingual'])]:
            tmp=name.with_suffix('.tmp.pdf'); pdf.save(tmp,garbage=4,deflate=True); tmp.replace(name)
        audit={'status':status,'source_sha256':docinfo['hash'],'source_pages':source.page_count,'total_blocks':len(docinfo['units']),
               'translated_blocks':len(completed),'missing_ids':missing,'warnings':diagnostics,'glossary_terms':len(glossary),
               'model':state['model'],'created':datetime.now().isoformat(),'limits':['图中文字保留原文，不执行 OCR 翻译。',
               '文本块与页面编号一一对应，不承诺段落识别等同作者语义分段。','科学严谨性需以原文证据为准；自动检查覆盖段落、保护标记和排版。']}
        (folder/'翻译检查报告.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),'utf-8')
        (folder/'词汇表.json').write_text(json.dumps(list(glossary.values()),ensure_ascii=False,indent=2),'utf-8')
        return {**{k:str(v) for k,v in names.items()},'audit':audit}
    finally: source.close(); annotated.close(); bilingual.close()
