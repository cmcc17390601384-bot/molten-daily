from datetime import date
from urllib.parse import parse_qs,urlparse
import pytest
from PySide6.QtWidgets import QApplication
from core import Store,SEEDS
from services import Retriever,DeepSeek,ApiError,eligibility
from ui import Window
from journal_metrics import parse_nature_metrics

def test_search_numeric_criteria_survive_widget_roundtrip():
    from topics import search_key
    assert search_key({'min_if':13,'days':365})==search_key({'min_if':13.0,'days':365})
    assert search_key({'min_if':13})!=search_key({'min_if':13.1})

def raw(i=0,title='Molten salt electrochemical separation of uranium'):
    return {'DOI':f'10.1234/new{i}','title':[title],'container-title':['Nature Communications'],'ISSN':['2041-1723'],
        'abstract':'Electrodeposition separates uranium from molten salt during nuclear fuel recycling.',
        'published':{'date-parts':[[date.today().year,date.today().month,date.today().day]]}}

def setup(tmp_path):
    s=Store(tmp_path);s.update_settings(queries=['molten salt electrochemical'],search_pages=3,ai_ranking=False);return s

def test_new_search_never_reuses_other_run_or_entire_library(tmp_path,monkeypatch):
    s=setup(tmp_path);old=dict(SEEDS[0],doi='10.1234/old',seed=False,date=date.today().isoformat());s.put(old)
    s.save_search_results(s.settings(),[old['doi']],state='complete')
    monkeypatch.setattr('services.request_json',lambda url,**kw: {'status':'ok','message':{'items':[raw()]}} if 'crossref' in url else {'results':[]})
    r=Retriever(s).search(on_demand=True);assert [p['doi'] for p in r['selected']]==['10.1234/new0']
    assert s.paper(old['doi']) and s.search_results()['dois']==['10.1234/new0']
    assert s.paper('10.1234/new0') is None and s.found_paper('10.1234/new0')
    monkeypatch.setattr('services.request_json',lambda *a,**k:(_ for _ in ()).throw(ApiError('failed')))
    with pytest.raises(ApiError):Retriever(s).search(on_demand=True)
    assert s.search_results()['dois']==[] and s.paper(old['doi'])

def test_pagination_and_on_demand_has_no_three_or_twenty_paper_cap(tmp_path,monkeypatch):
    s=setup(tmp_path);calls=[]
    def request(url,**kw):
        if 'crossref' not in url:return {'results':[]}
        cursor=parse_qs(urlparse(url).query)['cursor'][0];calls.append(cursor)
        return {'status':'ok','message':{'items':[raw(i) for i in (range(100) if cursor=='*' else range(100,127))],'next-cursor':'second'}}
    monkeypatch.setattr('services.request_json',request)
    r=Retriever(s).search(on_demand=True)
    assert calls==['*','second'] and len(r['selected'])==127 and r['fetched']==127

def test_theme_and_filters_invalidate_visible_results_before_search(tmp_path):
    s=setup(tmp_path);p=dict(SEEDS[0],date=date.today().isoformat());s.put(p);s.save_search_results(s.settings(),[p['doi']],state='complete')
    app=QApplication.instance() or QApplication([]);w=Window(s,smoke=True)
    assert w.lib_table.rowCount()==1
    w.topic_input.setText('肿瘤免疫治疗');assert w.lib_table.rowCount()==0 and w.library_table.rowCount()==1
    w.topic_input.setText('熔盐电化学，优先核能、核燃料循环和锕系元素分离');assert w.lib_table.rowCount()==1
    w.days.setValue(365);assert w.lib_table.rowCount()==0
    w.days.setValue(s.settings()['days']);assert w.lib_table.rowCount()==1
    w.search_depth.setValue(4);assert w.lib_table.rowCount()==0
    assert not hasattr(w,'strict_view')
    w.close();w.deleteLater();app.processEvents()

@pytest.mark.parametrize('kind',['unrelated','missing','invented','low_score','failed'])
def test_semantic_rejection_and_missing_evidence_never_show(tmp_path,monkeypatch,kind):
    s=setup(tmp_path);s.update_settings(topic_mode='custom',research_topic='熔盐中的铀分离',api_secret='mock',ai_ranking=True)
    monkeypatch.setattr('services.request_json',lambda url,**kw:{'status':'ok','message':{'items':[raw()]}} if 'crossref' in url else {'results':[]})
    def ai(self,system,payload,max_tokens):
        if kind=='failed':raise ApiError('provider failed')
        return {'papers':[dict(doi=payload[0]['doi'],score=40 if kind=='low_score' else 95,core_match=kind!='unrelated',
            evidence=[] if kind=='missing' else ['invented unrelated source evidence'] if kind=='invented' else [payload[0]['title']])]}
    monkeypatch.setattr(DeepSeek,'json',ai)
    r=Retriever(s).search(on_demand=True);assert not r['selected'] and not s.search_results()['dois']

def test_more_than_twenty_candidates_assessed_and_cached(tmp_path,monkeypatch):
    s=setup(tmp_path);s.update_settings(api_secret='mock',ai_ranking=True);calls=[]
    monkeypatch.setattr('services.request_json',lambda url,**kw:{'status':'ok','message':{'items':[raw(i) for i in range(29)]}} if 'crossref' in url else {'results':[]})
    def ai(self,system,payload,max_tokens):
        calls.extend(p['doi'] for p in payload)
        return {'papers':[dict(doi=p['doi'],score=95,core_match=True,evidence=[p['title']]) for p in payload]}
    monkeypatch.setattr(DeepSeek,'json',ai)
    assert len(Retriever(s).search(on_demand=True)['selected'])==29 and len(calls)==29
    assert len(Retriever(s).search(on_demand=True)['selected'])==29 and len(calls)==29

def test_latest_jif_exact_year_and_conflicting_newest_fail_closed(tmp_path):
    s=setup(tmp_path);assert int(s.metric(SEEDS[0],0)['year'])==2025 and float(s.metric(SEEDS[0],2024)['value'])==15.7
    assert s.metric(SEEDS[0],2022) is None
    rows=s.metrics();newest=dict(s.metric(SEEDS[0],0),value='99');s.write_metrics(rows+[newest]);assert s.metric(SEEDS[0],0) is None
    app=QApplication.instance() or QApplication([]);w=Window(s,smoke=True)
    assert w.year.value()==0 and w.year.findData(2025)>=0 and w.year.findData(date.today().year)<0
    w.close();w.deleteLater();app.processEvents()

def test_publisher_parser_never_uses_five_year_or_undated_metrics():
    page='<h2 id="citation-impact">2025&nbsp;Journal Metrics</h2><table><tr><th>Journal</th><th>Journal Impact Factor</th><th>5-year Journal Impact Factor</th></tr><tr><td>Nature Communications</td><td>18.1</td><td>18.9</td></tr></table>'
    rows=parse_nature_metrics(page);assert len(rows)==1 and rows[0]['year']==2025 and rows[0]['value']==18.1
    with pytest.raises(ValueError):parse_nature_metrics(page.replace('2025&nbsp;',''))
    with pytest.raises(ValueError):parse_nature_metrics(page.replace('<th>Journal Impact Factor</th>','<th>CiteScore</th>'))

def test_computer_vision_jif_uses_two_year_metric_and_actual_year(tmp_path):
    s=setup(tmp_path)
    ijcv=s.metric({'journal':'International Journal of Computer Vision','issn':['1573-1405']},0)
    assert float(ijcv['value'])==10.3 and int(ijcv['year'])==2025
    assert float(s.metric({'journal':'Artificial Intelligence Review'},0)['value'])>13
    assert float(s.metric({'journal':'IEEE Transactions on Pattern Analysis and Machine Intelligence'},0)['value'])>13
    assert s.metric({'journal':'Artificial Intelligence Review'},2024) is None

def test_search_requires_manual_add_and_supports_bulk_without_duplicates(tmp_path,monkeypatch):
    from PySide6.QtCore import QItemSelectionModel
    s=setup(tmp_path)
    monkeypatch.setattr('services.request_json',lambda url,**kw:{'status':'ok','message':{'items':[raw(1),raw(2)]}} if 'crossref' in url else {'results':[]})
    Retriever(s).search(on_demand=True);assert not s.papers()
    app=QApplication.instance() or QApplication([]);w=Window(s,smoke=True)
    assert w.library_table.rowCount()==0 and w.lib_table.rowCount()==2
    w.lib_table.selectRow(0);assert w.search_add.isEnabled() and not w.search_get.isEnabled() and not w.search_attach.isEnabled()
    w.lib_table.selectionModel().select(w.lib_table.model().index(1,0),QItemSelectionModel.Select|QItemSelectionModel.Rows)
    assert len(w.search_ids())==2 and not w.search_doi.isEnabled()
    w.add_search_selection();assert len(s.papers())==2 and w.library_table.rowCount()==2
    assert s.add_search_to_library(s.search_results()['dois'])==0
    w.topic_input.setText('另一个方向');assert w.lib_table.rowCount()==0 and w.library_table.rowCount()==2
    w.close();w.deleteLater();app.processEvents()

def test_targeted_journal_recall_stays_inside_verified_whitelist(tmp_path,monkeypatch):
    s=setup(tmp_path);s.update_settings(api_secret='mock',ai_ranking=True,search_pages=1)
    def request(url,**kw):
        if 'crossref' not in url:return {'results':[]}
        if 'query.container-title' in url:return {'status':'ok','message':{'items':[raw(999)]}}
        return {'status':'ok','message':{'items':[dict(raw(i),**{'container-title':['Unknown'],'ISSN':[]}) for i in range(100)]}}
    def ai(self,system,payload,max_tokens):
        if isinstance(payload,dict):return {'journals':['Invented Journal','Nature Communications']}
        return {'papers':[dict(doi=p['doi'],score=95,core_match=True,evidence=[p['title']]) for p in payload]}
    monkeypatch.setattr('services.request_json',request);monkeypatch.setattr(DeepSeek,'json',ai)
    r=Retriever(s).search(on_demand=True)
    assert r['targeted_added']==1 and r['unknown_jif']==100 and len(r['selected'])==1
    assert not s.papers() and r['selected'][0]['doi']=='10.1234/new999'
