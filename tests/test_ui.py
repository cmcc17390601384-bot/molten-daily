import sys,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from core import Store
from ui import Window

def test_native_workflow(tmp_path):
    app=QApplication.instance() or QApplication([])
    store=Store(tmp_path/'data');w=Window(store,smoke=True);w.show();QTest.qWait(200)
    w.seed();assert w.lib_table.rowCount()==2
    w.lib_table.selectRow(0);app.processEvents();assert w.selected
    w.favorite();assert sum(p['favorite'] for p in store.papers())==1
    w.mark_read();assert sum(p['seen'] for p in store.papers())==1
    w.nav.setCurrentRow(4);w.days.setValue(60);assert w.save_settings();assert store.settings()['days']==60
    w.filter.setText('nonexistent-paper');assert w.lib_table.rowCount()==0
    w.close();w.deleteLater();app.processEvents()

def test_daily_timer_only_attempts_once_per_day(tmp_path):
    app=QApplication.instance() or QApplication([])
    store=Store(tmp_path/'data');store.update_settings(schedule_time='00:00',last_auto_day='')
    w=Window(store,smoke=True);calls=[];w.search=lambda:calls.append('query')
    w.tick();w.tick();assert len(calls)==1
    store.update_settings(last_auto_day='',schedule=False);w.tick();assert len(calls)==1
    w.close();w.deleteLater();app.processEvents()
