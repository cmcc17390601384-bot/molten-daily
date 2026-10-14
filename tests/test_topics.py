from datetime import date
import pytest
from PySide6.QtWidgets import QApplication
from core import Store, SEEDS
from topics import topic_key, current_assessment
from services import DeepSeek, Retriever, eligibility, ApiError
from ui import Window
from analysis_report import PaperAnalyzer
from test_analysis import make_pdf, FakeAnalysis


def configured(tmp_path, **changes):
    store=Store(tmp_path/'data')
    store.update_settings(topic_mode='custom', research_topic='肿瘤免疫治疗中的耐药机制',
                          queries=['cancer immunotherapy'], nuclear_only=True, **changes)
    return store


def paper(**changes):
    return dict(dict(SEEDS[0], doi='10.1234/cancer', title='Cancer immunotherapy resistance',
                     abstract='Mechanisms of treatment resistance.', seed=False,
                     date=date.today().isoformat()), **changes)


def test_custom_filter_does_not_require_salt_or_nuclear(tmp_path):
    store=configured(tmp_path)
    assert eligibility(paper(), store)[0]
    assert not eligibility(SEEDS[0],store)[0]
    assert not eligibility(paper(journal='Unknown',issn=[]),store)[0]
    assert not eligibility(paper(retracted=True),store)[0]


def test_old_direction_assessment_cannot_reject_new_direction(tmp_path):
    store=configured(tmp_path); settings=store.settings()
    p=paper(ai={'score':0,'core_match':False,'topic_key':topic_key(settings)})
    assert not eligibility(p,store)[0]
    store.update_settings(research_topic='肿瘤免疫治疗机制与临床疗效')
    assert not current_assessment(p,store.settings())
    assert eligibility(p,store)[0]


def test_model_can_recognize_synonyms_without_literal_query_match(tmp_path,monkeypatch):
    store=configured(tmp_path, api_secret='mock-key-not-used'); seen=[]
    def response(url,**kw):
        seen.append(url)
        if 'crossref' not in url:return {'results':[]}
        return {'status':'ok','message':{'items':[{'DOI':'10.1234/tcell',
            'title':['T-cell therapy overcomes tumor resistance'],'abstract':'Immune checkpoint blockade.',
            'container-title':['Nature Communications'],'ISSN':['2041-1723'],
            'published':{'date-parts':[[date.today().year,date.today().month,date.today().day]]},'page':'1-6'}]}}
    monkeypatch.setattr('services.request_json',response)
    prompts=[]
    def assess(self,system,payload,max_tokens):
        prompts.append(system)
        return {'papers':[{'doi':payload[0]['doi'],'score':95,'core_match':True,'evidence':[payload[0]['title']]}]}
    monkeypatch.setattr(DeepSeek,'json',assess)
    result=Retriever(store).search()
    assert len(result['selected'])==1 and '肿瘤免疫治疗' in prompts[0]
    assert all('molten' not in url for url in seen)
    assert len(Retriever(store).search()['selected'])==1 and not store.papers()


def test_chinese_topic_automatically_generates_and_persists_queries(tmp_path,monkeypatch):
    store=configured(tmp_path, api_secret='mock-key-not-used')
    store.update_settings(queries=[])
    calls=[]
    def plan(self,system,payload,max_tokens):
        calls.append(payload)
        return {'queries':['cancer immunotherapy','tumor resistance']}
    monkeypatch.setattr(DeepSeek,'json',plan)
    monkeypatch.setattr('services.request_json',lambda url,**kw: {'status':'ok','message':{'items':[]}} if 'crossref' in url else {'results':[]})
    Retriever(store).search(); Retriever(store).search()
    assert len(calls)==1 and calls[0]['research_direction']=='肿瘤免疫治疗中的耐药机制'
    assert store.settings()['queries']==['cancer immunotherapy','tumor resistance']


def test_missing_api_and_invalid_generated_queries_fail_clearly(tmp_path,monkeypatch):
    store=configured(tmp_path);store.update_settings(queries=[])
    with pytest.raises(ApiError,match='英文检索词'):Retriever(store).search()
    monkeypatch.setattr(DeepSeek,'json',lambda *args: {'queries':'not a list'})
    with pytest.raises(ApiError,match='有效检索词'):DeepSeek(store).plan_queries()


def test_manual_queries_work_without_api(tmp_path,monkeypatch):
    store=configured(tmp_path,ai_ranking=False)
    def response(url,**kw):
        if 'crossref' not in url:return {'results':[]}
        return {'status':'ok','message':{'items':[{'DOI':'10.1234/manual','title':['Cancer immunotherapy'],
            'container-title':['Nature Communications'],'ISSN':['2041-1723'],
            'published':{'date-parts':[[date.today().year,date.today().month,date.today().day]]}}]}}
    monkeypatch.setattr('services.request_json',response)
    assert len(Retriever(store).search()['selected'])==1


def test_direction_ui_persists_and_clears_stale_queries(tmp_path):
    app=QApplication.instance() or QApplication([]);store=Store(tmp_path/'data')
    w=Window(store,smoke=True);w.topic_input.setText('肿瘤免疫治疗');w.topic_input.textEdited.emit('肿瘤免疫治疗')
    assert not w.queries.toPlainText()
    w.queries.setPlainText('cancer immunotherapy');assert w.save_settings()
    w.close();w.deleteLater();app.processEvents();w=Window(store,smoke=True)
    assert w.topic_input.text()=='肿瘤免疫治疗' and w.queries.toPlainText()=='cancer immunotherapy'
    w.topic_input.setText('钠离子电池');w.topic_input.textEdited.emit('钠离子电池')
    assert not w.queries.toPlainText() and w.save_settings()
    w.close();w.deleteLater();app.processEvents()


def test_analysis_cache_isolated_by_research_direction(tmp_path):
    store=configured(tmp_path); pdf=tmp_path/'paper.pdf';make_pdf(pdf)
    client=FakeAnalysis();a=PaperAnalyzer(store,client=client).run(pdf)
    store.update_settings(research_topic='材料科学')
    b=PaperAnalyzer(store,client=client).run(pdf)
    assert a['folder']!=b['folder'] and client.calls==2
    assert b['state']['research_topic']=='材料科学'
