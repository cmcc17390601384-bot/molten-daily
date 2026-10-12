import json,threading
from pathlib import Path
from datetime import date,timedelta
import fitz,pytest
from core import Store
from services import DeepSeek,BudgetExceeded,Cancelled
from margins import MarginAnnotator,cache_folder
from translation import extract_document

def three_blocks(path):
    doc=fitz.open();page=doc.new_page()
    for i,y in enumerate((80,180,280)):
        page.insert_text((40,y),f'Paragraph {i+1}: Uranium electrodeposition in molten salt proceeds at 453 K.')
    doc.save(path);doc.close()

def mock_api(monkeypatch,calls):
    monkeypatch.setattr(DeepSeek,'headers',lambda self:{})
    def response(url,**kwargs):
        payload=json.loads(kwargs['data']['messages'][1]['content']);calls.append(payload['id'])
        result={'id':payload['id'],'zh':'本段的完整译文：'+payload['text'],'items':[{'kind':'word','quote':'electrodeposition','ipa':'/test/','zh':'电沉积'}]}
        return {'usage':{'total_tokens':7800},'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}
    monkeypatch.setattr('services.request_json',response)

def test_decline_budget_mid_page_then_resume_without_repeating_paid_blocks(tmp_path,monkeypatch):
    store=Store(tmp_path/'data');store.update_settings(daily_token_limit=9000)
    pdf=tmp_path/'p.pdf';three_blocks(pdf);calls=[];mock_api(monkeypatch,calls)
    prompts=[];store.budget_handler=lambda r,c:prompts.append(r) or False
    with pytest.raises(BudgetExceeded):MarginAnnotator(store).run(pdf)
    folder=cache_folder(store,extract_document(pdf));state=json.loads((folder/'margins.json').read_text('utf-8'))
    assert len(state['results'])==1 and len(calls)==1 and len(prompts)==1
    store.update_settings(daily_token_limit=1000000,model='another-model',research_topic='different topic')
    store.budget_handler=None
    result=MarginAnnotator(store).run(pdf)
    assert len(calls)==3 and len(set(calls))==3 and result['audit']['categories']['paragraph']==3
    MarginAnnotator(Store(store.root)).run(pdf);assert len(calls)==3

def test_accept_budget_continues_same_job_and_today_only(tmp_path,monkeypatch):
    store=Store(tmp_path/'data');store.update_settings(daily_token_limit=9000)
    pdf=tmp_path/'p.pdf';three_blocks(pdf);calls=[];mock_api(monkeypatch,calls);prompts=[]
    store.budget_handler=lambda r,c:prompts.append(r) or True
    result=MarginAnnotator(store).run(pdf)
    assert len(calls)==3 and len(prompts)==1 and store.daily_budget()==1009000
    assert result['audit']['categories']['paragraph']==3
    store.update_settings(token_extra_day=(date.today()-timedelta(days=1)).isoformat())
    assert store.daily_budget()==9000

def test_stop_after_response_preserves_paid_result(tmp_path,monkeypatch):
    store=Store(tmp_path/'data');pdf=tmp_path/'p.pdf';three_blocks(pdf);calls=[];ev=threading.Event()
    monkeypatch.setattr(DeepSeek,'headers',lambda self:{})
    def response(url,**kwargs):
        p=json.loads(kwargs['data']['messages'][1]['content']);calls.append(p['id']);ev.set()
        return {'usage':{'total_tokens':100},'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'id':p['id'],'zh':p['text'],'items':[]})}}]}
    monkeypatch.setattr('services.request_json',response)
    with pytest.raises(Cancelled):MarginAnnotator(store,ev).run(pdf)
    state=json.loads((cache_folder(store,extract_document(pdf))/'margins.json').read_text('utf-8'))
    assert len(state['results'])==1 and len(calls)==1

def test_old_budget_migrates_once_and_new_manual_limit_is_retained(tmp_path):
    folder=tmp_path/'data';folder.mkdir();(folder/'settings.json').write_text(json.dumps({'daily_token_limit':80000}),'utf-8')
    store=Store(folder);assert store.settings()['daily_token_limit']==1000000
    store.update_settings(daily_token_limit=500000);assert Store(folder).settings()['daily_token_limit']==500000

def test_legacy_translation_is_reused_for_new_margin_notes(tmp_path):
    store=Store(tmp_path/'data');pdf=tmp_path/'p.pdf';three_blocks(pdf);doc=extract_document(pdf)
    old=store.root/'translations'/'old';old.mkdir()
    (old/'translation.json').write_text(json.dumps({'document':doc,'results':{u['id']:{'protected_zh':u['protected']} for u in doc['units']}}),'utf-8')
    class Client:
        def json(self,system,payload,max_tokens):
            assert payload['translation_exists'] is True
            return {'id':payload['id'],'zh':'','items':[]}
    result=MarginAnnotator(store,client=Client()).run(pdf)
    assert result['audit']['categories']['paragraph']==3

def test_invalid_numbers_are_not_saved_as_completed(tmp_path):
    store=Store(tmp_path/'data');pdf=tmp_path/'p.pdf';three_blocks(pdf)
    class Client:
        def json(self,system,payload,max_tokens):return {'id':payload['id'],'zh':'遗漏数值的译文','items':[]}
    with pytest.raises(Exception,match='保护标记'):MarginAnnotator(store,client=Client()).run(pdf)
    cache=cache_folder(store,extract_document(pdf))/'margins.json'
    assert not cache.exists() or not json.loads(cache.read_text('utf-8'))['results']

def test_worker_budget_prompt_resumes_without_deadlock(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication,QMessageBox
    from PySide6.QtTest import QTest
    from ui import Window
    import time
    app=QApplication.instance() or QApplication([]);store=Store(tmp_path/'data');store.update_settings(daily_token_limit=9000)
    pdf=tmp_path/'p.pdf';three_blocks(pdf);calls=[];mock_api(monkeypatch,calls)
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
    w=Window(store,smoke=True);completed=[]
    w.run_job('test',lambda c,p:MarginAnnotator(store,c,p).run(pdf),completed.append)
    until=time.monotonic()+10
    while w.worker.isRunning() and time.monotonic()<until:QTest.qWait(20)
    app.processEvents()
    assert not w.worker.isRunning() and completed and len(calls)==3
    assert store.budget_handler is None and store.daily_budget()==1009000
    w.close();w.deleteLater();app.processEvents()

def test_bad_markers_are_repaired_once_and_cached(tmp_path):
    store=Store(tmp_path/'data');pdf=tmp_path/'p.pdf';three_blocks(pdf)
    class Client:
        calls=0
        def json(self,system,payload,max_tokens):
            self.calls+=1
            return {'id':payload['id'],'zh':payload['text'] if 'required_markers' in payload else '不合格译文','items':[]}
    client=Client();result=MarginAnnotator(store,client=client).run(pdf)
    assert client.calls==6 and result['audit']['categories']['paragraph']==3
    MarginAnnotator(store,client=client).run(pdf);assert client.calls==6
