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
from analysis_report import PaperAnalyzer,import_local,report_html

STYLE='''
QWidget {font-family: "Microsoft YaHei UI"; font-size: 10pt; color: #223c40;}
QMainWindow {background:#f3f7f6;} QWidget#sidebar {background:#073f3b;} QLabel#brand {color:#fff;font-size:21pt;font-weight:700;}
QLabel#subbrand {color:#a4c8c1;font-size:9pt;} QListWidget#nav {background:transparent;border:0;color:#c7ded9;outline:0;}
QListWidget#nav::item {padding:17px 14px;margin:3px 0;border-radius:7px;} QListWidget#nav::item:selected {background:#1c6058;color:white;}
QLabel#heading {font-size:24pt;font-weight:700;color:#073f3b;} QLabel#muted {color:#69817f;} QLabel#card {background:white;border:1px solid #dce6e2;border-radius:9px;padding:14px;font-size:12pt;}
QPushButton {background:white;border:1px solid #c9d9d4;border-radius:6px;padding:8px 13px;min-height:18px;}
QPushButton:hover {background:#e4f1ec;} QPushButton:disabled {color:#9cacaa;background:#edf2f0;}
QPushButton#primary {background:#08786a;color:white;border:0;font-weight:600;} QPushButton#primary:hover {background:#096557;}
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


class Window(QMainWindow):
    def __init__(self,store=None,smoke=False):
        super().__init__();self.store=store or Store();self.smoke=smoke;self.worker=None;self.cancel_event=None
        self.source_doc=None;self.pdf_path='';self.margin_output='';self.selected=None;self.page_no=0;self.want_quit=False;self.fit_mode=True
        self.setWindowTitle('文献精读 · '+VERSION);self.setWindowIcon(icon());self.resize(1440,950);self.setMinimumSize(1050,720);self.setStyleSheet(STYLE)
        root=QWidget();self.setCentralWidget(root);layout=QVBoxLayout(root);layout.setContentsMargins(22,18,22,16)
        layout.addWidget(label('文献精读','heading'));layout.addWidget(label('按需找论文 · 原版左右留白 · 随时暂停与继续','muted'))
        from PySide6.QtWidgets import QTabWidget
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        self.status=label('就绪。导入 PDF 后点击“生成 / 继续”，每段完成即保存。','muted');layout.addWidget(self.status)
        row=QHBoxLayout();self.progress=QProgressBar();self.progress.setRange(0,0);self.progress.hide();row.addWidget(self.progress,1)
        self.cancel_btn=button('暂停并保留进度',self.cancel);self.cancel_btn.hide();row.addWidget(self.cancel_btn);layout.addLayout(row)
        self.make_search();self.make_reader();self.make_settings();self.refresh()
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
        self.digest=label('按需查询；保留已有资料。JIF 未核实的论文标为候选，不冒充达标。','muted');v.addWidget(self.digest)
        row=QHBoxLayout();self.filter=QLineEdit();self.filter.setPlaceholderText('筛选本地标题、DOI、期刊');self.filter.textChanged.connect(self.refresh)
        row.addWidget(self.filter);self.strict_view=QCheckBox('只看符合筛选条件');self.strict_view.toggled.connect(self.refresh);row.addWidget(self.strict_view);v.addLayout(row)
        self.lib_table=table(['论文','期刊','发表日期','JIF / 状态']);self.lib_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.lib_table.itemSelectionChanged.connect(self.select_paper);v.addWidget(self.lib_table,1)
        self.detail=QTextBrowser();self.detail.setOpenExternalLinks(True);self.detail.setMaximumHeight(175);v.addWidget(self.detail)
        row=QHBoxLayout();row.addWidget(button('出版社 / DOI',self.open_doi));row.addWidget(button('获取开放 PDF',self.get_pdf));row.addWidget(button('阅读本地 PDF',self.open_reader_selected));row.addStretch();v.addLayout(row)
    def make_reader(self):
        v=self.page('PDF 留白精读');row=QHBoxLayout()
        self.import_btn=button('导入 PDF',self.import_pdf);row.addWidget(self.import_btn)
        self.generate_btn=button('生成 / 继续留白精读',self.annotate_margins,True);row.addWidget(self.generate_btn)
        row.addWidget(button('导出留白 PDF',self.export_pdf));row.addStretch();self.pdf_title=label('尚未导入 PDF','muted');row.addWidget(self.pdf_title);v.addLayout(row)
        row=QHBoxLayout();self.view_kind=QComboBox();self.view_kind.addItems(['原版 PDF','留白精读 PDF']);self.view_kind.currentIndexChanged.connect(self.switch_pdf);row.addWidget(self.view_kind)
        row.addWidget(button('上一页',lambda:self.move_page(-1)));self.page_label=label('0 / 0');row.addWidget(self.page_label);row.addWidget(button('下一页',lambda:self.move_page(1)));row.addStretch()
        row.addWidget(button('−',lambda:self.change_zoom(-10)));self.zoom=QSpinBox();self.zoom.setRange(25,400);self.zoom.setSuffix(' %');self.zoom.setValue(100);self.zoom.valueChanged.connect(self.zoom_changed);row.addWidget(self.zoom)
        row.addWidget(button('+',lambda:self.change_zoom(10)));row.addWidget(button('适合宽度',self.fit_width));row.addWidget(button('100%',lambda:self.zoom.setValue(100)));v.addLayout(row)
        self.pdf_area=PdfArea();self.pdf_area.setAlignment(Qt.AlignCenter);self.pdf_area.setWidgetResizable(False);self.pdf_label=QLabel();self.pdf_label.setAlignment(Qt.AlignCenter);self.pdf_area.setWidget(self.pdf_label);self.pdf_area.zoomed.connect(self.change_zoom);v.addWidget(self.pdf_area,1)
        self.reader_status=label('左侧整段中文，右侧专业词 / 短语 / 长难句及音标。内容过多自动续页；Ctrl + 滚轮缩放。','muted');v.addWidget(self.reader_status)
    def make_settings(self):
        v=self.page('设置');scroll=QScrollArea();scroll.setWidgetResizable(True);content=QWidget();form=QFormLayout(content);form.setSpacing(14);s=self.store.settings()
        self.base=QLineEdit(s['base_url']);form.addRow('API 地址',self.base)
        self.secret=QLineEdit();self.secret.setEchoMode(QLineEdit.Password);self.secret.setPlaceholderText('已保存，留空保持不变' if s['api_secret'] else '输入 API Key');form.addRow('API Key',self.secret)
        self.model=QComboBox();self.model.setEditable(True);self.model.addItem(s['model']);form.addRow('模型',self.model)
        form.addRow('',button('保存并测试连接',self.test_api))
        self.budget=QSpinBox();self.budget.setRange(10000,100000000);self.budget.setSingleStep(100000);self.budget.setValue(s['daily_token_limit']);form.addRow('每日 Token 基础额度',self.budget)
        self.usage=label('','muted');form.addRow('',self.usage)
        form.addRow('',label('默认每日 100 万 Token；不足时询问是否仅为今天追加 100 万。取消会暂停，已完成段落保留。此额度是软件预算，不会更改 API 账户余额。','muted'))
        self.queries=QTextEdit();self.queries.setPlainText('\n'.join(s['queries']));self.queries.setMaximumHeight(95);form.addRow('英文检索词（每行一组，可留空）',self.queries)
        self.days=QSpinBox();self.days.setRange(1,3650);self.days.setValue(s['days']);form.addRow('查询最近多少天',self.days)
        self.minif=QDoubleSpinBox();self.minif.setRange(0,100);self.minif.setValue(s['min_if']);form.addRow('JIF 必须大于',self.minif)
        self.year=QSpinBox();self.year.setRange(2020,date.today().year);self.year.setValue(s['metric_year']);form.addRow('JIF 年份',self.year)
        self.maxpages=QSpinBox();self.maxpages.setRange(3,100);self.maxpages.setValue(s['max_pages']);form.addRow('短篇优先页数',self.maxpages)
        self.strict=QCheckBox('严格限制页数，页数未知也不算达标');self.strict.setChecked(s['strict_pages']);form.addRow('',self.strict)
        form.addRow('',label('内置 JIF 表仅含 3 本期刊的 2024 年指标，可导入带年份及来源的 CSV 扩充。','muted'))
        row=QHBoxLayout();row.addWidget(button('导出指标表',self.export_metrics));row.addWidget(button('导入指标表',self.import_metrics));form.addRow('',row)
        scroll.setWidget(content);v.addWidget(scroll,1);v.addWidget(button('保存设置',self.save_settings,True))
    def save_settings(self,*_):
        if self.worker and self.worker.isRunning():return False
        try:
            from services import public_url
            topic=self.topic_input.text().strip()
            if not 1<=len(topic)<=1500:raise ValueError('请填写研究方向，最多 1500 字。')
            qs=[q.strip() for q in self.queries.toPlainText().splitlines() if q.strip()]
            if qs:qs=query_lines(qs)
            s=self.store.settings();s.update(base_url=public_url(self.base.text().strip().rstrip('/')),model=self.model.currentText().strip(),
                daily_token_limit=self.budget.value(),topic_mode='custom',research_topic=topic,queries=qs,schedule=False,
                days=self.days.value(),min_if=self.minif.value(),metric_year=self.year.value(),max_pages=self.maxpages.value(),strict_pages=self.strict.isChecked())
            if not s['model']:raise ValueError('请填写模型名称。')
            if self.secret.text().strip():s['api_secret']=protect(self.secret.text().strip())
            self.store.save_settings(s);self.secret.clear();self.secret.setPlaceholderText('已保存，留空保持不变' if s['api_secret'] else '输入 API Key')
            self.refresh();self.status.setText('设置已保存。');return True
        except Exception as e:self.job_error(safe_error(e));return False
    def run_job(self,name,fn,callback):
        if self.worker and self.worker.isRunning():self.status.setText('当前任务仍在运行，请等待或暂停。');return
        self.cancel_event=threading.Event();self.worker=Worker(fn,self.cancel_event,self);self.worker.budget.connect(self.confirm_budget)
        self.store.budget_handler=self.worker.approve_budget
        self.worker.progress.connect(self.status.setText);self.worker.result.connect(callback);self.worker.failed.connect(self.job_error);self.worker.finished.connect(self.job_finished)
        self.tabs.widget(2).setEnabled(False);self.tabs.widget(0).setEnabled(False);self.topic_input.setEnabled(False);self.view_kind.setEnabled(False)
        for b in (self.search_btn,self.generate_btn,self.import_btn):b.setEnabled(False)
        self.progress.show();self.cancel_btn.show();self.status.setText(name);self.worker.start()
    def confirm_budget(self,r):
        if self.cancel_event and self.cancel_event.is_set():r['event'].set();return
        prompt=f'今日已用 {r["used"]:,} Token，当前额度 {r["limit"]:,}。\n下一请求预计最多 {r["estimate"]:,} Token。\n\n是否仅为今天追加 {r["extra"]:,} Token 并继续？\n这会继续调用付费 API。选择“否”将暂停，已完成段落保留。'
        answer=QMessageBox.question(self,'额度不足：是否继续？',prompt,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        r['approved']=answer==QMessageBox.Yes;r['event'].set()
    def job_finished(self):
        self.store.budget_handler=None;self.tabs.widget(2).setEnabled(True);self.tabs.widget(0).setEnabled(True);self.topic_input.setEnabled(True);self.view_kind.setEnabled(True)
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
        rows=[];s=self.store.settings();q=self.filter.text().casefold()
        for p in self.store.papers():
            ok,score,reasons,m=eligibility(p,self.store,s)
            if q not in (p['title']+' '+p['doi']+' '+p['journal']).casefold():continue
            if self.strict_view.isChecked() and not ok:continue
            rows.append((ok,score,p,reasons,m))
        rows.sort(key=lambda x:(x[0],x[1],x[2].get('date','')),reverse=True);self.lib_table.blockSignals(True);self.lib_table.setRowCount(len(rows))
        for i,(ok,score,p,reasons,m) in enumerate(rows):
            title=current_assessment(p,s).get('title_zh') or p['title']
            for j,text in enumerate([title,p['journal'],p.get('date',''),(str(m['value']) if m else '未核实')+(' / 符合' if ok else ' / 候选')]):
                item=QTableWidgetItem(text);item.setData(Qt.UserRole,p['doi']);item.setToolTip('; '.join(reasons));self.lib_table.setItem(i,j,item)
            self.lib_table.setRowHeight(i,48)
        self.lib_table.blockSignals(False)
    def select_paper(self):
        row=self.lib_table.currentRow()
        if row<0 or not self.lib_table.item(row,0):return
        self.selected=self.lib_table.item(row,0).data(Qt.UserRole);p=self.store.paper(self.selected);e=html.escape
        a=current_assessment(p,self.store.settings());_,_,reasons,m=eligibility(p,self.store)
        self.detail.setHtml('<h3>'+e(p['title'])+'</h3><p>'+e('；'.join(reasons) or '符合当前筛选')+'</p><p>'+e(a.get('reason',''))+'</p><p>'+e(p.get('abstract','') or '来源未提供摘要')+'</p>')
    def open_doi(self):
        p=self.store.paper(self.selected) if self.selected else None
        if p and p.get('url','').startswith('https://'):QDesktopServices.openUrl(QUrl(p['url']))
    def get_pdf(self):
        p=self.store.paper(self.selected) if self.selected else None
        if not p:self.status.setText('请先选择一篇论文。');return
        if not p.get('pdf_url'):self.status.setText('暂无开放 PDF 链接，可从出版社下载后导入。');return
        dest=self.store.root/'pdfs'/(hashlib.sha256(p['doi'].encode()).hexdigest()[:20]+'.pdf');doi=p['doi']
        def done(path):self.store.set_flag(doi,'pdf',path);self.load_pdf(path);self.tabs.setCurrentIndex(1)
        self.run_job('获取开放 PDF…',lambda c,pr:download_pdf(p['pdf_url'],dest,c),done)
    def open_reader_selected(self):
        p=self.store.paper(self.selected) if self.selected else None
        if p and p.get('pdf') and Path(p['pdf']).exists():self.load_pdf(p['pdf']);self.tabs.setCurrentIndex(1)
        else:self.status.setText('请先获取开放 PDF 或在精读页导入本地 PDF。')
    def import_pdf(self,*_):
        path,_=QFileDialog.getOpenFileName(self,'导入论文 PDF','','PDF (*.pdf)')
        if not path:return
        try:p=import_local(self.store,path);self.load_pdf(p['pdf']);self.tabs.setCurrentIndex(1);self.refresh()
        except Exception as e:self.job_error(safe_error(e))
    def load_pdf(self,path,title=''):
        from margins import cache_folder
        doc=extract_document(path);self.pdf_path=str(Path(path).resolve());self.pdf_title.setText(title or Path(path).name);self.store.update_settings(last_pdf=self.pdf_path)
        folder=cache_folder(self.store,doc);output=folder/'页边精读批注_原版左右留白.pdf';self.margin_output=str(output) if output.exists() else ''
        self.view_kind.blockSignals(True);self.view_kind.setCurrentIndex(1 if self.margin_output else 0);self.view_kind.blockSignals(False);self.switch_pdf()
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
        self.run_job('恢复缓存并生成留白精读…',lambda c,p:MarginAnnotator(self.store,c,p).run(path),self.margin_done)
    def margin_done(self,r):
        self.margin_output=r['pdf'];self.view_kind.blockSignals(True);self.view_kind.setCurrentIndex(1);self.view_kind.blockSignals(False);self.switch_pdf();a=r['audit']
        self.status.setText(f'完成：{a["categories"]["paragraph"]} 段译文、{a["items"]-a["categories"]["paragraph"]} 条批注。已在软件内打开，可缩放或导出。')
        if a['warnings']:self.reader_status.setText('；'.join(a['warnings']))
    def export_pdf(self):
        if not self.margin_output:self.status.setText('请先生成留白精读 PDF。');return
        target,_=QFileDialog.getSaveFileName(self,'导出留白精读 PDF',Path(self.pdf_path).stem+'_留白精读.pdf','PDF (*.pdf)')
        if target and Path(target).resolve()!=Path(self.margin_output).resolve():shutil.copy2(self.margin_output,target);self.status.setText('PDF 已导出。')
    def test_api(self):
        if not self.save_settings():return
        def done(models):
            current=self.model.currentText();self.model.clear();self.model.addItems(models)
            if current in models:self.model.setCurrentText(current)
            self.store.update_settings(model=self.model.currentText());self.status.setText('API 连接成功。')
        self.run_job('测试 API…',lambda c,p:DeepSeek(self.store,cancel=c).models(),done)
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
