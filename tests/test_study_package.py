import json,zipfile,shutil
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication,QFileDialog
from core import Store
from margins import MarginAnnotator,cache_folder
from study_package import export_package,import_package,pdf_hash
from test_interactive_reader import source,Client
from ui import Window

def package(tmp_path):
    first=Store(tmp_path/'first');pdf=tmp_path/'original.pdf';source(pdf)
    MarginAnnotator(first,client=Client()).run(pdf,interactive=True)
    first.update_settings(api_secret='DO-NOT-SHARE-SECRET',email='private@example.com')
    out=tmp_path/'share.mdstudy';export_package(first,pdf,out)
    return first,pdf,out

def test_cross_computer_rename_import_and_no_repeat_translation(tmp_path):
    first,pdf,out=package(tmp_path);second=Store(tmp_path/'second');renamed=tmp_path/'different-name.pdf';shutil.copy2(pdf,renamed)
    result=import_package(second,out);assert result['added']==2
    client=Client();r=MarginAnnotator(second,client=client).run(renamed,interactive=True)
    assert client.calls==0 and second.tokens()==0 and r['paragraphs']==2
    assert import_package(second,out)['added']==0
    with zipfile.ZipFile(out) as z:
        text=z.read('study.json').decode('utf-8')
        assert 'DO-NOT-SHARE-SECRET' not in text and 'private@example.com' not in text and str(tmp_path) not in text
        assert json.loads(text)['document']['source']==''
        assert set(z.namelist())=={'manifest.json','study.json'}

def test_wrong_pdf_not_applied_and_local_work_not_overwritten(tmp_path):
    first,pdf,out=package(tmp_path);second=Store(tmp_path/'second');r=import_package(second,out)
    cache=Path(r['cache']);state=json.loads(cache.read_text('utf-8'));uid=next(iter(state['results']));state['results'][uid]['zh']='我自己的译文';cache.write_text(json.dumps(state),'utf-8')
    assert import_package(second,out)['kept']==2
    assert json.loads(cache.read_text('utf-8'))['results'][uid]['zh']=='我自己的译文'
    different=tmp_path/'different.pdf';source(different,90)
    app=QApplication.instance() or QApplication([]);w=Window(second,smoke=True);w.load_pdf(different)
    assert not w.study_state
    w.load_pdf(pdf);assert len(w.study_state['results'])==2
    w.close();w.deleteLater();app.processEvents()

@pytest.mark.parametrize('damage',['path','version','hash','coords','json'])
def test_invalid_packages_rejected_without_writes(tmp_path,damage):
    first,pdf,out=package(tmp_path)
    with zipfile.ZipFile(out) as z:manifest=json.loads(z.read('manifest.json'));state=json.loads(z.read('study.json'))
    if damage=='version':manifest['version']=900
    if damage=='hash':manifest['pdf_sha256']='0'*64
    if damage=='coords':next(iter(state['results'].values()))['items'][0]['rects']=[[0,0,float('nan'),5]]
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('manifest.json',json.dumps(manifest));z.writestr('study.json','bad json' if damage=='json' else json.dumps(state))
        if damage=='path':z.writestr('../outside.txt','unexpected')
    second=Store(tmp_path/'second')
    with pytest.raises(ValueError):import_package(second,out)
    assert not list((second.root/'translations').iterdir()) and not (tmp_path/'outside.txt').exists()

def test_ui_package_import_matches_existing_library_pdf(tmp_path,monkeypatch):
    from library import import_pdf
    first,pdf,out=package(tmp_path);second=Store(tmp_path/'second');p=import_pdf(second,pdf)
    app=QApplication.instance() or QApplication([]);w=Window(second,smoke=True)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a:(str(out),''))
    w.import_study_package()
    assert w.pdf_path==str(Path(p['pdf']).resolve()) and w.tabs.currentIndex()==2 and w.package_export_action.isEnabled()
    assert len(w.study_state['results'])==2 and second.tokens()==0
    w.close();w.deleteLater();app.processEvents()
