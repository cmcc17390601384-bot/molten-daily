from __future__ import annotations
import csv, hashlib, html, json, os, shutil, sys, threading
from datetime import date, datetime, timedelta
from pathlib import Path
import fitz
from PySide6.QtCore import Qt,QTimer,QThread,Signal,QUrl,QTime
from PySide6.QtGui import QAction,QColor,QIcon,QImage,QPainter,QPixmap,QDesktopServices,QFont
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,
    QLabel,QPushButton,QListWidget,QStackedWidget,QTableWidget,QTableWidgetItem,QHeaderView,QSplitter,
    QTextBrowser,QTextEdit,QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QCheckBox,QTimeEdit,QFormLayout,
    QScrollArea,QFileDialog,QInputDialog,QMessageBox,QProgressBar,QSystemTrayIcon,QMenu,QFrame,QAbstractItemView)
from core import APP,VERSION,SEEDS,Store,protect,unprotect,safe_error,set_autostart,DEFAULTS
from topics import custom_topic,research_topic,current_assessment,query_lines
from services import Retriever,DeepSeek,eligibility,topic_score,download_pdf,Cancelled
from translation import Translator,extract_document,scientific_html
from margins import MarginAnnotator
from analysis_report import PaperAnalyzer,report_html
from library import import_pdf as import_local
from library_ui import LibraryUI

STYLE='''
QWidget {font-family: "Microsoft YaHei UI"; font-size: 10pt; color: #223c40;}
QMainWindow {background:#f3f7f6;} QWidget#sidebar {background:#073f3b;} QLabel#brand {color:#fff;font-size:21pt;font-weight:700;}
QLabel#subbrand {color:#a4c8c1;font-size:9pt;} QListWidget#nav {background:transparent;border:0;color:#c7ded9;outline:0;}
QListWidget#nav::item {padding:17px 14px;margin:3px 0;border-radius:7px;} QListWidget#nav::item:selected {background:#1c6058;color:white;}
QLabel#heading {font-size:24pt;font-weight:700;color:#073f3b;} QLabel#muted {color:#69817f;} QLabel#card {background:white;border:1px solid #dce6e2;border-radius:9px;padding:14px;font-size:12pt;}
QPushButton {background:white;border:1px solid #c9d9d4;border-radius:6px;padding:8px 13px;min-height:18px;}
QPushButton:hover {background:#e4f1ec;} QPushButton:disabled {color:#9cacaa;background:#edf2f0;}
QPushButton#primary {background:#08786a;color:white;border:0;font-weight:600;} QPushButton#primary:hover {background:#096557;}
QPushButton#primary:disabled {background:#edf2f0;color:#9cacaa;border:1px solid #c9d9d4;}
QTabBar::tab {padding:10px 22px;background:#e9f1ed;border:0;margin-right:3px;}
QTabBar::tab:selected {background:#ffffff;color:#08786a;font-weight:600;}
QTabWidget::pane {border:1px solid #dbe5e1;background:#ffffff;}
QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox,QTimeEdit,QTextEdit {background:white;border:1px solid #cbd9d5;border-radius:5px;padding:6px;}
QTableWidget {background:white;border:1px solid #dbe5e1;gridline-color:#e8eeeb;selection-background-color:#deeee7;selection-color:#15473e;}
QHeaderView::section {background:#e9f1ed;padding:9px;border:0;border-bottom:1px solid #d4e1db;font-weight:600;}
QTextBrowser {background:white;border:1px solid #dae5df;border-radius:7px;padding:12px;font-size:11pt;}
QProgressBar {border:0;background:#dce9e3;border-radius:3px;height:5px;} QProgressBar::chunk {background:#1f9a81;}
QCheckBox {spacing:8px;} QScrollArea {border:0;background:transparent;} QToolTip {background:#fff9df;color:#243b37;}
'''

def icon():
    p=QPixmap(64,64); p.fill(QColor('#08786a')); q=QPainter(p); q.setPen(QColor('white')); q.setFont(QFont('Segoe UI',27,QFont.Bold)); q.drawText(p.rect(),Qt.AlignCenter,'M'); q.end(); return QIcon(p)
def button(text,fn,primary=False):
    b=QPushButton(text); b.clicked.connect(fn)
    if primary: b.setObjectName('primary')
    return b
def label(text,name=''):
    l=QLabel(text); l.setWordWrap(True)
    if name:l.setObjectName(name)
    return l
def table(headers):
    t=QTableWidget(0,len(headers)); t.setHorizontalHeaderLabels(headers); t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection); t.setEditTriggers(QAbstractItemView.NoEditTriggers); t.verticalHeader().hide()
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents); return t


class Worker(QThread):
    progress=Signal(str); result=Signal(object); failed=Signal(str); budget=Signal(object)
    def __init__(self,fn,cancel,parent=None):super().__init__(parent);self.fn=fn;self.cancel=cancel
    def approve_budget(self,details,cancel):
        request=dict(details,event=threading.Event(),approved=False);self.budget.emit(request)
        while not request['event'].wait(.1):
            if cancel and cancel.is_set():return False
        return request['approved'] and not (cancel and cancel.is_set())
    def run(self):
        try:self.result.emit(self.fn(self.cancel,self.progress.emit))
        except Exception as e:self.failed.emit(safe_error(e))


class PdfArea(QScrollArea):
    zoomed=Signal(int)
    def wheelEvent(self,event):
        if event.modifiers() & Qt.ControlModifier:
            self.zoomed.emit(10 if event.angleDelta().y()>0 else -10);event.accept()
        else:super().wheelEvent(event)


class Window(LibraryUI,QMainWindow):
    def __init__(self,store=None,smoke=False):
        super().__init__();self.store=store or Store();self.smoke=smoke;self.worker=None;self.cancel_event=None
        self.source_doc=None;self.pdf_path='';self.margin_output='';self.selected=None;self.page_no=0;self.want_quit=False;self.fit_mode=True
        self.setWindowTitle('文献精读 · '+VERSION);self.setWindowIcon(icon());self.resize(1440,950);self.setMinimumSize(1050,720);self.setStyleSheet(STYLE)
        root=QWidget();self.setCentralWidget(root);layout=QVBoxLayout(root);layout.setContentsMargins(22,18,22,16)
        layout.addWidget(label('文献精读','heading'))
        from PySide6.QtWidgets import QTabWidget
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        self.status=label('就绪。导入 PDF 后点击“生成 / 继续”，每段完成即保存。','muted');layout.addWidget(self.status)
        row=QHBoxLayout();self.progress=QProgressBar();self.progress.setRange(0,0);self.progress.hide();row.addWidget(self.progress,1)
        self.cancel_btn=button('暂停并保留进度',self.cancel);self.cancel_btn.hide();row.addWidget(self.cancel_btn);layout.addLayout(row)
        self.make_search();self.make_library();self.make_reader();self.make_settings();self.tabs.currentChanged.connect(self.tab_changed);self.refresh();self.update_actions()
        last=self.store.settings().get('last_pdf','')
        if last and Path(last).exists():
            try:self.load_pdf(last)
            except Exception:pass
    def page(self,title):
        w=QWidget();v=QVBoxLayout(w);v.setContentsMargins(14,14,14,10);v.setSpacing(12);self.tabs.addTab(w,title);return v
    def make_search(self):
        s=self.store.settings();v=self.page('找文献')
        row=QHBoxLayout();self.topic_input=QLineEdit(research_topic(s));self.topic_input.setPlaceholderText('输入研究方向，如：钠离子电池正极材料的原位表征')
        row.addWidget(self.topic_input,1);self.search_btn=button('按此方向查询',self.search,True);row.addWidget(self.search_btn);v.addLayout(row)
        self.topic_input.textEdited.connect(lambda:self.queries.clear())
        filters=QWidget();grid=QGridLayout(filters);grid.setContentsMargins(0,0,0,0);grid.setHorizontalSpacing(12)
        self.days=QSpinBox();self.days.setRange(1,3650);self.days.setValue(s['days']);self.days.setSuffix(' 天')
        self.minif=QDoubleSpinBox();self.minif.setRange(0,100);self.minif.setValue(s['min_if'])
        self.year=QSpinBox();self.year.setRange(2020,date.today().year);self.year.setValue(s['metric_year'])
        self.maxpages=QSpinBox();self.maxpages.setRange(3,100);self.maxpages.setValue(s['max_pages']);self.maxpages.setSuffix(' 页')
        for i,(name,widget) in enumerate([('最近发表',self.days),('JIF 必须大于',self.minif),('JIF 年份',self.year),('短篇优先',self.maxpages)]):
            grid.addWidget(label(name,'muted'),0,i);grid.addWidget(widget,1,i)
        v.addWidget(filters)
        advanced=button('展开检索词与高级选项',lambda:None);advanced.setCheckable(True);v.addWidget(advanced)
        self.advanced_filters=QWidget();form=QFormLayout(self.advanced_filters);form.setContentsMargins(0,0,0,0)
        self.queries=QTextEdit();self.queries.setPlainText('\n'.join(s['queries']));self.queries.setMaximumHeight(85);form.addRow('英文检索词（每行一组）',self.queries)
        self.strict=QCheckBox('严格限制页数，未知页数不通过');self.strict.setChecked(s['strict_pages']);self.strict.toggled.connect(self.refresh);form.addRow('',self.strict)
        row=QHBoxLayout();row.addWidget(button('导入 JIF 表',self.import_metrics));row.addWidget(button('导出 JIF 表',self.export_metrics));row.addWidget(label('内置 3 本期刊的 2024 年 JIF；导入指标须有年份、来源与核对日期。','muted'),1);form.addRow('',row)
        self.advanced_filters.hide();v.addWidget(self.advanced_filters)
        advanced.toggled.connect(lambda checked:(self.advanced_filters.setVisible(checked),advanced.setText('收起检索词与高级选项' if checked else '展开检索词与高级选项')))
        for control in (self.days,self.minif,self.year,self.maxpages):control.valueChanged.connect(self.refresh)
        self.digest=label('JIF 是硬性门槛：仅显示所选年份已记录来源且严格高于阈值的论文。未核实指标一律排除。','muted');v.addWidget(self.digest)
        row=QHBoxLayout();self.filter=QLineEdit();self.filter.setPlaceholderText('筛选本地标题、DOI、期刊');self.filter.textChanged.connect(self.refresh)
        row.addWidget(self.filter);self.strict_view=QCheckBox('只看符合筛选条件');self.strict_view.toggled.connect(self.refresh);row.addWidget(self.strict_view);v.addLayout(row)
        self.lib_table=table(['论文','期刊','发表日期','JIF / 状态']);self.lib_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.lib_table.itemSelectionChanged.connect(self.select_paper);v.addWidget(self.lib_table,1)
        self.detail=QTextBrowser();self.detail.setOpenExternalLinks(True);self.detail.setMaximumHeight(135);v.addWidget(self.detail)
        row=QHBoxLayout()
        self.search_read=button('阅读 PDF',self.open_reader_selected,True);self.search_get=button('获取开放 PDF',self.get_pdf);self.search_attach=button('关联 PDF',self.attach_pdf);self.search_doi=button('出版社',self.open_doi);self.search_classify=button('加入分类',self.classify)
        for widget in (self.search_read,self.search_get,self.search_attach,self.search_doi,self.search_classify):row.addWidget(widget)
        row.addStretch();row.addWidget(button('打开图书馆',self.show_library));v.addLayout(row)
        self.lib_table.cellDoubleClicked.connect(lambda *_:self.open_reader_selected())
    def make_reader(self):
        v=self.page('PDF 留白精读');row=QHBoxLayout()
        self.import_btn=button('打开新 PDF',self.import_pdf);row.addWidget(self.import_btn)
        self.generate_btn=button('生成 / 继续留白精读',self.annotate_margins,True);row.addWidget(self.generate_btn)
        self.export_btn=button('导出留白 PDF',self.export_pdf);row.addWidget(self.export_btn);row.addWidget(button('返回图书馆',self.show_library));row.addStretch();self.pdf_title=label('尚未导入 PDF','muted');row.addWidget(self.pdf_title);v.addLayout(row)
        row=QHBoxLayout();self.view_kind=QComboBox();self.view_kind.addItems(['原版 PDF','留白精读 PDF']);self.view_kind.currentIndexChanged.connect(self.switch_pdf);row.addWidget(self.view_kind)
        row.addWidget(button('上一页',lambda:self.move_page(-1)));self.page_label=label('0 / 0');row.addWidget(self.page_label);row.addWidget(button('下一页',lambda:self.move_page(1)));row.addStretch()
        row.addWidget(button('−',lambda:self.change_zoom(-10)));self.zoom=QSpinBox();self.zoom.setRange(25,400);self.zoom.setSuffix(' %');self.zoom.setValue(100);self.zoom.valueChanged.connect(self.zoom_changed);row.addWidget(self.zoom)
        row.addWidget(button('+',lambda:self.change_zoom(10)));row.addWidget(button('适合宽度',self.fit_width));row.addWidget(button('100%',lambda:self.zoom.setValue(100)));v.addLayout(row)
        self.pdf_area=PdfArea();self.pdf_area.setAlignment(Qt.AlignCenter);self.pdf_area.setWidgetResizable(False);self.pdf_label=QLabel();self.pdf_label.setAlignment(Qt.AlignCenter);self.pdf_area.setWidget(self.pdf_label);self.pdf_area.zoomed.connect(self.change_zoom);v.addWidget(self.pdf_area,1)
        self.reader_status=label('左侧整段中文，右侧专业词 / 短语 / 长难句及音标。内容过多自动续页；Ctrl + 滚轮缩放。','muted');v.addWidget(self.reader_status)
    def make_settings(self):
        v=self.page('设置');scroll=QScrollArea();scroll.setWidgetResizable(True);content=QWidget();form=QFormLayout(content);form.setSpacing(14);s=self.store.settings()
        from providers import PROVIDERS,profiles
        self.api_profiles=profiles(s);self.profile_url=s['base_url'].rstrip('/');self.models_seen=set()
        self.provider=QComboBox()
        for name,url in PROVIDERS:self.provider.addItem(name,url)
        index=self.provider.findData(self.profile_url);self.provider.setCurrentIndex(index if index>=0 else self.provider.count()-1);form.addRow('API 供应商',self.provider)
        self.base=QLineEdit(s['base_url']);form.addRow('API 地址',self.base)
        self.secret=QLineEdit();self.secret.setEchoMode(QLineEdit.Password);self.secret.setPlaceholderText('已保存，留空保持不变' if s['api_secret'] else '输入 API Key');form.addRow('API Key',self.secret)
        self.model=QComboBox();self.model.setEditable(True);self.model.addItem(s['model']);form.addRow('模型',self.model)
        self.model.setPlaceholderText('检测模型后选择，或输入模型 ID')
        self.model_status=label('从供应商实时获取模型；新模型无需升级软件。','muted');form.addRow('',self.model_status)
        form.addRow('',button('检测 / 刷新可用模型',self.test_api))
        self.model_timer=QTimer(self);self.model_timer.setSingleShot(True);self.model_timer.timeout.connect(self.auto_models)
        self.provider.currentIndexChanged.connect(self.provider_changed)
        self.base.editingFinished.connect(self.endpoint_edited)
        self.secret.editingFinished.connect(lambda:self.model_timer.start(300) if self.secret.text().strip() else None)
        self.budget=QSpinBox();self.budget.setRange(10000,100000000);self.budget.setSingleStep(100000);self.budget.setValue(s['daily_token_limit']);form.addRow('每日 Token 基础额度',self.budget)
        self.usage=label('','muted');form.addRow('',self.usage)
        form.addRow('',label('默认每日 100 万 Token；不足时询问是否仅为今天追加 100 万。取消会暂停，已完成段落保留。此额度是软件预算，不会更改 API 账户余额。','muted'))
        scroll.setWidget(content);v.addWidget(scroll,1);v.addWidget(button('保存设置',self.save_settings,True))
    def provider_changed(self,*_):
        endpoint=self.provider.currentData()
        if endpoint:self.switch_provider(endpoint)
        else:
            self.switch_provider('https://your-provider.example/v1');self.base.setFocus();self.base.selectAll()
    def endpoint_edited(self):
        endpoint=self.base.text().strip().rstrip('/')
        if endpoint!=self.profile_url:self.switch_provider(endpoint)
    def switch_provider(self,endpoint):
        from providers import remember
        from services import public_url
        try:public_url(endpoint)
        except ValueError as e:self.job_error(str(e));return
        remember(self.api_profiles,self.profile_url,self.model.currentText(),self.secret.text().strip())
        self.profile_url=endpoint;profile=self.api_profiles.get(endpoint,{})
        self.provider.blockSignals(True);index=self.provider.findData(endpoint);self.provider.setCurrentIndex(index if index>=0 else self.provider.count()-1);self.provider.blockSignals(False)
        self.base.setText(endpoint);self.secret.clear();self.secret.setPlaceholderText('已保存该供应商密钥，留空保持不变' if profile.get('api_secret') else '输入此供应商的 API Key')
        self.model.clear();self.model.addItems(profile.get('models',[]));self.model.setCurrentText(profile.get('model',''))
        self.model_status.setText('正在自动检测可用模型…' if profile.get('api_secret') else '填写此供应商密钥后自动检测模型。支持 /models 与 chat/completions 兼容接口。')
        if profile.get('api_secret'):self.model_timer.start(300)
    def auto_models(self):
        if self.worker and self.worker.isRunning():return
        if self.secret.text().strip() or self.api_profiles.get(self.profile_url,{}).get('api_secret'):self.test_api()
    def save_settings(self,*_,allow_empty_model=False):
        if self.worker and self.worker.isRunning():return False
        try:
            from services import public_url
            topic=self.topic_input.text().strip()
            if not 1<=len(topic)<=1500:raise ValueError('请填写研究方向，最多 1500 字。')
            qs=[q.strip() for q in self.queries.toPlainText().splitlines() if q.strip()]
            if qs:qs=query_lines(qs)
            if self.base.text().strip().rstrip('/')!=self.profile_url:self.endpoint_edited()
            s=self.store.settings();s.update(base_url=public_url(self.base.text().strip().rstrip('/')),model=self.model.currentText().strip(),
                daily_token_limit=self.budget.value(),topic_mode='custom',research_topic=topic,queries=qs,schedule=False,
                days=self.days.value(),min_if=self.minif.value(),metric_year=self.year.value(),max_pages=self.maxpages.value(),strict_pages=self.strict.isChecked())
            if not s['model'] and not allow_empty_model:raise ValueError('请先检测并选择模型，或手动填写模型 ID。')
            from providers import remember
            profile=remember(self.api_profiles,self.profile_url,s['model'],self.secret.text().strip())
            s['api_secret']=profile.get('api_secret','');s['api_profiles']=self.api_profiles
            self.store.save_settings(s);self.secret.clear();self.secret.setPlaceholderText('已保存，留空保持不变' if s['api_secret'] else '输入 API Key')
            self.refresh();self.status.setText('设置已保存。');return True
        except Exception as e:self.job_error(safe_error(e));return False
    def run_job(self,name,fn,callback):
        if self.worker and self.worker.isRunning():self.status.setText('当前任务仍在运行，请等待或暂停。');return
        self.cancel_event=threading.Event();self.worker=Worker(fn,self.cancel_event,self);self.worker.budget.connect(self.confirm_budget)
        self.store.budget_handler=self.worker.approve_budget
        self.worker.progress.connect(self.status.setText);self.worker.result.connect(callback);self.worker.failed.connect(self.job_error);self.worker.finished.connect(self.job_finished)
        self.tabs.widget(3).setEnabled(False);self.tabs.widget(1).setEnabled(False);self.tabs.widget(0).setEnabled(False);self.topic_input.setEnabled(False);self.view_kind.setEnabled(False)
        for b in (self.search_btn,self.generate_btn,self.import_btn):b.setEnabled(False)
        self.progress.show();self.cancel_btn.show();self.status.setText(name);self.worker.start();self.update_actions()
    def confirm_budget(self,r):
        if self.cancel_event and self.cancel_event.is_set():r['event'].set();return
        prompt=f'今日已用 {r["used"]:,} Token，当前额度 {r["limit"]:,}。\n下一请求预计最多 {r["estimate"]:,} Token。\n\n是否仅为今天追加 {r["extra"]:,} Token 并继续？\n这会继续调用付费 API。选择“否”将暂停，已完成段落保留。'
        answer=QMessageBox.question(self,'额度不足：是否继续？',prompt,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        r['approved']=answer==QMessageBox.Yes;r['event'].set()
    def job_finished(self):
        self.store.budget_handler=None;self.tabs.widget(3).setEnabled(True);self.tabs.widget(1).setEnabled(True);self.tabs.widget(0).setEnabled(True);self.topic_input.setEnabled(True);self.view_kind.setEnabled(True)
        for b in (self.search_btn,self.generate_btn,self.import_btn):b.setEnabled(True)
        self.progress.hide();self.cancel_btn.hide();self.refresh()
        if self.want_quit:self.close()
    def job_error(self,text):
        self.status.setText(text);self.store.event('任务提示',text)
        if not self.smoke and '暂停' not in text and '停止' not in text:QMessageBox.warning(self,'任务提示',text)
    def cancel(self):
        if self.cancel_event:self.cancel_event.set();self.status.setText('正在暂停；当前请求完成后保存结果，不丢弃已付费内容。')
    def search(self):
        if self.save_settings():self.run_job('检索真实题录…',lambda c,p:Retriever(self.store,c,p).search(on_demand=True),self.search_done)
    def search_done(self,r):
        self.queries.setPlainText('\n'.join(self.store.settings()['queries']))
        self.digest.setText(f'检索 {r["fetched"]} 条记录；本次符合条件 {len(r["selected"])} 篇。'+(' 部分服务未完成：'+'；'.join(r['errors']) if r['errors'] else ''))
        self.status.setText('查询完成，结果已保存在本地。');self.refresh()
    def refresh(self,*_):
        if not hasattr(self,'usage'):return
        self.usage.setText(f'今日已用 {self.store.tokens():,} / {self.store.daily_budget():,} Token（含今天追加额度）')
        previous=self.selected;rows=[];s=self.store.settings();s.update(metric_year=self.year.value(),min_if=self.minif.value(),days=self.days.value(),max_pages=self.maxpages.value(),strict_pages=self.strict.isChecked());q=self.filter.text().casefold()
        for p in self.store.papers():
            ok,score,reasons,m=eligibility(p,self.store,s)
            if not m or float(m['value'])<=s['min_if']:continue
            if not (date.today()-timedelta(days=s['days'])).isoformat()<=p.get('date','')<=date.today().isoformat():continue
            if q not in (p['title']+' '+p['doi']+' '+p['journal']).casefold():continue
            if self.strict_view.isChecked() and not ok:continue
            rows.append((ok,score,p,reasons,m))
        rows.sort(key=lambda x:(x[0],x[1],x[2].get('date','')),reverse=True);self.lib_table.blockSignals(True);self.lib_table.setRowCount(len(rows))
        for i,(ok,score,p,reasons,m) in enumerate(rows):
            title=current_assessment(p,s).get('title_zh') or p['title']
            for j,text in enumerate([title,p['journal'],p.get('date',''),str(m['value'])+' ('+str(m['year'])+')'+(' / 符合' if ok else ' / 其他条件待核实')]):
                item=QTableWidgetItem(text);item.setData(Qt.UserRole,p['doi']);item.setToolTip('; '.join(reasons));self.lib_table.setItem(i,j,item)
            self.lib_table.setRowHeight(i,48)
        self.lib_table.clearSelection();self.lib_table.setCurrentCell(-1,-1)
        for i in range(self.lib_table.rowCount()):
            if self.lib_table.item(i,0).data(Qt.UserRole)==previous:self.lib_table.selectRow(i);break
        self.lib_table.blockSignals(False)
        if self.tabs.currentIndex()==0:self.select_paper()
        if hasattr(self,'category'):self.refresh_categories()
        self.update_actions()
    def select_paper(self):
        row=self.lib_table.currentRow()
        if row<0 or not self.lib_table.item(row,0):
            self.selected=None;self.detail.setPlainText('选择一篇论文查看摘要和 JIF 来源。' if self.lib_table.rowCount() else '暂无符合当前 JIF 与日期要求的记录。可扩大时间范围或导入有来源的 JIF 表；本地文献请在图书馆管理。');self.update_actions();return
        self.selected=self.lib_table.item(row,0).data(Qt.UserRole);p=self.store.paper(self.selected);e=html.escape
        a=current_assessment(p,self.store.settings());_,_,reasons,m=eligibility(p,self.store,dict(self.store.settings(),metric_year=self.year.value(),min_if=self.minif.value()))
        metric_info=('<p>JIF '+e(str(m['value']))+' · '+e(str(m['year']))+' 年 · 核对日期 '+e(m['checked'])+' · <a href="'+e(m['source'],quote=True)+'">指标来源</a></p>') if m else ''
        self.detail.setHtml(metric_info+'<h3>'+e(p['title'])+'</h3><p>'+e('；'.join(reasons) or '符合当前筛选')+'</p><p>'+e(a.get('reason',''))+'</p><p>'+e(p.get('abstract','') or '来源未提供摘要')+'</p>')
        self.update_actions()
    def open_doi(self):
        p=self.store.paper(self.selected) if self.selected else None
        if p and p.get('url','').startswith('https://'):QDesktopServices.openUrl(QUrl(p['url']))
    def get_pdf(self):
        p=self.store.paper(self.selected) if self.selected else None
        if not p:self.status.setText('请先选择一篇论文。');return
        doi=p['doi']
        def job(c,pr):
            from library import link_pdf
            item=p
            if not item.get('pdf_url'):item=Retriever(self.store,c,pr).by_doi(doi)
            if not item.get('pdf_url'):raise ValueError('暂无开放 PDF，可从出版社下载后点击“关联本地 PDF”。')
            dest=self.store.root/'pdfs'/(hashlib.sha256(doi.encode()).hexdigest()[:20]+'.pdf')
            path=download_pdf(item['pdf_url'],dest,c);return link_pdf(self.store,doi,path)
        def done(path):self.load_pdf(path);self.tabs.setCurrentIndex(2);self.refresh()
        self.run_job('查询并获取开放 PDF…',job,done)
    def open_reader_selected(self):
        p=self.store.paper(self.selected) if self.selected else None
        if p and p.get('pdf') and Path(p['pdf']).is_file():
            try:self.load_pdf(p['pdf'],p['title']);self.store.set_flag(p['doi'],'seen',1);self.tabs.setCurrentIndex(2);self.refresh_library()
            except Exception as e:self.job_error(safe_error(e))
        else:self.status.setText('请先获取开放 PDF 或在精读页导入本地 PDF。')
    def import_pdf(self,*_):
        path,_=QFileDialog.getOpenFileName(self,'导入论文 PDF','','PDF (*.pdf)')
        if not path:return
        try:p=import_local(self.store,path);self.selected=p['doi'];self.load_pdf(p['pdf'],p['title']);self.tabs.setCurrentIndex(2);self.refresh()
        except Exception as e:self.job_error(safe_error(e))
    def load_pdf(self,path,title=''):
        from margins import cache_folder
        doc=extract_document(path);self.pdf_path=str(Path(path).resolve());self.pdf_title.setText(title or Path(path).name);self.store.update_settings(last_pdf=self.pdf_path)
        folder=cache_folder(self.store,doc);output=folder/'页边精读批注_原版左右留白.pdf';self.margin_output=str(output) if output.exists() else ''
        self.view_kind.blockSignals(True);self.view_kind.setCurrentIndex(1 if self.margin_output else 0);self.view_kind.blockSignals(False);self.switch_pdf();self.update_actions()
        cache=folder/'margins.json'
        if cache.exists():
            st=json.loads(cache.read_text('utf-8'));self.status.setText(f'已缓存 {len(st.get("results",{}))} 段；点击“生成 / 继续”从未完成处处理。')
    def switch_pdf(self,*_):
        path=self.margin_output if self.view_kind.currentIndex()==1 else self.pdf_path
        if not path:
            self.view_kind.blockSignals(True);self.view_kind.setCurrentIndex(0);self.view_kind.blockSignals(False);self.status.setText('尚未生成留白 PDF，请点击“生成 / 继续”。');return
        if self.source_doc:self.source_doc.close()
        self.source_doc=fitz.open(path);self.page_no=0;self.fit_width()
    def fit_width(self):
        self.fit_mode=True
        if self.source_doc:
            value=round((self.pdf_area.viewport().width()-24)/self.source_doc[self.page_no].rect.width/1.3333*100)
            self.zoom.blockSignals(True);self.zoom.setValue(max(25,min(400,value)));self.zoom.blockSignals(False);self.render_page()
    def change_zoom(self,delta):self.zoom.setValue(self.zoom.value()+delta)
    def zoom_changed(self,*_):self.fit_mode=False;self.render_page()
    def move_page(self,n):
        if self.source_doc:self.page_no=max(0,min(len(self.source_doc)-1,self.page_no+n));self.render_page()
    def render_page(self,*_):
        if not self.source_doc:return
        p=self.source_doc[self.page_no];scale=self.zoom.value()/100*1.3333
        scale=min(scale,(18000000/(p.rect.width*p.rect.height))**.5)
        pix=p.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False);im=QImage(pix.samples,pix.width,pix.height,pix.stride,QImage.Format_RGB888).copy()
        pm=QPixmap.fromImage(im);self.pdf_label.setPixmap(pm);self.pdf_label.resize(pm.size());self.page_label.setText(f'{self.page_no+1} / {len(self.source_doc)}')
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'zoom') and self.fit_mode:QTimer.singleShot(0,self.fit_width)
    def annotate_margins(self):
        if not self.pdf_path:self.status.setText('请先导入 PDF。');return
        if not self.save_settings():return
        path=self.pdf_path
        self.view_kind.setCurrentIndex(0)  # Release the previous output PDF before replacing it.
        from pdf_process import run_margin_process
        self.run_job('恢复缓存并生成留白精读…',lambda c,p:run_margin_process(self.store,path,c,p),self.margin_done)
    def margin_done(self,r):
        self.margin_output=r['pdf'];self.view_kind.blockSignals(True);self.view_kind.setCurrentIndex(1);self.view_kind.blockSignals(False);self.switch_pdf();self.update_actions();a=r['audit']
        self.status.setText(f'完成：{a["categories"]["paragraph"]} 段译文、{a["items"]-a["categories"]["paragraph"]} 条批注。已在软件内打开，可缩放或导出。')
        if a['warnings']:self.reader_status.setText('；'.join(a['warnings']))
    def export_pdf(self):
        if not self.margin_output:self.status.setText('请先生成留白精读 PDF。');return
        target,_=QFileDialog.getSaveFileName(self,'导出留白精读 PDF',Path(self.pdf_path).stem+'_留白精读.pdf','PDF (*.pdf)')
        if target and Path(target).resolve()!=Path(self.margin_output).resolve():shutil.copy2(self.margin_output,target);self.status.setText('PDF 已导出。')
    def test_api(self):
        self.model_timer.stop()
        if self.worker and self.worker.isRunning():return
        try:
            from providers import remember
            from services import public_url
            if self.base.text().strip().rstrip('/')!=self.profile_url:self.endpoint_edited()
            endpoint=public_url(self.base.text().strip().rstrip('/'))
            profile=remember(self.api_profiles,endpoint,self.model.currentText().strip(),self.secret.text().strip())
            self.store.update_settings(base_url=endpoint,model=profile['model'],api_secret=profile.get('api_secret',''),api_profiles=self.api_profiles)
            self.secret.clear();self.secret.setPlaceholderText('已保存该供应商密钥，留空保持不变' if profile.get('api_secret') else '输入此供应商的 API Key')
        except Exception as e:self.job_error(safe_error(e));return
        def done(models):
            current=self.model.currentText();self.model.clear();self.model.addItems(models)
            self.model.setCurrentText(current if current in models else models[0])
            self.api_profiles.setdefault(self.profile_url,{}).update(model=self.model.currentText(),models=models)
            self.store.update_settings(model=self.model.currentText(),api_profiles=self.api_profiles);self.models_seen.add(self.profile_url)
            self.model_status.setText(f'已实时检测到 {len(models)} 个模型；可选择或输入模型 ID。列表可见不等于所有模型都支持文本翻译。');self.status.setText('模型列表已更新；未发送翻译请求。')
        self.run_job('正在向当前供应商获取模型列表…',lambda c,p:DeepSeek(self.store,cancel=c).models(),done)
    def import_metrics(self):
        file,_=QFileDialog.getOpenFileName(self,'导入 JIF 表','','CSV (*.csv)')
        if not file:return
        try:
            with open(file,encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
            merged={(r['journal'].casefold(),str(r['year'])):r for r in self.store.metrics()}
            for r in rows:merged[(r['journal'].casefold(),str(r['year']))]=r
            self.store.write_metrics(list(merged.values()));self.refresh();self.status.setText('指标表已合并。')
        except Exception as e:self.job_error(safe_error(e))
    def export_metrics(self):
        file,_=QFileDialog.getSaveFileName(self,'导出 JIF 表','journal_metrics.csv','CSV (*.csv)')
        if file:shutil.copy2(self.store.root/'journal_metrics.csv',file)
    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():self.want_quit=True;self.cancel();event.ignore();return
        self.store.budget_handler=None
        if self.source_doc:self.source_doc.close();self.source_doc=None
        event.accept()
        if not self.smoke:QApplication.quit()
