import json,threading
from pathlib import Path
import fitz,pytest
from PySide6.QtCore import QPointF,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QMessageBox
from core import Store
from library import import_pdf
from margins import MarginAnnotator
from study_reader import page_notes
from ui import Window

class Client:
    def __init__(self):self.calls=0
    def json(self,system,payload,max_tokens):
        self.calls+=1
        return {'id':payload['id'],'zh':'电沉积在熔盐中进行。'+payload['text'],
            'items':[{'kind':'word','quote':'electrodeposition','ipa':'/ɪˌlektroʊˌdepəˈzɪʃən/','zh':'电沉积'},
                     {'kind':'phrase','quote':'molten salt','ipa':'','zh':'熔盐'}]}

def source(path,rotate=0):
    with fitz.open() as doc:
        for _ in range(2):
            p=doc.new_page();p.insert_text((60,100),'Uranium electrodeposition in molten salt proceeds at 453 K.');p.set_rotation(rotate)
        doc.save(path)

def test_interactive_reuses_cache_without_pdf_export(tmp_path,monkeypatch):
    s=Store(tmp_path/'data');pdf=tmp_path/'p.pdf';source(pdf);client=Client()
    r=MarginAnnotator(s,client=client).run(pdf,interactive=True)
    assert client.calls==2 and r['paragraphs']==2 and 'pdf' not in r
    monkeypatch.setattr('margins.export_margins',lambda *a,**k:pytest.fail('Interactive reading must not paginate/export'))
    MarginAnnotator(s,client=client).run(pdf,interactive=True);assert client.calls==2
    assert not list(Path(r['folder']).glob('*.pdf'))
    # Paid results can be read immediately even before an interrupted run builds pages.
    state=json.loads(Path(r['cache']).read_text('utf-8'));state.pop('pages');state['results'].pop(next(reversed(state['results'])))
    assert len(page_notes(state,0))==3 and not page_notes(state,1)

@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_hover_geometry_zoom_and_original_page_count(tmp_path,rotation):
    app=QApplication.instance() or QApplication([]);s=Store(tmp_path/'data');pdf=tmp_path/'p.pdf';source(pdf,rotation)
    MarginAnnotator(s,client=Client()).run(pdf,interactive=True)
    w=Window(s,smoke=True);w.show();w.tabs.setCurrentIndex(2);w.load_pdf(pdf);QTest.qWait(150)
    assert len(w.source_doc)==2 and w.page_label.text()=='1 / 2'
    for zoom in (70,150,250,400):
        w.zoom.setValue(zoom)
        i,rect=next((i,r) for i,r in w.pdf_label.targets if w.study_entries[i]['kind']=='word')
        hits=w.pdf_label.hit_test(rect.center());assert hits[0]==i
        # Send a real Qt mouse event, including offscreen portions of the scroll canvas.
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        event=QMouseEvent(QEvent.MouseMove,rect.center(),rect.center(),Qt.NoButton,Qt.NoButton,Qt.NoModifier)
        app.sendEvent(w.pdf_label,event)
        assert '电沉积' in w.note_panel.toPlainText() and 'ɪ' in w.note_panel.toPlainText()
        assert '电沉积在熔盐中进行' in w.translation_panel.toPlainText()
        assert w.pdf_label.pixmap().width()*w.pdf_label.pixmap().height()<8_020_000
        assert abs(w.pdf_label.width()-w.source_doc[0].rect.width*zoom/100*1.3333)<1
    w.move_page(1);assert w.page_label.text()=='2 / 2'
    w.move_page(1);assert w.page_label.text()=='2 / 2'
    w.close();w.deleteLater();app.processEvents()

def test_bulk_delete_restore_and_reimport_keeps_attachments(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([]);s=Store(tmp_path/'data');pdf=tmp_path/'p.pdf';source(pdf)
    p=import_pdf(s,pdf);s.put(dict(doi='10.1234/second',title='Second paper'));cid=s.save_collection('核能');s.assign_collection([p['doi']],cid)
    s.set_flag(p['doi'],'note','保留学习笔记');r=MarginAnnotator(s,client=Client()).run(pdf,interactive=True);paid=Path(r['cache']).read_bytes()
    w=Window(s,smoke=True);w.tabs.setCurrentIndex(1);w.library_table.selectAll()
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.No);w.delete_selected();assert len(s.papers())==2
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.Yes);w.delete_selected()
    assert not s.papers() and s.collections()[0]['count']==0 and w.library_table.rowCount()==0
    s.put(dict(p,title='Re-fetched'));assert s.paper(p['doi']) is None
    w.category.setCurrentIndex(w.category.findData('trash'));w.library_table.selectAll();assert w.library_delete.text()=='恢复选中'
    assert not w.library_read.isEnabled() and not w.library_classify.isEnabled()
    w.delete_selected();assert len(s.papers())==2 and s.collections()[0]['count']==1
    assert s.paper(p['doi'])['note']=='保留学习笔记' and Path(p['pdf']).exists() and Path(r['cache']).read_bytes()==paid
    s.delete_papers([p['doi']]);assert import_pdf(s,pdf)['doi']==p['doi'] and len(s.papers())==2
    w.close();w.deleteLater();app.processEvents()

def test_existing_database_migration(tmp_path):
    import sqlite3
    db=tmp_path/'library.sqlite3'
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE papers(doi TEXT PRIMARY KEY,payload TEXT,added TEXT,seen INTEGER DEFAULT 0,favorite INTEGER DEFAULT 0,notified INTEGER DEFAULT 0,pdf TEXT DEFAULT '',note TEXT DEFAULT '')")
        c.execute('INSERT INTO papers(doi,payload,added) VALUES(?,?,?)',('10.1234/a',json.dumps({'doi':'10.1234/a','title':'Old'}),'2026-10-13'))
    s=Store(tmp_path);assert s.paper('10.1234/a')['deleted']==0
    assert s.delete_papers(['10.1234/a','10.1234/a'])==1
    assert Store(tmp_path).restore_papers(['10.1234/a'])==1
