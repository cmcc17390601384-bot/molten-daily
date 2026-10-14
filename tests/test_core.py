import sys, json, threading
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
import fitz, pytest
from core import Store,SEEDS,protect,unprotect,canonical_doi,safe_error
from services import eligibility,topic_score,cr_paper,oa_paper,page_count,public_url,DeepSeek,Retriever,ApiError,Cancelled
from translation import extract_document,protect_text,validate_translation,restore,order_blocks,Translator

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'data');s.update_settings(metric_year=2024);return s

def test_secret_roundtrip():
    raw='test-secret-never-persist-in-clear'; encrypted=protect(raw)
    assert raw not in encrypted and unprotect(encrypted)==raw
def test_doi_and_redaction():
    assert canonical_doi('HTTPS://doi.org/10.1002/ABC')=='10.1002/abc'
    assert 'SECRET' not in safe_error('Bearer SECRET sk-SECRET api_key=SECRET&v=2')
def test_dedup_preserves_flags(store):
    p=dict(SEEDS[0]);assert store.put(p);store.set_flag(p['doi'],'seen',1);store.set_flag(p['doi'],'favorite',1)
    p['doi']='https://doi.org/'+p['doi'].upper();assert not store.put(p)
    assert len(store.papers())==1 and store.papers()[0]['seen']==1 and store.papers()[0]['favorite']==1
def test_unknown_if_not_qualified(store):
    p=dict(SEEDS[0],journal='Unknown Journal',issn=[])
    assert not eligibility(p,store)[0]
def test_strict_greater_than_10(store):
    ms=store.metrics();ms[0]['value']='10';store.write_metrics(ms)
    assert not eligibility(SEEDS[0],store)[0]
def test_year_exact(store):
    s=store.settings();s['metric_year']=2022;assert not eligibility(SEEDS[0],store,s)[0]
def test_pages_unknown_and_long(store):
    s=store.settings();s['strict_pages']=True
    assert eligibility(SEEDS[0],store,s)[0]
    assert not eligibility(SEEDS[1],store,s)[0]
    assert not eligibility(dict(SEEDS[0],pages=None),store,s)[0]
def test_synthesis_not_automatic_match():
    assert not topic_score(dict(title='Molten salt synthesis of nanoparticles',abstract=''))[1]
    assert topic_score(SEEDS[1])[1:]==(True,True)
def test_retraction_and_future(store):
    assert not eligibility(dict(SEEDS[0],retracted=True),store)[0]
    assert not eligibility(dict(SEEDS[0],date=(date.today()+timedelta(days=4)).isoformat()),store)[0]
def test_page_range_not_article_number():
    assert page_count('7664-7668')==5 and page_count('e202216315') is None and page_count('5777') is None
def test_metadata():
    p=cr_paper({'DOI':'10.1234/X','title':['<i>Test</i>'],'container-title':['J'],'published-online':{'date-parts':[[2025,6,11]]},'published-print':{'date-parts':[[2025,8,28]]},'page':'10-15'})
    assert p['date']=='2025-06-11' and p['pages']==6 and p['title']=='Test'
def test_urls():
    for u in ['file:///secret','http://api.deepseek.com','https://localhost/a','https://127.0.0.1/a','https://u:p@api.deepseek.com']:
        with pytest.raises(ValueError):public_url(u)
    assert public_url('https://api.deepseek.com')
def test_budget_prevents_call(store):
    s=store.settings();s['daily_token_limit']=100
    with pytest.raises(ApiError,match='预算'):DeepSeek(store,s).json('test',{},1000)
def test_protection():
    original='UCl₃ in LiCl-KCl at 453 K; 94.6% and 1.20e-16 mol cm⁻³.'
    text,tokens=protect_text(original);assert restore(text,tokens)==original;assert not validate_translation(text,tokens)
    assert validate_translation(text.replace(tokens[0]['key'],''),tokens)
    assert validate_translation(text+tokens[0]['key'],tokens)

def test_scientific_subscripts():
    from translation import scientific_html
    assert scientific_html('NaAlCl4')=='NaAlCl<sub>4</sub>'
    assert scientific_html('UCl₃')=='UCl<sub>3</sub>'
    assert scientific_html('cm⁻³')=='cm<sup>-3</sup>'
    assert scientific_html('U³⁺')=='U<sup>3+</sup>'
    assert scientific_html('Al3+')=='Al<sup>3+</sup>'
    assert scientific_html('CO32-')=='CO32-'
def test_reading_order():
    bs=[dict(bbox=(320,100,570,130),text='R1'),dict(bbox=(20,150,280,180),text='L2'),dict(bbox=(20,100,280,130),text='L1'),dict(bbox=(20,20,570,60),text='Title')]
    assert [x['text'] for x in order_blocks(bs,600)]==['Title','L1','L2','R1']

def sample_pdf(path):
    d=fitz.open();p=d.new_page(width=595,height=842)
    p.insert_text((30,40),'Molten salt electrochemistry',fontsize=18)
    p.insert_textbox(fitz.Rect(30,90,275,190),'Uranium electrodeposition produces dendrites in molten salt. Recovery is 94.6% at 453 K.',fontsize=12)
    p.insert_textbox(fitz.Rect(320,90,570,190),'The electrolyte contains UCl3 and NaAlCl4. This evidence suggests precipitation.',fontsize=12)
    p.insert_text((40,220),'U + 3e = U',fontsize=12)
    p.draw_rect(fitz.Rect(30,280,275,430),fill=(.3,.6,.8));p.insert_text((40,455),'Figure 1. Original vector illustration.',fontsize=12)
    d.save(path);d.close()

class FakeAI:
    calls=0
    def json(self,system,payload,max_tokens):
        self.calls+=1
        return dict(id=payload['id'],zh='对照译文：'+payload['text'],glossary=[])

def test_translation_resume_export_and_vector_integrity(store,tmp_path):
    pdf=tmp_path/'original.pdf';sample_pdf(pdf);client=FakeAI();r=Translator(store,client=client).run(pdf)
    assert r['audit']['translated_blocks']==r['audit']['total_blocks'];calls=client.calls
    r2=Translator(store,client=client).run(pdf);assert client.calls==calls
    with fitz.open(pdf) as a,fitz.open(r['bilingual']) as b:
        assert b.page_count>=2
        pa=a[0].get_pixmap(annots=False).samples;pb=b[0].get_pixmap(annots=False).samples
        # Font-resource rewriting can shift subpixel antialiasing by 1 gray level.
        assert sum(abs(x-y) for x,y in zip(pa,pb))/len(pa)<.03
        assert a[0].get_text()==b[0].get_text()
        assert 'P001' in b[1].get_text()
    with fitz.open(r['annotated']) as d: assert list(d[0].annots())
def test_scanned_pdf_not_silently_complete(store,tmp_path):
    pdf=tmp_path/'scan.pdf';d=fitz.open();d.new_page();d.save(pdf);d.close()
    with pytest.raises(ValueError,match='文本层'):extract_document(pdf)
def test_translation_cancel_preserves_cached_blocks(store,tmp_path):
    pdf=tmp_path/'p.pdf';sample_pdf(pdf);ev=threading.Event()
    class CancelAI(FakeAI):
        def json(self,*args,**kwargs):
            r=super().json(*args,**kwargs);ev.set();return r
    with pytest.raises(Cancelled):Translator(store,ev,client=CancelAI()).run(pdf)
    f=next((store.root/'translations').glob('*/translation.json'));s=json.loads(f.read_text('utf-8'));assert len(s['results'])==1
def test_malformed_translation_does_not_become_complete(store,tmp_path):
    pdf=tmp_path/'p.pdf';sample_pdf(pdf)
    class BadAI(FakeAI):
        def json(self,*args,**kwargs):return {'id':'wrong','zh':'错误'}
    with pytest.raises(ApiError):Translator(store,client=BadAI()).run(pdf)
def test_malformed_metrics_does_not_replace_file(store):
    before=(store.root/'journal_metrics.csv').read_bytes()
    with pytest.raises(ValueError):store.write_metrics([dict(journal='X',year='?',value='10')])
    assert (store.root/'journal_metrics.csv').read_bytes()==before
def test_total_network_failure_not_no_papers(store,monkeypatch):
    def fail(*a,**kw):raise ApiError('network unavailable')
    monkeypatch.setattr('services.request_json',fail)
    with pytest.raises(ApiError,match='所有检索来源'):Retriever(store).search()
def test_search_repeats_without_adding_results_to_library(store,monkeypatch):
    old=dict(SEEDS[0]);store.put(old)
    recent=dict(SEEDS[0],doi='10.1234/recent',date=date.today().isoformat(),seed=False)
    fake={'status':'ok','message':{'items':[{'DOI':recent['doi'],'title':[recent['title']],'abstract':recent['abstract'],'container-title':[recent['journal']],'ISSN':recent['issn'],'published':{'date-parts':[[date.today().year,date.today().month,date.today().day]]},'page':'1-6'}]}}
    monkeypatch.setattr('services.request_json',lambda url,**kw:fake if 'crossref' in url else {'results':[]})
    r=Retriever(store).search();assert len(r['selected'])==1
    assert len(Retriever(store).search()['selected'])==1
    assert [p['doi'] for p in store.papers()]==[old['doi']]
    assert store.paper(recent['doi']) is None
