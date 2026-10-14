from datetime import date
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtTest import QTest
from core import Store,SEEDS
from ui import Window
from test_core import sample_pdf
import threading

def test_simple_native_workflow_and_zoom(tmp_path):
    app=QApplication.instance() or QApplication([]);store=Store(tmp_path/'data');store.put(dict(SEEDS[0],date=date.today().isoformat()))
    store.save_search_results(store.settings(),[SEEDS[0]['doi']],state='complete')
    w=Window(store,smoke=True);w.show();QTest.qWait(70)
    assert w.tabs.count()==4 and [w.tabs.tabText(i) for i in range(4)]==['找文献','图书馆','交互精读','设置']
    assert w.lib_table.rowCount()==1
    assert w.queries.toPlainText().splitlines()==store.settings()['queries']
    w.lib_table.selectRow(0);app.processEvents();assert w.selected
    w.days.setValue(60);assert w.save_settings() and store.settings()['days']==60
    pdf=tmp_path/'sample.pdf';sample_pdf(pdf);w.tabs.setCurrentIndex(2);w.load_pdf(pdf)
    w.zoom.setValue(100);width=w.pdf_label.pixmap().width();w.change_zoom(50)
    assert w.pdf_label.pixmap().width()>width*1.45
    w.change_zoom(-100);assert w.pdf_label.pixmap().width()<width*.55
    w.fit_width();assert w.fit_mode
    w.filter.setText('not-a-paper');assert w.lib_table.rowCount()==0
    w.close();w.deleteLater();app.processEvents()

def test_budget_dialog_yes_and_no(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([]);w=Window(Store(tmp_path/'data'),smoke=True)
    for answer,expected in [(QMessageBox.Yes,True),(QMessageBox.No,False)]:
        monkeypatch.setattr(QMessageBox,'question',lambda *args:answer)
        r=dict(used=10,limit=10,estimate=100,extra=1000000,event=threading.Event(),approved=False)
        w.confirm_budget(r);assert r['approved']==expected and r['event'].is_set()
    w.close();w.deleteLater();app.processEvents()
