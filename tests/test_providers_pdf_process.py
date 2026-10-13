import json,threading,time
from pathlib import Path
import fitz,pytest
from core import Store,protect,unprotect
from providers import profiles,remember
from services import DeepSeek,ApiError,Cancelled
from pdf_process import run_margin_process
from test_core import sample_pdf

def cached_document(tmp_path,count=50):
    source=tmp_path/'source.pdf';sample_pdf(source)
    state=dict(document=dict(source=str(source),hash='test'),pages={'0':[dict(kind='paragraph',number=f'P{i}',quote='',ipa='',zh='整段译文：电化学测试。'*8,rects=[[30,90,275,190]]) for i in range(count)]})
    cache=tmp_path/'margins.json';cache.write_text(json.dumps(state,ensure_ascii=False),'utf-8');return cache

def test_isolated_export_progress_and_shared_fonts(tmp_path):
    cache=cached_document(tmp_path);messages=[]
    r=run_margin_process(Store(tmp_path/'data'),'',threading.Event(),messages.append,export_cache=str(cache))
    assert r['audit']['items']==50 and any(x.startswith('保存') for x in messages)
    with fitz.open(r['pdf']) as pdf:
        assert len(pdf)>1 and len({font[0] for p in pdf for font in p.get_fonts()})<15
    assert Path(r['pdf']).stat().st_size<15*1024*1024

def test_cancel_export_retains_cache_and_previous_output(tmp_path):
    cache=cached_document(tmp_path,100);before=cache.read_bytes();output=tmp_path/'页边精读批注_原版左右留白.pdf';output.write_bytes(b'previous output')
    event=threading.Event();start=time.monotonic()
    def progress(s):
        if s.startswith('排版'):event.set()
    with pytest.raises((RuntimeError,Cancelled),match='暂停|停止'):run_margin_process(Store(tmp_path/'data'),'',event,progress,export_cache=str(cache))
    assert time.monotonic()-start<10 and cache.read_bytes()==before and output.read_bytes()==b'previous output'
    r=run_margin_process(Store(tmp_path/'data'),'',threading.Event(),lambda _:None,export_cache=str(cache))
    assert r['audit']['items']==100

def test_provider_secrets_scoped_and_encrypted(tmp_path):
    s=Store(tmp_path);settings=dict(s.settings(),api_secret=protect('provider-one-secret'));p=profiles(settings)
    remember(p,'https://second.example/v1','new-model','provider-two-secret')
    assert unprotect(p[settings['base_url']]['api_secret'])=='provider-one-secret'
    assert unprotect(p['https://second.example/v1']['api_secret'])=='provider-two-secret'
    assert 'provider-two-secret' not in json.dumps(p)

def test_dynamic_models_accept_new_ids_and_deduplicate(tmp_path,monkeypatch):
    store=Store(tmp_path);store.update_settings(api_secret=protect('fake'))
    monkeypatch.setattr('services.request_json',lambda *a,**kw:{'data':[{'id':'future-model-2030'},{'id':'future-model-2030'},{'id':'another'},None,{'id':''}]})
    assert DeepSeek(store).models()==['future-model-2030','another']
    monkeypatch.setattr('services.request_json',lambda *a,**kw:{'data':[]})
    with pytest.raises(ApiError,match='有效模型列表'):DeepSeek(store).models()

def test_ui_provider_switch_never_reuses_other_key(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from ui import Window
    app=QApplication.instance() or QApplication([]);store=Store(tmp_path);store.update_settings(api_secret=protect('first-key'))
    w=Window(store,smoke=True);w.provider.setCurrentIndex(1);w.model_timer.stop()
    assert w.profile_url=='https://api.siliconflow.cn/v1' and not w.secret.text()
    assert w.save_settings(allow_empty_model=True) and store.settings()['api_secret']==''
    w.secret.setText('second-key');w.model.setCurrentText('future-model');assert w.save_settings()
    w.provider.setCurrentIndex(0);w.model_timer.stop();assert w.save_settings()
    assert unprotect(store.settings()['api_secret'])=='first-key'
    w.provider.setCurrentIndex(1);w.model_timer.stop();assert w.model.currentText()=='future-model'
    assert w.save_settings() and unprotect(store.settings()['api_secret'])=='second-key'
    w.close();w.deleteLater();app.processEvents()

def test_ui_model_refresh_does_not_need_research_topic(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from ui import Window
    app=QApplication.instance() or QApplication([]);w=Window(Store(tmp_path),smoke=True)
    w.topic_input.clear();w.secret.setText('test-key');w.model.setCurrentText('old')
    monkeypatch.setattr(w,'run_job',lambda name,fn,callback:callback(['brand-new','fresh-next']))
    w.test_api();assert w.model.count()==2 and w.store.settings()['model']=='brand-new'
    assert w.api_profiles[w.profile_url]['models']==['brand-new','fresh-next']
    w.close();w.deleteLater();app.processEvents()

def test_citation_export_single_and_batch_no_office_actions(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication,QFileDialog,QMenu
    from ui import Window
    from test_library import paper
    from citations import read_records
    app=QApplication.instance() or QApplication([]);s=Store(tmp_path/'data');s.put(paper());s.put(dict(paper(),doi='10.1234/batch'))
    w=Window(s,smoke=True);w.tabs.setCurrentIndex(1)
    text=' '.join(a.text() for menu in w.findChildren(QMenu) for a in menu.actions())
    assert 'Word' not in text and '发送到 EndNote' not in text
    target=tmp_path/'batch.ris';monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a:(str(target),'RIS (*.ris)'))
    w.library_table.selectAll();w.export_citations();assert len(read_records(target))==2
    w.library_table.clearSelection();w.library_table.selectRow(0);w.export_citations();assert len(read_records(target))==1
    w.close();w.deleteLater();app.processEvents()
