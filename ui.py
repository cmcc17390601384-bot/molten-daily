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
from topics import custom_topic,research_topic,current_assessment,query_lines,search_key
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

class MetricYear(QComboBox):
    valueChanged=Signal(int)
    def __init__(self,store,value):
        super().__init__();self.reload(store,value);self.currentIndexChanged.connect(lambda *_:self.valueChanged.emit(self.value()))
    def value(self):return int(self.currentData() or 0)
    def setValue(self,value):self.setCurrentIndex(max(0,self.findData(int(value))))
    def reload(self,store,value=None):
        value=self.value() if value is None else value;self.blockSignals(True);self.clear();self.addItem('最新已核实（推荐）',0)
        rows=store.metrics()
        for year in sorted({int(r['year']) for r in rows if int(r['year'])<date.today().year},reverse=True):
            count=len({r['journal'].casefold() for r in rows if int(r['year'])==year})
            self.addItem(f'{year} 年指标（{count} 本期刊）',year)
        self.setValue(value);self.blockSignals(False)


class Window(LibraryUI,QMainWindow):
    def __init__(self,store=None,smoke=False):
        super().__init__();self.store=store or Store();self.smoke=smoke;self.worker=None;self.cancel_event=None
        self.source_doc=None;self.pdf_path='';self.margin_output='';self.selected=None;self.page_no=0;self.want_quit=False;self.fit_mode=True
        self.study_state={};self.study_entries=[];self.render_key=None
        self.resize_timer=QTimer(self);self.resize_timer.setSingleShot(True);self.resize_timer.timeout.connect(self.fit_width)
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
        self.topic_input.textEdited.connect(lambda:self.queries.clear());self.topic_input.textChanged.connect(self.refresh)
        filters=QWidget();grid=QGridLayout(filters);grid.setContentsMargins(0,0,0,0);grid.setHorizontalSpacing(12)
        self.days=QSpinBox();self.days.setRange(1,3650);self.days.setValue(s['days']);self.days.setSuffix(' 天')
        self.minif=QDoubleSpinBox();self.minif.setRange(0,100);self.minif.setValue(s['min_if'])
        self.year=MetricYear(self.store,s['metric_year'])
        self.maxpages=QSpinBox();self.maxpages.setRange(3,100);self.maxpages.setValue(s['max_pages']);self.maxpages.setSuffix(' 页')
        for i,(name,widget) in enumerate([('最近发表',self.days),('JIF 必须大于',self.minif),('JIF 年份',self.year),('短篇优先',self.maxpages)]):
            grid.addWidget(label(name,'muted'),0,i);grid.addWidget(widget,1,i)
        v.addWidget(filters)
        advanced=button('展开检索词与高级选项',lambda:None);advanced.setCheckable(True);v.addWidget(advanced)
        self.advanced_filters=QWidget();form=QFormLayout(self.advanced_filters);form.setContentsMargins(0,0,0,0)
        self.queries=QTextEdit();self.queries.setPlainText('\n'.join(s['queries']));self.queries.setMaximumHeight(85);form.addRow('英文检索词（每行一组）',self.queries)
        self.queries.textChanged.connect(self.refresh)
        self.search_depth=QSpinBox();self.search_depth.setRange(1,10);self.search_depth.setValue(s.get('search_pages',3));self.search_depth.setSuffix(' 页 × 100 条 / 每来源每组词');self.search_depth.valueChanged.connect(self.refresh);form.addRow('检索深度',self.search_depth)
        self.strict=QCheckBox('严格限制页数，未知页数不通过');self.strict.setChecked(s['strict_pages']);self.strict.toggled.connect(self.refresh);form.addRow('',self.strict)
        row=QHBoxLayout();row.addWidget(button('导入 JIF 表',self.import_metrics));row.addWidget(button('导出 JIF 表',self.export_metrics));row.addWidget(button('联网更新 JIF',self.update_metrics));self.metric_coverage=label('','muted');row.addWidget(self.metric_coverage,1);form.addRow('',row)
        self.advanced_filters.hide();v.addWidget(self.advanced_filters)
        advanced.toggled.connect(lambda checked:(self.advanced_filters.setVisible(checked),advanced.setText('收起检索词与高级选项' if checked else '展开检索词与高级选项')))
        for control in (self.days,self.minif,self.year,self.maxpages):control.valueChanged.connect(self.refresh)
        self.digest=label('只展示本次检索且通过主题与 JIF 门槛的文献；更换条件后请重新查询。','muted');v.addWidget(self.digest)
        row=QHBoxLayout();self.filter=QLineEdit();self.filter.setPlaceholderText('在本次结果中筛选标题、DOI、期刊');self.filter.textChanged.connect(self.refresh)
        row.addWidget(self.filter);required=label('主题与 JIF 均为必需条件','muted');required.setWordWrap(False);row.addWidget(required);v.addLayout(row)
        self.lib_table=table(['论文','期刊','发表日期','JIF / 状态']);self.lib_table.setSelectionMode(QAbstractItemView.ExtendedSelection);self.lib_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.lib_table.itemSelectionChanged.connect(self.select_paper);v.addWidget(self.lib_table,1)
        self.detail=QTextBrowser();self.detail.setOpenExternalLinks(True);self.detail.setMaximumHeight(135);v.addWidget(self.detail)
        row=QHBoxLayout()
        self.search_add=button('加入图书馆',self.add_search_selection,True);row.addWidget(self.search_add)
        self.search_read=button('阅读 PDF',self.open_reader_selected,True);self.search_get=button('获取开放 PDF',self.get_pdf);self.search_attach=button('关联 PDF',self.attach_pdf);self.search_doi=button('出版社',self.open_doi);self.search_classify=button('加入分类',self.classify)
        for widget in (self.search_read,self.search_get,self.search_attach,self.search_doi,self.search_classify):row.addWidget(widget)
        row.addStretch();row.addWidget(button('打开图书馆',self.show_library));v.addLayout(row)
        self.lib_table.cellDoubleClicked.connect(lambda *_:self.open_reader_selected())
    def make_reader(self):
        v=self.page('交互精读');row=QHBoxLayout()
        self.import_btn=button('打开新 PDF',self.import_pdf);row.addWidget(self.import_btn)
        self.generate_btn=button('生成 / 继续精读',self.annotate_margins,True);row.addWidget(self.generate_btn)
        self.package_btn=button('精读包',lambda:None);menu=QMenu(self.package_btn)
        menu.addAction('导入精读包…').triggered.connect(self.import_study_package)
        self.package_export_action=menu.addAction('导出精读包，分享给他人…');self.package_export_action.triggered.connect(self.export_study_package)
        self.package_btn.setMenu(menu);row.addWidget(self.package_btn)
        self.export_btn=button('导出留白 PDF',self.export_pdf);self.export_btn.hide()
        row.addWidget(button('返回图书馆',self.show_library));row.addStretch();self.pdf_title=label('尚未导入 PDF','muted');row.addWidget(self.pdf_title);v.addLayout(row)
        row=QHBoxLayout();self.view_kind=QComboBox();self.view_kind.addItem('原版 PDF');self.view_kind.hide()
        row.addWidget(button('上一页',lambda:self.move_page(-1)));self.page_label=label('0 / 0');row.addWidget(self.page_label);row.addWidget(button('下一页',lambda:self.move_page(1)));row.addStretch()
        row.addWidget(button('−',lambda:self.change_zoom(-10)));self.zoom=QSpinBox();self.zoom.setRange(25,400);self.zoom.setSuffix(' %');self.zoom.setValue(100);self.zoom.valueChanged.connect(self.zoom_changed);row.addWidget(self.zoom)
        row.addWidget(button('+',lambda:self.change_zoom(10)));row.addWidget(button('适合宽度',self.fit_width));row.addWidget(button('100%',lambda:self.zoom.setValue(100)));v.addLayout(row)
        from study_reader import StudyPage
        self.reader_splitter=QSplitter(Qt.Horizontal)
        def side(title):
            box=QWidget();layout=QVBoxLayout(box);layout.setContentsMargins(0,0,0,0);layout.addWidget(label(title,'muted'));browser=QTextBrowser();browser.setMinimumWidth(170);layout.addWidget(browser,1);return box,browser
        left,self.note_panel=side('词句释义 · 音标');right,self.translation_panel=side('所属段落 · 完整中文翻译')
        self.pdf_area=PdfArea();self.pdf_area.setAlignment(Qt.AlignCenter);self.pdf_area.setWidgetResizable(False);self.pdf_area.setMinimumWidth(300)
        self.pdf_label=StudyPage();self.pdf_label.setAlignment(Qt.AlignCenter);self.pdf_label.hovered.connect(self.show_study_note);self.pdf_area.setWidget(self.pdf_label);self.pdf_area.zoomed.connect(self.change_zoom)
        for widget in (left,self.pdf_area,right):self.reader_splitter.addWidget(widget)
        self.reader_splitter.setChildrenCollapsible(False);self.reader_splitter.setSizes([260,770,310]);self.reader_splitter.splitterMoved.connect(lambda *_:self.resize_timer.start(120) if self.fit_mode else None);v.addWidget(self.reader_splitter,1)
        self.reader_status=label('鼠标移到高亮处：左侧词句释义，右侧整段翻译。黄色：词；蓝色：短语；绿色：长难句。拖动分隔线调整宽度；Ctrl + 滚轮缩放。','muted');v.addWidget(self.reader_status)
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
            s=self.store.settings();mode='molten' if not custom_topic(s) and topic==research_topic(s) else 'custom'
            s.update(base_url=public_url(self.base.text().strip().rstrip('/')),model=self.model.currentText().strip(),
                daily_token_limit=self.budget.value(),topic_mode=mode,research_topic=topic,queries=qs,schedule=False,search_pages=self.search_depth.value(),
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
        if self.source_doc and getattr(self,'study_cache',None):self.reload_study();self.render_page()
        if self.want_quit:self.close()
    def job_error(self,text):
        self.status.setText(text);self.store.event('任务提示',text)
        if not self.smoke and '暂停' not in text and '停止' not in text:QMessageBox.warning(self,'任务提示',text)
    def cancel(self):
        if self.cancel_event:self.cancel_event.set();self.status.setText('正在暂停；当前请求完成后保存结果，不丢弃已付费内容。')
    def search(self):
        if self.save_settings():
            self.store.save_search_results(self.store.settings(),[],state='running');self.refresh()
            self.run_job('检索真实题录…',lambda c,p:Retriever(self.store,c,p).search(on_demand=True),self.search_done)
    def search_done(self,r):
        self.queries.setPlainText('\n'.join(self.store.settings()['queries']))
        self.status.setText('查询完成，结果独立保存。选中需要的文献后点击“加入图书馆”，支持 Ctrl / Shift 多选。');self.refresh()
    def search_settings(self):
        s=self.store.settings();topic=self.topic_input.text().strip();mode='molten' if not custom_topic(s) and topic==research_topic(s) else 'custom'
        s.update(topic_mode=mode,research_topic=topic,queries=[q.strip() for q in self.queries.toPlainText().splitlines() if q.strip()],
            metric_year=self.year.value(),min_if=self.minif.value(),days=self.days.value(),max_pages=self.maxpages.value(),strict_pages=self.strict.isChecked(),search_pages=self.search_depth.value())
        return s
    def refresh(self,*_):
        if not hasattr(self,'usage'):return
        self.usage.setText(f'今日已用 {self.store.tokens():,} / {self.store.daily_budget():,} Token（含今天追加额度）')
        previous=self.selected;rows=[];s=self.search_settings();q=self.filter.text().casefold();run=self.store.search_results()
        matched=run.get('key')==search_key(s);stats=run.get('stats',{}) if matched else {}
        for doi in run.get('dois',[]) if matched else []:
            p=self.store.found_paper(doi)
            if not p:continue
            ok,score,reasons,m=eligibility(p,self.store,s)
            if not ok:continue
            if not m or float(m['value'])<=s['min_if']:continue
            if not (date.today()-timedelta(days=s['days'])).isoformat()<=p.get('date','')<=date.today().isoformat():continue
            if q not in (p['title']+' '+p['doi']+' '+p['journal']).casefold():continue
            rows.append((ok,score,p,reasons,m))
        metrics=self.store.metrics();years=sorted({str(m['year']) for m in metrics},reverse=True)
        chosen=[m for m in metrics if not s['metric_year'] or int(m['year'])==s['metric_year']]
        self.metric_coverage.setText(f'本模式覆盖 {len({m["journal"].casefold() for m in chosen})} 本期刊 · 指标年份 {" / ".join(years)}；JIF 年份不是发表年份。')
        if stats.get('state')=='complete':
            self.digest.setText(f'本次题录 {stats.get("fetched",0)} 条 · 符合条件 {len(rows)} 篇 · JIF 未核实 {stats.get("unknown_jif",0)} / 未达标 {stats.get("below_jif",0)} · 主题剔除 {stats.get("topic_rejected",0)} / 待核验 {stats.get("unassessed",0)}'+('。已到检索深度上限，可在高级选项增加。' if stats.get('truncated') else '')+('。部分服务未完成：'+'；'.join(stats['errors']) if stats.get('errors') else ''))
            self.digest.setToolTip('JIF 尚未覆盖的期刊：\n'+'\n'.join(stats.get('missing_journals',[])[:50]))
        elif stats.get('state')=='failed':
            self.digest.setText('本次查询未完成，旧结果已清空：'+'；'.join(stats.get('errors',[])));self.digest.setToolTip('')
        else:
            self.digest.setText('正在查询当前条件，旧结果已清空。' if stats.get('state')=='running' else '条件尚未查询或已改变，请点击“按此方向查询”；既有文献保存在图书馆。');self.digest.setToolTip('')
        rows.sort(key=lambda x:(x[0],x[1],x[2].get('date','')),reverse=True);self.lib_table.blockSignals(True);self.lib_table.setRowCount(len(rows))
        for i,(ok,score,p,reasons,m) in enumerate(rows):
            title=current_assessment(p,s).get('title_zh') or p['title']
            for j,text in enumerate([title,p['journal'],p.get('date',''),str(m['value'])+' ('+str(m['year'])+')']):
                if j==3:text+=' · 已入库' if self.store.paper(p['doi']) else ' · 未入库'
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
        ids=self.search_ids()
        if len(ids)>1:
            self.selected=None;self.detail.setPlainText(f'已选中 {len(ids)} 篇文献。点击“加入图书馆”批量保存，已入库的文献会自动跳过。');self.update_actions();return
        row=self.lib_table.currentRow()
        if row<0 or not self.lib_table.item(row,0):
            self.selected=None;self.detail.setPlainText('选择一篇论文查看主题证据与 JIF 来源。' if self.lib_table.rowCount() else '当前没有已通过全部条件的结果；请查询当前条件，或根据上方统计调整时间范围、检索词、检索深度和指标表。');self.update_actions();return
        self.selected=self.lib_table.item(row,0).data(Qt.UserRole);p=self.store.found_paper(self.selected);e=html.escape
        a=current_assessment(p,self.search_settings());_,_,reasons,m=eligibility(p,self.store,self.search_settings())
        metric_info=('<p>JIF '+e(str(m['value']))+' · '+e(str(m['year']))+' 年 · 核对日期 '+e(m['checked'])+' · <a href="'+e(m['source'],quote=True)+'">指标来源</a></p>') if m else ''
        evidence='<p>主题证据：'+e('；'.join(a.get('evidence',[])))+'</p>' if a.get('evidence') else '<p>主题依据：题目 / 摘要满足检索词规则。</p>'
        self.detail.setHtml(metric_info+'<h3>'+e(p['title'])+'</h3>'+evidence+'<p>'+e(a.get('reason',''))+'</p><p>'+e(p.get('abstract','') or '来源未提供摘要')+'</p>')
        self.update_actions()
    def open_doi(self):
        p=self.store.found_paper(self.selected) if self.selected else None
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
        with open(path,'rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        self.pdf_path=str(Path(path).resolve());self.pdf_title.setText(title or Path(path).name);self.store.update_settings(last_pdf=self.pdf_path)
        self.study_cache=cache_folder(self.store,{'hash':digest})/'margins.json';self.study_hash=digest;self.reload_study();self.switch_pdf();self.update_actions()
        self.status.setText(f'已缓存 {len(self.study_state.get("results",{}))} 段；悬停高亮阅读，点击“生成 / 继续精读”补齐未完成段落。')
    def export_study_package(self):
        if not self.pdf_path:return
        target,_=QFileDialog.getSaveFileName(self,'分享精读包（不含原文 PDF 和 API 密钥）',Path(self.pdf_path).stem+'.mdstudy','精读包 (*.mdstudy)')
        if not target:return
        try:
            from study_package import export_package
            result=export_package(self.store,self.pdf_path,target)
            self.status.setText(f'已导出 {result["paragraphs"]} 段精读。对方导入精读包并打开同一份 PDF，即可免翻译阅读。')
        except Exception as e:self.job_error(safe_error(e))
    def import_study_package(self):
        path,_=QFileDialog.getOpenFileName(self,'导入精读包','','精读包 (*.mdstudy)')
        if not path:return
        try:
            from study_package import import_package,pdf_hash
            result=import_package(self.store,path);matched=''
            candidates=[self.pdf_path]+[p.get('pdf','') for p in self.store.papers() if p.get('pdf_sha256')==result['hash']]
            for candidate in dict.fromkeys(candidates):
                if candidate and Path(candidate).is_file() and pdf_hash(candidate)==result['hash']:matched=candidate;break
            if matched:self.load_pdf(matched);self.tabs.setCurrentIndex(2)
            self.status.setText(f'精读包已导入：新增 {result["added"]} 段，保留本地 {result["kept"]} 段。'+('已匹配原文，直接悬停阅读，无需再次翻译。' if matched else '打开对应的同一份 PDF 后自动套用；文件名可以不同。'))
            self.update_actions()
        except Exception as e:self.job_error(safe_error(e))
    def reload_study(self):
        self.study_state={}
        if getattr(self,'study_cache',None) and self.study_cache.exists():
            try:
                state=json.loads(self.study_cache.read_text('utf-8'))
                if state.get('document',{}).get('hash')==self.study_hash:self.study_state=state
            except (OSError,ValueError):self.status.setText('精读缓存读取失败；原文仍可阅读。')
        self.render_key=None
    def show_study_note(self,hits):
        from study_reader import panels
        left,right=panels(self.study_entries,hits);self.note_panel.setHtml(left);self.translation_panel.setHtml(right)
    def switch_pdf(self,*_):
        path=self.pdf_path
        if not path:return
        if self.source_doc:self.source_doc.close()
        self.source_doc=fitz.open(path);self.render_key=None;self.page_no=0;self.fit_width()
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
        p=self.source_doc[self.page_no];display_scale=self.zoom.value()/100*1.3333
        scale=min(display_scale,(8000000/(p.rect.width*p.rect.height))**.5)
        key=(self.pdf_path,self.page_no,round(display_scale,6))
        if key==self.render_key:return
        self.render_key=key
        pix=p.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False);im=QImage(pix.samples,pix.width,pix.height,pix.stride,QImage.Format_RGB888).copy()
        pm=QPixmap.fromImage(im);self.pdf_label.setPixmap(pm);self.pdf_label.resize(round(p.rect.width*display_scale),round(p.rect.height*display_scale));self.page_label.setText(f'{self.page_no+1} / {len(self.source_doc)}')
        from study_reader import page_notes
        self.study_entries=page_notes(self.study_state,self.page_no);self.pdf_label.set_notes(self.study_entries,p,display_scale)
        first=next((i for i,n in enumerate(self.study_entries) if n['kind']=='paragraph'),None)
        self.show_study_note((first,) if first is not None else ())
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'zoom') and self.fit_mode:self.resize_timer.start(120)
    def annotate_margins(self):
        if not self.pdf_path:self.status.setText('请先导入 PDF。');return
        if not self.save_settings():return
        path=self.pdf_path
        self.view_kind.setCurrentIndex(0)  # Release the previous output PDF before replacing it.
        from pdf_process import run_margin_process
        self.run_job('恢复缓存并生成交互精读…',lambda c,p:run_margin_process(self.store,path,c,p,interactive=True),self.margin_done)
    def margin_done(self,r):
        self.reload_study();self.render_page();self.update_actions()
        self.status.setText(f'精读已就绪：{len(self.study_state.get("results",{}))} 段已缓存，保留原文页数。移到高亮处查看释义和译文。')
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
            self.store.write_metrics(list(merged.values()));self.year.reload(self.store);self.refresh();self.status.setText('指标表已合并。')
        except Exception as e:self.job_error(safe_error(e))
    def export_metrics(self):
        file,_=QFileDialog.getSaveFileName(self,'导出 JIF 表','journal_metrics.csv','CSV (*.csv)')
        if file:shutil.copy2(self.store.root/'journal_metrics.csv',file)
    def update_metrics(self):
        from journal_metrics import update_publisher_metrics
        def done(result):
            self.year.reload(self.store);self.refresh();self.status.setText(f'已核对出版社 {result["year"]} 年 {result["rows"]} 本期刊指标，新增 {result["added"]} 条；已有同年指标保留。')
        self.run_job('联网核对 JIF…',lambda c,p:update_publisher_metrics(self.store,c,p),done)
    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():self.want_quit=True;self.cancel();event.ignore();return
        self.store.budget_handler=None
        if self.source_doc:self.source_doc.close();self.source_doc=None
        event.accept()
        if not self.smoke:QApplication.quit()
