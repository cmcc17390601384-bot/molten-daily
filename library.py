"""Managed PDF attachments and safe identity reconciliation."""
from pathlib import Path
import hashlib,re,shutil
import fitz
from core import canonical_doi
from translation import extract_document

def doi_candidates(path):
    with fitz.open(path) as doc:text=' '.join(doc[i].get_text() for i in range(min(2,len(doc))))
    return list(dict.fromkeys(canonical_doi(m.rstrip('.,;:)]}')) for m in re.findall(r'10\.\d{4,9}/[^\s<>"\{]+',text,re.I)))[:12]

def link_pdf(store,doi,path):
    info=extract_document(path);paper=store.paper(doi)
    if not paper:raise ValueError('请先选择需要关联的题录。')
    dest=store.root/'pdfs'/(info['hash'][:20]+'.pdf')
    if Path(path).resolve()!=dest.resolve():shutil.copy2(path,dest)
    if paper.get('pdf') and paper['pdf']!=str(dest):
        paper['pdf_alternates']=list(dict.fromkeys(paper.get('pdf_alternates',[])+[paper['pdf']]))
    paper.update(pdf_sha256=info['hash'],pages=info['page_count']);store.put(paper);store.set_flag(doi,'pdf',str(dest))
    local='local:'+info['hash']
    if local!=doi and store.paper(local):store.merge_papers(local,doi)
    return str(dest)

def import_pdf(store,path):
    info=extract_document(path)
    existing=next((p for p in store.papers(include_deleted=True) if p.get('pdf_sha256')==info['hash']),None)
    if existing:
        store.restore_papers([existing['doi']]);link_pdf(store,existing['doi'],path);return store.paper(existing['doi'])
    with fitz.open(path) as doc:title=(doc.metadata.get('title') or Path(path).stem).strip()
    key='local:'+info['hash'];store.put(dict(doi=key,title=title,journal='本地导入 / 题录待补全',issn=[],date='',source='本地 PDF',pages=info['page_count'],doi_candidates=doi_candidates(path)))
    link_pdf(store,key,path);return store.paper(key)

def reconcile(store,old_doi,verified):
    store.put(verified)
    store.restore_papers([verified['doi']])
    if old_doi!=verified['doi']:store.merge_papers(old_doi,verified['doi'])
    return store.paper(verified['doi'])

def edit_metadata(store,doi,values):
    p=store.paper(doi)
    if not p:raise ValueError('文献不存在。')
    title=values.get('title','').strip();year=values.get('date','').strip()
    if not title:raise ValueError('标题不能为空。')
    if year and not re.fullmatch(r'(19|20)\d{2}',year):raise ValueError('年份应为四位数字，未知可留空。')
    new_doi=canonical_doi(values.get('doi',''))
    if new_doi and not re.fullmatch(r'10\.\d{4,9}/\S+',new_doi):raise ValueError('DOI 格式不正确；未知可留空。')
    if new_doi and new_doi!=doi and store.paper(new_doi):raise ValueError('此 DOI 已在图书馆，请用“DOI 补全 / 合并”保留两条记录的附件及分类。')
    if p.get('journal','')!=values.get('journal','').strip():p['issn']=[]
    for key in ('title','journal','date','volume','issue','page_range','article_number'):p[key]=values.get(key,'').strip()
    from citations import authors
    entered=values.get('authors','').strip()
    if entered!='; '.join(authors(p)):
        p['author_list']=[]
        for name in entered.split(';'):
            name=name.strip()
            if not name:continue
            if ',' in name:family,given=name.split(',',1);p['author_list'].append(dict(family=family.strip(),given=given.strip()))
            else:p['author_list'].append(dict(name=name))
        p['authors']=entered
    p['metadata_edited']=True
    if new_doi and new_doi!=doi:
        p['doi']=new_doi;p['url']='https://doi.org/'+new_doi;store.put(p);store.merge_papers(doi,new_doi)
    else:
        # Explicit editing can clear fields; retrieval merging deliberately cannot.
        import json
        with store.db() as db:db.execute('UPDATE papers SET payload=? WHERE doi=?',(json.dumps(p,ensure_ascii=False),doi))
    return p['doi']
