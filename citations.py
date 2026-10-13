"""Portable citation exchange; no generated authors, DOI or page ranges."""
from pathlib import Path
from urllib.parse import urlparse,unquote
import hashlib,re,xml.etree.ElementTree as ET
from core import canonical_doi

def clean(value):return re.sub(r'[\r\n\x00-\x08\x0b\x0c\x0e-\x1f]+',' ',str(value or '')).strip()
def authors(p):
    if p.get('author_list'):
        return [clean((a.get('family','')+', '+a.get('given','')).strip(', ') if a.get('family') else a.get('name','')) for a in p['author_list']]
    return [clean(x) for x in p.get('authors','').split(';') if clean(x)]
def year(p):return p.get('date','')[:4] if re.match(r'^\d{4}',p.get('date','')) else ''
def real_doi(p):return p['doi'] if p['doi'].startswith('10.') else ''
def missing_fields(p):return [name for name,value in [('标题',p.get('title')),('作者',authors(p)),('年份',year(p)),('期刊',p.get('journal') if not p.get('journal','').startswith('本地') else ''),('卷',p.get('volume')),('页码或文章号',p.get('page_range') or p.get('article_number'))] if not value]
def export_records(papers,path):
    path=Path(path);is_enw=path.suffix.lower()=='.enw';lines=[]
    for p in papers:
        fields=[('TY','Journal Article' if is_enw else 'JOUR'),('TI',p['title']),('JO',p.get('journal','')),('PY',year(p)),('VL',p.get('volume','')),('IS',p.get('issue',''))]
        fields += [('AU',a) for a in authors(p)]
        fields += [('SP',p.get('page_range') or p.get('article_number','')),('DO',real_doi(p)),('UR',p.get('url','')),('AB',p.get('abstract',''))]
        fields += [('KW',x) for x in p.get('keywords',[])]
        if p.get('pdf') and Path(p['pdf']).exists():fields.append(('L1',Path(p['pdf']).resolve().as_uri()))
        missing=missing_fields(p)
        if missing:fields.append(('N1','待核实字段：'+'、'.join(missing)))
        tags={'TY':'0','TI':'T','JO':'J','PY':'D','VL':'V','IS':'N','AU':'A','SP':'P','DO':'R','UR':'U','AB':'X','KW':'K','L1':'>','N1':'Z'}
        lines.extend(('%'+tags[k]+' '+clean(v)) if is_enw else (k+'  - '+clean(v)) for k,v in fields if clean(v))
        if not is_enw:lines.append('ER  - ')
        lines.append('')
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8');return str(path)

def _paper(fields):
    get=lambda k:fields.get(k,[''])[0]
    title=get('TI') or get('T1')
    if not title:raise ValueError('导入题录缺少标题。')
    doi=canonical_doi(get('DO'));author_list=[]
    for a in fields.get('AU',[]) or fields.get('A1',[]):
        if ',' in a:family,given=a.split(',',1);author_list.append(dict(family=family.strip(),given=given.strip()))
        else:author_list.append(dict(name=a))
    identity=doi if doi.startswith('10.') else 'ref:'+hashlib.sha256((title+'|'+get('PY')+'|'+str(author_list)).encode()).hexdigest()
    return dict(doi=identity,title=title,journal=get('JO') or get('JF') or get('T2'),date=(get('PY') or get('Y1'))[:4],author_list=author_list,
                authors='; '.join(fields.get('AU',[]) or fields.get('A1',[])),volume=get('VL'),issue=get('IS'),page_range=get('SP')+('-'+get('EP') if get('EP') else ''),
                abstract=get('AB'),url=get('UR') or ('https://doi.org/'+doi if doi.startswith('10.') else ''),issn=[],source='EndNote / RIS 导入',keywords=fields.get('KW',[]),import_attachments=fields.get('L1',[]))

def read_records(path):
    path=Path(path)
    if path.stat().st_size>20*1024*1024:raise ValueError('题录文件超过 20 MB，请分批导入。')
    data=path.read_text('utf-8-sig')
    if path.suffix.lower()=='.xml':
        if '<!DOCTYPE' in data.upper() or '<!ENTITY' in data.upper():raise ValueError('不支持含实体声明的 XML。')
        root=ET.fromstring(data);records=[]
        for rec in root.findall('.//record'):
            value=lambda tag:''.join(rec.find(tag).itertext()).strip() if rec.find(tag) is not None else ''
            fields={k:[value(tag)] for k,tag in {'TI':'titles/title','JO':'titles/secondary-title','PY':'dates/year','VL':'volume','IS':'number','SP':'pages','DO':'electronic-resource-num','AB':'abstract','UR':'urls/web-urls/url'}.items()}
            fields['AU']=[''.join(x.itertext()).strip() for x in rec.findall('contributors/authors/author')]
            fields['KW']=[''.join(x.itertext()).strip() for x in rec.findall('keywords/keyword')]
            fields['L1']=[''.join(x.itertext()).strip() for x in rec.findall('urls/pdf-urls/url')];records.append(_paper(fields))
        return records
    records=[];fields={};last=''
    for line in data.splitlines()+['ER  - ']:
        match=re.match(r'^([A-Z0-9]{2})  - ?(.*)$',line)
        if match:
            tag,value=match.groups()
            if tag=='ER':
                if fields:records.append(_paper(fields));fields={}
            else:fields.setdefault(tag,[]).append(value.strip());last=tag
        elif line.strip() and last and fields:fields[last][-1]+=' '+line.strip()
    return records

def local_attachment(value,source_file):
    if value.startswith('internal-pdf://'):
        rel=unquote(value[len('internal-pdf://'):]);root=Path(source_file).with_suffix('.Data')/'PDF'
        p=(root/rel).resolve()
        if not p.is_relative_to(root.resolve()):return None
    elif value.startswith('file:'):
        u=urlparse(value)
        if u.netloc:return None
        name=unquote(u.path);name=name[1:] if re.match(r'^/[A-Za-z]:',name) else name;p=Path(name)
    elif re.match(r'^[A-Za-z]:[\\/]',value):p=Path(value)
    else:return None
    return p if p.suffix.lower()=='.pdf' and p.is_file() else None

def temporary_citation(p):
    doi=real_doi(p)
    if not doi:raise ValueError('请先补全 DOI，再复制可准确匹配的 EndNote 临时引用。')
    if any(c in doi for c in '{};'):raise ValueError('此 DOI 含临时引用分隔符，请使用 Word 正式引用。')
    return '{'+doi+'}'

NS='http://schemas.openxmlformats.org/officeDocument/2006/bibliography'
ET.register_namespace('b',NS)
def word_source(p):
    if not p.get('title') or not authors(p) or not year(p):raise ValueError('Word 引用需要标题、作者和年份；请先用 DOI 补全题录。')
    root=ET.Element('{'+NS+'}Source')
    def add(parent,key,value):return ET.SubElement(parent,'{'+NS+'}'+key) if value is None else _text(parent,key,value)
    def _text(parent,key,value):e=ET.SubElement(parent,'{'+NS+'}'+key);e.text=clean(value);return e
    tag='MD'+hashlib.sha256(p['doi'].encode()).hexdigest()[:20];add(root,'Tag',tag);add(root,'SourceType','JournalArticle')
    a=add(add(root,'Author',None),'Author',None);names=add(a,'NameList',None)
    for author in p.get('author_list') or [{'name':x} for x in authors(p)]:
        person=add(names,'Person',None);add(person,'Last',author.get('family') or author.get('name',''))
        if author.get('given'):add(person,'First',author['given'])
    for k,v in {'Title':p['title'],'JournalName':p.get('journal',''),'Year':year(p),'Volume':p.get('volume',''),'Issue':p.get('issue',''),'Pages':p.get('page_range') or p.get('article_number',''),'StandardNumber':real_doi(p),'URL':p.get('url','')}.items():
        if v:add(root,k,v)
    return tag,ET.tostring(root,encoding='unicode')
