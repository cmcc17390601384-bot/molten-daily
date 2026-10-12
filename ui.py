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
from core import APP,VERSION,SEEDS,Store,protect,unprotect,safe_error,set_autostart
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
    progress=Signal(str); result=Signal(object); failed=Signal(str)
    def __init__(self,fn,cancel,parent=None): super().__init__(parent); self.fn=fn; self.cancel=cancel
    def run(self):
        try: self.result.emit(self.fn(self.cancel,self.progress.emit))
        except Exception as e: self.failed.emit(safe_error(e))

class Window(QMainWindow):
    def __init__(self,store=None,smoke=False):
        super().__init__(); self.store=store or Store(); self.smoke=smoke; self.worker=None; self.cancel_event=None
        self.selected=None; self.pdf_path=''; self.source_doc=None; self.translation_state=None; self.page_no=0; self.want_quit=False
        self.setWindowTitle(APP+' · '+VERSION); self.setWindowIcon(icon()); self.resize(1440,940); self.setMinimumSize(1100,740)
        self.setStyleSheet(STYLE); central=QWidget(); self.setCentralWidget(central); outer=QHBoxLayout(central); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        side=QWidget(); side.setObjectName('sidebar'); side.setFixedWidth(210); sv=QVBoxLayout(side); sv.setContentsMargins(22,30,22,20)
        sv.addWidget(label('熔盐日课','brand')); sv.addWidget(label('MOLTEN DAILY\n每天读懂一点前沿','subbrand')); sv.addSpacing(25)
        self.nav=QListWidget(); self.nav.setObjectName('nav'); self.nav.addItems(['01   每日精选','02   我的文献','03   对照精读','04   生词本','05   设置与指标','06   运行记录','07   文献分析']); sv.addWidget(self.nav)
        sv.addWidget(label('原生桌面 · 本地资料库\n核能优先 / 证据可追溯','subbrand')); outer.addWidget(side)
        right=QWidget(); rv=QVBoxLayout(right); rv.setContentsMargins(26,22,26,14); self.stack=QStackedWidget(); rv.addWidget(self.stack,1)
        self.status=label('就绪。每日精选依据实际出版记录，暂无新文献时不会虚构推荐。','muted'); rv.addWidget(self.status)
        bottom=QHBoxLayout(); self.progress=QProgressBar(); self.progress.setRange(0,0); self.progress.hide(); bottom.addWidget(self.progress,1)
        self.cancel_btn=button('停止当前任务',self.cancel); self.cancel_btn.hide(); bottom.addWidget(self.cancel_btn); rv.addLayout(bottom); outer.addWidget(right,1)
        self.make_dashboard(); self.make_library(); self.make_reader(); self.make_glossary(); self.make_settings(); self.make_logs();self.make_analysis()
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex); self.nav.setCurrentRow(0)
        self.tray=QSystemTrayIcon(self.windowIcon(),self); menu=QMenu(); menu.addAction('打开熔盐日课',self.show_normal); menu.addAction('立即查询',self.search); menu.addSeparator(); menu.addAction('退出',self.quit_app)
        self.tray.setContextMenu(menu); self.tray.setToolTip(APP); self.tray.activated.connect(lambda reason:self.show_normal() if reason==QSystemTrayIcon.DoubleClick else None)
        self.tray.messageClicked.connect(self.show_normal)
        if not smoke and QSystemTrayIcon.isSystemTrayAvailable(): self.tray.show()
        self.timer=QTimer(self); self.timer.timeout.connect(self.tick)
        if not smoke: self.timer.start(60000); QTimer.singleShot(12000,self.tick)
        self.refresh()
    def page(self,title,subtitle):
        w=QWidget(); v=QVBoxLayout(w); v.setContentsMargins(0,0,0,0); v.setSpacing(14); v.addWidget(label(title,'heading')); v.addWidget(label(subtitle,'muted')); self.stack.addWidget(w); return v
    def make_dashboard(self):
        v=self.page('每日精选','少量、高相关、可追溯。新文献窗口与篇幅偏好可在设置中调整。')
        h=QHBoxLayout(); self.cards=[]
        for text in ['可学习文献','待读文献','今日 Token']:
            l=label(text,'card'); h.addWidget(l); self.cards.append(l)
        v.addLayout(h); h=QHBoxLayout(); self.search_btn=button('立即查询新文献',self.search,True); h.addWidget(self.search_btn)
        h.addWidget(button('加载入门文献',self.seed)); h.addWidget(button('按 DOI 加入',self.add_doi)); h.addStretch(); v.addLayout(h)
        last=self.store.settings().get('last_success','')
        self.digest=label(('上次成功检索：'+last+'。下方显示当前符合条件的待读文献。') if last else '尚未查询。首次使用可加载前两篇核能背景文献作为学习起点。','muted'); v.addWidget(self.digest)
        self.daily_table=table(['论文 / 推荐依据','期刊','JIF（年份）','页数','发表日期']); self.daily_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.daily_table.itemSelectionChanged.connect(lambda:self.select_paper(self.daily_table)); self.daily_table.doubleClicked.connect(lambda _:self.open_reader_selected())
        v.addWidget(self.daily_table,1); self.daily_detail=QTextBrowser(); self.daily_detail.setMaximumHeight(230); self.daily_detail.setOpenExternalLinks(True); v.addWidget(self.daily_detail)
        self.action_row(v)
    def make_library(self):
        v=self.page('我的文献','收藏、已读和原文路径保存在本机；“候选”包含指标未知或不满足严格条件的记录。')
        h=QHBoxLayout(); self.filter=QLineEdit(); self.filter.setPlaceholderText('搜索标题、DOI、期刊'); self.filter.textChanged.connect(self.refresh)
        self.scope=QComboBox(); self.scope.addItems(['全部文献','严格符合','候选 / 指标待核实','已收藏','未读']); self.scope.currentIndexChanged.connect(self.refresh)
        h.addWidget(self.filter,1); h.addWidget(self.scope); h.addWidget(button('导入本地 PDF',lambda:self.import_pdf(link=False))); v.addLayout(h)
        self.lib_table=table(['论文','期刊','JIF（年份）','页数','状态']); self.lib_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.lib_table.itemSelectionChanged.connect(lambda:self.select_paper(self.lib_table)); self.lib_table.doubleClicked.connect(lambda _:self.open_reader_selected()); v.addWidget(self.lib_table,1)
        self.detail=QTextBrowser(); self.detail.setOpenExternalLinks(True); self.detail.setMaximumHeight(260); v.addWidget(self.detail); self.action_row(v)
    def action_row(self,v):
        h=QHBoxLayout(); h.addWidget(button('出版社 / DOI',self.open_doi)); h.addWidget(button('获取开放 PDF',self.get_pdf)); h.addWidget(button('关联本地 PDF',self.import_pdf)); h.addWidget(button('进入精读',self.open_reader_selected)); h.addWidget(button('收藏 / 取消',self.favorite)); h.addWidget(button('标为已读',self.mark_read)); h.addStretch(); v.addLayout(h)
    def make_reader(self):
        v=self.page('对照精读','原文完整保留；中文按文本块编号对应。首次可试译第 1 页，再继续全文。翻译会将提取的文字发送到已配置 API。')
        h=QHBoxLayout(); h.addWidget(button('导入 PDF',lambda:self.import_pdf(link=False))); self.pdf_title=label('尚未打开论文','muted'); h.addWidget(self.pdf_title,1); v.addLayout(h)
        h=QHBoxLayout(); h.addWidget(button('上一页',lambda:self.move_page(-1))); self.page_label=QLabel('0 / 0'); h.addWidget(self.page_label); h.addWidget(button('下一页',lambda:self.move_page(1))); h.addStretch()
        self.translate_first=button('试译第 1 页',lambda:self.translate(1)); self.translate_all=button('全文翻译 / 继续',lambda:self.translate(None),True)
        h.addWidget(self.translate_first); h.addWidget(self.translate_all); h.addWidget(button('打开对照 PDF',self.open_translation)); h.addWidget(button('打开导出目录',self.open_translation_folder)); v.addLayout(h)
        mh=QHBoxLayout();mh.addWidget(button('分析当前文献',self.analyze_current,True));mh.addWidget(button('生成页边精读批注',self.annotate_margins));mh.addWidget(button('打开页边批注 PDF',self.open_margins));mh.addWidget(label('新增左右留白批注，按页缓存。分析与批注会调用 API。','muted'),1);v.addLayout(mh)
        split=QSplitter(); self.pdf_area=QScrollArea(); self.pdf_area.setWidgetResizable(True); self.pdf_label=QLabel(); self.pdf_label.setAlignment(Qt.AlignTop|Qt.AlignHCenter); self.pdf_area.setWidget(self.pdf_label)
        self.zh=QTextBrowser(); self.zh.setOpenLinks(False); self.zh.anchorClicked.connect(self.jump_block); split.addWidget(self.pdf_area); split.addWidget(self.zh); split.setSizes([600,600]); v.addWidget(split,1)
        self.reader_status=label('公式和图内文字以原文为准；扫描页不被计入已翻译文本。','muted'); v.addWidget(self.reader_status)
    def make_glossary(self):
        v=self.page('生词本','原文中实际出现的词汇，附音标、语境释义和来源标识。AI 生成的音标会与内置词表区分。')
        self.gloss_table=table(['词汇','读音（IPA）','语境释义','来源']); self.gloss_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); v.addWidget(self.gloss_table,1)
        h=QHBoxLayout(); h.addWidget(button('刷新词汇',self.refresh_gloss)); h.addWidget(button('导出 CSV',self.export_gloss)); h.addStretch(); v.addLayout(h)
    def make_settings(self):
        v=self.page('设置与指标','密钥使用 Windows 系统加密；不会写入日志。指标按选定年份严格匹配，可导入 CSV 扩大期刊覆盖。')
        scroll=QScrollArea(); scroll.setWidgetResizable(True); content=QWidget(); form=QFormLayout(content); form.setSpacing(12); s=self.store.settings()
        self.base=QLineEdit(s['base_url']); form.addRow('API 地址（HTTPS）',self.base)
        self.secret=QLineEdit(); self.secret.setEchoMode(QLineEdit.Password); self.secret.setPlaceholderText('已加密保存；留空保持不变' if s['api_secret'] else '输入 DeepSeek API Key'); form.addRow('DeepSeek 密钥',self.secret)
        scope='当前保存方式：本机绑定 DPAPI（受限环境回退）。请勿共享 userdata；在正常账户重新保存密钥可使用账户绑定。' if s['api_secret'].startswith('machine:') else '优先当前账户 DPAPI；受限环境回退为本机绑定 DPAPI。不要共享 userdata。'
        form.addRow('',label(scope,'muted'))
        self.model=QComboBox(); self.model.setEditable(True); self.model.addItems(list(dict.fromkeys([s['model'],'deepseek-flash','deepseek-v4-pro']))); form.addRow('模型',self.model)
        h=QHBoxLayout(); h.addWidget(button('保存并测试连接 / 读取模型',self.test_api)); self.clear_key=button('清除密钥',self.clear_api); h.addWidget(self.clear_key); form.addRow('',h)
        self.oa_key=QLineEdit(); self.oa_key.setEchoMode(QLineEdit.Password); self.oa_key.setPlaceholderText('可选；免费接口限流时可在 OpenAlex 获取密钥'); form.addRow('OpenAlex 密钥',self.oa_key)
        self.email=QLineEdit(s['email']); form.addRow('Crossref 联系邮箱（可选）',self.email)
        self.days=QSpinBox(); self.days.setRange(1,3650); self.days.setValue(s['days']); form.addRow('查询最近多少天',self.days)
        self.count=QSpinBox(); self.count.setRange(1,5); self.count.setValue(s['daily_count']); form.addRow('每次最多推送',self.count)
        self.maxpages=QSpinBox(); self.maxpages.setRange(3,50); self.maxpages.setValue(s['max_pages']); form.addRow('短篇优先页数',self.maxpages)
        self.strict=QCheckBox('严格限制页数（页数未知也不推送）'); self.strict.setChecked(s['strict_pages']); form.addRow('',self.strict)
        self.minif=QDoubleSpinBox(); self.minif.setRange(0,100); self.minif.setValue(s['min_if']); form.addRow('JIF 必须大于',self.minif)
        self.year=QSpinBox(); self.year.setRange(2020,date.today().year); self.year.setValue(s['metric_year']); form.addRow('采用 JIF 年份',self.year)
        self.nuclear=QCheckBox('仅核能相关（默认优先核能，可兼顾其他熔盐电化学）'); self.nuclear.setChecked(s['nuclear_only']); form.addRow('',self.nuclear)
        self.airank=QCheckBox('用 DeepSeek 分析推荐理由（消耗 API Token）'); self.airank.setChecked(s['ai_ranking']); form.addRow('',self.airank)
        self.budget=QSpinBox(); self.budget.setRange(1000,1000000); self.budget.setSingleStep(10000); self.budget.setValue(s['daily_token_limit']); form.addRow('每天 Token 预算上限',self.budget)
        self.schedule=QCheckBox('每天自动查询并发送系统托盘通知'); self.schedule.setChecked(s['schedule']); form.addRow('',self.schedule)
        self.clock=QTimeEdit(QTime.fromString(s['schedule_time'],'HH:mm')); self.clock.setDisplayFormat('HH:mm'); form.addRow('每天本地时间',self.clock)
        self.startup=QCheckBox('登录 Windows 后启动到托盘'); self.startup.setChecked(s['autostart']); form.addRow('',self.startup)
        self.queries=QTextEdit('\n'.join(s['queries'])); self.queries.setMaximumHeight(115); form.addRow('检索词（每行一组，最多 6 组）',self.queries)
        self.metric_table=table(['期刊','ISSN','年份','JIF','来源','核实日期']); self.metric_table.setMinimumHeight(170); form.addRow('指标表',self.metric_table)
        h=QHBoxLayout(); h.addWidget(button('导入指标 CSV',self.import_metrics)); h.addWidget(button('导出指标 CSV',self.export_metrics)); h.addWidget(button('打开本机数据目录',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root))))); form.addRow('',h)
        form.addRow('',label('预置指标表仅覆盖 3 本已核实期刊，采用 2024 年数据。其他期刊会进入候选，导入有年份/来源的 JIF 后参与筛选。','muted'))
        scroll.setWidget(content); v.addWidget(scroll,1); v.addWidget(button('保存设置',self.save_settings,True)); self.load_metric_table()
    def make_logs(self):
        v=self.page('运行记录','区分检索成功、部分来源失败、API 错误与翻译中断；日志自动隐藏密钥。'); self.logs=QTextBrowser(); v.addWidget(self.logs,1); v.addWidget(button('刷新记录',self.refresh_logs))
    def run_job(self,name,fn,callback):
        if self.worker and self.worker.isRunning(): self.status.setText('当前任务仍在运行；可先停止再开始其他任务。'); return
        self.cancel_event=threading.Event(); self.worker=Worker(fn,self.cancel_event,self); self.progress.show(); self.cancel_btn.show(); self.search_btn.setEnabled(False)
        self.translate_first.setEnabled(False); self.translate_all.setEnabled(False); self.status.setText(name)
        self.worker.progress.connect(self.status.setText); self.worker.result.connect(callback); self.worker.failed.connect(self.job_error); self.worker.finished.connect(self.job_finished); self.worker.start()
    def job_finished(self):
        self.progress.hide(); self.cancel_btn.hide(); self.search_btn.setEnabled(True); self.translate_first.setEnabled(True); self.translate_all.setEnabled(True); self.refresh(); self.refresh_logs()
        if self.want_quit: QApplication.quit()
    def job_error(self,text):
        self.status.setText(text); self.store.event('错误 / 中断',text)
        if not self.smoke: QMessageBox.warning(self,'任务未完成',text)
    def cancel(self):
        if self.cancel_event: self.cancel_event.set(); self.status.setText('正在停止；当前网络请求结束后保留已完成进度。')
    def search(self): self.run_job('正在检索…',lambda c,p:Retriever(self.store,c,p).search(),self.search_done)
    def search_done(self,r):
        s=f"检索 {r['fetched']} 条记录，主题相关 {r['relevant']} 篇；新增 {r['new']} 篇，本次精选 {len(r['selected'])} 篇。"
        if not r['selected']: s+=' 暂无尚未推送且符合条件的新文献，可继续阅读资料库。'
        if r['errors']: s+=' 部分服务未完成，详见运行记录。'
        self.digest.setText(s); self.status.setText(s)
        if r['selected'] and self.tray.isVisible():
            p=r['selected'][0]; self.tray.showMessage(APP,f"新增 {len(r['selected'])} 篇精选\n"+p.get('ai',{}).get('title_zh',p['title']),QSystemTrayIcon.Information,15000)
        self.refresh()
    def tick(self):
        if self.worker and self.worker.isRunning(): return
        s=self.store.settings(); now=datetime.now()
        if s['schedule'] and now.strftime('%H:%M')>=s['schedule_time'] and s['last_auto_day']!=now.date().isoformat():
            self.store.update_settings(last_auto_day=now.date().isoformat()); self.search()
    def seed(self):
        for p in SEEDS: self.store.put(p)
        self.digest.setText('已加入 2 篇入门文献（2021 / 2025），明确归入学习资料库，不冒充近期新论文。'); self.refresh(); self.nav.setCurrentRow(1)
    def add_doi(self):
        value,ok=QInputDialog.getText(self,'按 DOI 加入','DOI 或 https://doi.org/...')
        if ok and value: self.run_job('正在验证 DOI…',lambda c,p:Retriever(self.store,c,p).by_doi(value),lambda r:self.status.setText('已验证并加入：'+r['title']))
    def refresh(self,*_):
        allp=self.store.papers(); s=self.store.settings(); cutoff=(date.today()-timedelta(days=s['days'])).isoformat()
        daily=[p for p in allp if not p.get('seed') and not p['seen'] and cutoff<=p.get('date','')<=date.today().isoformat() and eligibility(p,self.store,s)[0]]
        daily.sort(key=lambda p:(eligibility(p,self.store,s)[1],p.get('date','')),reverse=True); self.fill_papers(self.daily_table,daily[:s['daily_count']],True)
        self.cards[0].setText(f'资料库\n{len(allp)} 篇'); self.cards[1].setText(f'待读\n{sum(not p["seen"] for p in allp)} 篇'); self.cards[2].setText(f'今日用量\n{self.store.tokens():,} Token')
        if not hasattr(self,'filter'): return
        query=self.filter.text().casefold(); scope=self.scope.currentIndex(); filtered=[]
        for p in allp:
            if query not in (p['title']+' '+p['doi']+' '+p['journal']).casefold(): continue
            ok=eligibility(p,self.store,s)[0]
            if (scope==1 and not ok) or (scope==2 and ok) or (scope==3 and not p['favorite']) or (scope==4 and p['seen']): continue
            filtered.append(p)
        filtered.sort(key=lambda p:(p['favorite'],p.get('date','')),reverse=True); self.fill_papers(self.lib_table,filtered)
    def fill_papers(self,t,rows,daily=False):
        t.blockSignals(True); t.setRowCount(len(rows))
        for i,p in enumerate(rows):
            ok,score,reasons,m=eligibility(p,self.store); title=p.get('ai',{}).get('title_zh') or p['title']
            if p['favorite']: title='★ '+title
            metric=f"{float(m['value']):g} ({m['year']})" if m else '未核实'
            cells=[title,p['journal'],metric,str(p.get('pages') or '未知'),p.get('date','') if daily else ('已读' if p['seen'] else '未读')+(' / 入门' if p.get('seed') else ' / 符合' if ok else ' / 候选')]
            for j,x in enumerate(cells):
                it=QTableWidgetItem(x); it.setData(Qt.UserRole,p['doi']); it.setToolTip(p['title']+'\n'+('; '.join(reasons) if reasons else '符合当前筛选；具体创新性以原文为准')); t.setItem(i,j,it)
            t.setRowHeight(i,56)
        t.blockSignals(False)
    def select_paper(self,t):
        row=t.currentRow()
        if row<0 or not t.item(row,0): return
        self.selected=t.item(row,0).data(Qt.UserRole); p=self.store.paper(self.selected)
        if not p:return
        ok,score,reasons,m=eligibility(p,self.store); e=html.escape
        identity='本地文献（DOI 尚未核实）' if p['doi'].startswith('local:') else f'<a href="{e(p.get("url",""))}">{e(p["doi"])}</a>'
        text=f'<h3>{e(p["title"])}</h3><p>{e(p.get("authors",""))}</p><p>{identity} · {e(p.get("date",""))} · {e(p.get("source",""))}</p>'
        text+=f'<p><b>{"符合当前筛选" if ok else "候选："+e("；".join(reasons))}</b> · 相关性参考分 {score}</p>'
        if m: text+=f'<p>JIF {m["value"]}（{m["year"]}）· <a href="{e(m["source"])}">指标来源</a> · 核实日期 {m["checked"]}</p>'
        if p.get('ai'):
            a=p['ai']; text+=f'<p><b>推荐理由</b> {e(a["reason"])}<br><b>新意</b> {e(a["novelty"])}<br><b>学习问题</b> {e(" / ".join(a["questions"]))}<br><small>{e(a["basis"])}</small></p>'
        text+='<p><b>摘要 / 来源说明</b><br>'+e(p.get('abstract') or '数据库未提供摘要；不据此推断实验结论。')+'</p>'
        self.detail.setHtml(text); self.daily_detail.setHtml(text)
    def favorite(self):
        if self.selected:
            p=self.store.paper(self.selected)
            if p:self.store.set_flag(self.selected,'favorite',int(not p['favorite'])); self.refresh()
    def mark_read(self):
        if self.selected:self.store.set_flag(self.selected,'seen',1); self.refresh(); self.status.setText('已记录本篇阅读完成。')
    def open_doi(self):
        p=self.store.paper(self.selected) if self.selected else None
        if p and p.get('url','').startswith('https://'): QDesktopServices.openUrl(QUrl(p['url']))
        elif p:self.status.setText('本地文献尚未核实 DOI，可使用“按 DOI 加入”查询出版社题录。')
    def get_pdf(self):
        p=self.store.paper(self.selected) if self.selected else None
        if not p:return
        if not p.get('pdf_url'):
            self.status.setText('未找到开放获取 PDF 地址；可打开出版社页面下载后关联。'); return
        dest=self.store.root/'pdfs'/(hashlib.sha256(p['doi'].encode()).hexdigest()[:20]+'.pdf'); doi=p['doi']
        def done(path): self.store.set_flag(doi,'pdf',path); self.load_pdf(path); self.nav.setCurrentRow(2)
        self.run_job('下载开放 PDF…',lambda c,pr:download_pdf(p['pdf_url'],dest,c),done)
    def import_pdf(self,checked=False,link=True):
        path,_=QFileDialog.getOpenFileName(self,'导入有文本层的论文 PDF','','PDF (*.pdf)')
        if not path:return
        try:
            info=extract_document(path); dest=self.store.root/'pdfs'/(info['hash'][:20]+'.pdf')
            if Path(path).resolve()!=dest.resolve():shutil.copy2(path,dest)
            if link and self.selected:
                self.store.set_flag(self.selected,'pdf',str(dest)); p=self.store.paper(self.selected); p['pages']=info['page_count']; self.store.put(p)
            else:
                p=import_local(self.store,path);self.selected=p['doi'];dest=Path(p['pdf'])
            self.load_pdf(str(dest),Path(path).stem); self.nav.setCurrentRow(2);self.refresh();return str(dest)
        except Exception as e:self.job_error(safe_error(e))
    def make_analysis(self):
        v=self.page('文献分析','自行导入 PDF，梳理研究问题、方法、结果、创新与局限；每条分析附可定位的原文证据。')
        h=QHBoxLayout();h.addWidget(button('导入 PDF 并分析',self.import_and_analyze,True));h.addWidget(button('分析当前文献',self.analyze_current));h.addWidget(button('导出分析报告',self.export_analysis));h.addStretch();v.addLayout(h)
        self.analysis_view=QTextBrowser();self.analysis_view.setOpenLinks(False);self.analysis_view.anchorClicked.connect(self.jump_evidence);v.addWidget(self.analysis_view,1)
        self.analysis_view.setHtml('<h2>从自己的论文开始</h2><p>导入带文本层的 PDF 后，可分析、翻译，并生成原版左右留白批注。论文自动加入本地资料库。</p><p>分析依据提取的文字与图注；图像视觉内容不由模型直接读取。长文会均衡节选并显示分析范围。</p>');self.analysis_state=None;self.analysis_file=None
    def import_and_analyze(self):
        if self.import_pdf(link=False):self.analyze_current()
    def analyze_current(self):
        if not self.pdf_path:self.status.setText('请先导入或打开 PDF。');self.nav.setCurrentRow(6);return
        path=self.pdf_path;self.nav.setCurrentRow(6)
        def done(r):
            self.analysis_state=r['state'];self.analysis_file=r['markdown'];self.analysis_view.setHtml(report_html(r['state']));self.nav.setCurrentRow(6)
            self.status.setText('精读分析完成：每条结论附证据块，点击页码定位原文；报告已缓存，可导出。')
        self.run_job('正在分析当前论文…',lambda c,p:PaperAnalyzer(self.store,c,p).run(path),done)
    def export_analysis(self):
        if not self.analysis_file:self.status.setText('请先分析一篇文献。');return
        file,_=QFileDialog.getSaveFileName(self,'导出精读分析报告','文献精读分析.md','Markdown (*.md)')
        if file:shutil.copy2(self.analysis_file,file);self.status.setText('分析报告已导出。')
    def jump_evidence(self,url):
        if not self.analysis_state:return
        uid=url.toString().replace('evidence:','');doc=self.analysis_state['document'];unit=next((u for u in doc['units'] if u['id']==uid),None)
        if unit:
            if self.pdf_path!=doc['source']:self.load_pdf(doc['source'])
            self.page_no=unit['page'];self.nav.setCurrentRow(2);self.render_page(unit['bbox'])
            self.pdf_area.verticalScrollBar().setValue(int(unit['bbox'][1]*self.pdf_label.pixmap().width()/self.source_doc[self.page_no].rect.width))
    def open_reader_selected(self):
        p=self.store.paper(self.selected) if self.selected else None
        if not p:return
        if p.get('pdf') and Path(p['pdf']).exists():self.load_pdf(p['pdf'],p['title']); self.nav.setCurrentRow(2)
        else:self.status.setText('请先获取开放 PDF 或关联本地 PDF。')
    def load_pdf(self,path,title=''):
        if self.source_doc:self.source_doc.close()
        self.pdf_path=path; self.source_doc=fitz.open(path); self.page_no=0; self.translation_state=None; self.translation_folder=None
        self.pdf_title.setText(title or Path(path).name); digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        candidates=[]
        for f in (self.store.root/'translations').glob('*/translation.json'):
            try:
                st=json.loads(f.read_text('utf-8'))
                if st['document']['hash']==digest:candidates.append((f.stat().st_mtime,f,st))
            except (OSError,ValueError,KeyError):continue
        if candidates:
            _,f,self.translation_state=max(candidates,key=lambda x:x[0]); self.translation_folder=f.parent
        self.render_page()
    def move_page(self,n):
        if self.source_doc:self.page_no=max(0,min(len(self.source_doc)-1,self.page_no+n)); self.render_page()
    def render_page(self,highlight=None):
        if not self.source_doc:return
        p=self.source_doc[self.page_no]; pix=p.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False)
        im=QImage(pix.samples,pix.width,pix.height,pix.stride,QImage.Format_RGB888).copy(); pm=QPixmap.fromImage(im)
        if highlight:
            painter=QPainter(pm); painter.setPen(QColor('#ff9e27')); painter.drawRect(int(highlight[0]*1.5),int(highlight[1]*1.5),int((highlight[2]-highlight[0])*1.5),int((highlight[3]-highlight[1])*1.5)); painter.end()
        target=max(420,self.pdf_area.viewport().width()-24); self.pdf_label.setPixmap(pm.scaledToWidth(target,Qt.SmoothTransformation)); self.page_label.setText(f'{self.page_no+1} / {len(self.source_doc)}')
        e=html.escape; body='<h2>第 '+str(self.page_no+1)+' 页 · 中文对照</h2>'
        if self.translation_state:
            st=self.translation_state; units=[u for u in st['document']['units'] if u['page']==self.page_no]
            for u in units:
                r=st['results'].get(u['id']); body+=f'<p><a href="block:{u["id"]}">{u["id"]} ↗ 定位原文</a><br>'+(scientific_html(r['zh']) if r else '<i>尚未翻译</i>')+'</p>'
                if r:
                    for g in r.get('glossary',[]): body+='<p style="color:#52746b;font-size:10pt">'+e(g['term']+' '+g['ipa']+' — '+g['meaning'])+'</p>'
            self.reader_status.setText(f'已处理 {len(st["results"])} / {len(st["document"]["units"])} 个文本块；模型 {st["model"]}。点击编号定位原文。')
        else: body+='<p>点击“试译第 1 页”开始。原文图片、公式和上下标保持原样，译文逐块对应。</p>'
        self.zh.setHtml(body)
    def jump_block(self,url):
        if not self.translation_state:return
        uid=url.toString().replace('block:',''); u=next((x for x in self.translation_state['document']['units'] if x['id']==uid),None)
        if u:self.page_no=u['page']; self.render_page(u['bbox']); self.pdf_area.verticalScrollBar().setValue(int(u['bbox'][1]*self.pdf_label.pixmap().width()/self.source_doc[self.page_no].rect.width))
    def translate(self,limit):
        if not self.pdf_path:self.status.setText('请先导入或打开 PDF。');return
        path=self.pdf_path
        self.run_job('正在提取 PDF 文本…',lambda c,p:Translator(self.store,c,p).run(path,limit),self.translation_done)
    def translation_done(self,r):
        self.translation_folder=Path(r['folder']); self.translation_state=json.loads(Path(r['statefile']).read_text('utf-8')); self.render_page(); self.refresh_gloss()
        a=r['audit']; self.status.setText(f'{a["status"]}：{a["translated_blocks"]}/{a["total_blocks"]} 个文本块；已生成中英对照 PDF、原文批注及检查报告。')
    def open_translation(self):
        if getattr(self,'translation_folder',None):QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.translation_folder/'中英对照_中文双栏.pdf')))
    def open_translation_folder(self):
        if getattr(self,'translation_folder',None):QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.translation_folder)))
    def annotate_margins(self):
        if not self.pdf_path:self.status.setText('请先导入或打开 PDF。');return
        path=self.pdf_path
        def done(r):
            self.margin_output=r['pdf'];a=r['audit'];self.refresh_gloss();self.status.setText(f'页边精读已生成：{a["items"]} 条编号批注，{a["categories"]["word"]} 个专业词、{a["categories"]["phrase"]} 个短语、{a["categories"]["sentence"]} 个长难句。')
        self.run_job('正在生成原版左右留白批注…',lambda c,p:MarginAnnotator(self.store,c,p).run(path),done)
    def open_margins(self):
        if not self.pdf_path:return
        digest=hashlib.sha256(Path(self.pdf_path).read_bytes()).hexdigest();found=[]
        for f in (self.store.root/'translations').glob('margin_*/margins.json'):
            try:
                if json.loads(f.read_text('utf-8'))['document']['hash']==digest:found.append(f.parent/'页边精读批注_原版左右留白.pdf')
            except (OSError,ValueError,KeyError):continue
        found=[f for f in found if f.exists()]
        if found:QDesktopServices.openUrl(QUrl.fromLocalFile(str(max(found,key=lambda f:f.stat().st_mtime))))
        else:self.status.setText('当前论文尚未生成页边精读批注。')
    def refresh_gloss(self):
        terms={}
        for f in (self.store.root/'translations').glob('*/词汇表.json'):
            try:
                for g in json.loads(f.read_text('utf-8')):terms[g['term'].casefold()]=g
            except (OSError,ValueError,KeyError):continue
        self.gloss=list(sorted(terms.values(),key=lambda x:x['term'])); self.gloss_table.setRowCount(len(self.gloss))
        for i,g in enumerate(self.gloss):
            for j,k in enumerate(['term','ipa','meaning','origin']):self.gloss_table.setItem(i,j,QTableWidgetItem(g[k]))
    def export_gloss(self):
        self.refresh_gloss(); file,_=QFileDialog.getSaveFileName(self,'导出生词 CSV','熔盐生词.csv','CSV (*.csv)')
        if file:
            with open(file,'w',encoding='utf-8-sig',newline='') as f:
                w=csv.DictWriter(f,fieldnames=['term','ipa','meaning','origin']);w.writeheader();w.writerows(self.gloss)
    def save_settings(self,checked=False):
        try:
            from services import public_url
            base=public_url(self.base.text().strip().rstrip('/')); s=self.store.settings()
            queries=[q.strip() for q in self.queries.toPlainText().splitlines() if q.strip()][:6]
            if not queries:raise ValueError('至少保留一组检索词。')
            s.update(base_url=base,model=self.model.currentText().strip(),email=self.email.text().strip(),days=self.days.value(),daily_count=self.count.value(),
                max_pages=self.maxpages.value(),min_if=self.minif.value(),metric_year=self.year.value(),nuclear_only=self.nuclear.isChecked(),strict_pages=self.strict.isChecked(),
                ai_ranking=self.airank.isChecked(),daily_token_limit=self.budget.value(),schedule=self.schedule.isChecked(),schedule_time=self.clock.time().toString('HH:mm'),queries=queries)
            if not s['model']:raise ValueError('模型名不能为空。')
            if self.secret.text():s['api_secret']=protect(self.secret.text().strip())
            if self.oa_key.text():s['openalex_secret']=protect(self.oa_key.text().strip())
            if s['autostart']!=self.startup.isChecked():set_autostart(self.startup.isChecked());s['autostart']=self.startup.isChecked()
            self.store.save_settings(s);self.secret.clear();self.oa_key.clear();self.secret.setPlaceholderText('已加密保存；留空保持不变' if s['api_secret'] else '输入 DeepSeek API Key')
            self.status.setText('设置已保存。筛选严格使用所选年份 JIF；程序运行且电脑开机时自动检查。');self.refresh();return True
        except Exception as e:self.job_error(safe_error(e));return False
    def test_api(self):
        if not self.save_settings():return
        def done(models):
            current=self.model.currentText();self.model.clear();self.model.addItems(models)
            if current in models:self.model.setCurrentText(current)
            self.store.update_settings(model=self.model.currentText());self.status.setText('API 连接成功；可用模型：'+', '.join(models))
        self.run_job('测试官方 API 与模型列表…',lambda c,p:DeepSeek(self.store,cancel=c).models(),done)
    def clear_api(self):self.store.update_settings(api_secret='');self.secret.clear();self.secret.setPlaceholderText('输入新的 API Key');self.status.setText('已删除本机保存的 DeepSeek 密钥。')
    def load_metric_table(self):
        rows=self.store.metrics();self.metric_table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            for j,k in enumerate(['journal','issn','year','value','source','checked']):self.metric_table.setItem(i,j,QTableWidgetItem(str(r[k])))
    def import_metrics(self):
        file,_=QFileDialog.getOpenFileName(self,'导入有来源的 JIF 表','','CSV (*.csv)')
        if not file:return
        try:
            with open(file,encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
            merged={(r['journal'].casefold(),str(r['year'])):r for r in self.store.metrics()}
            for r in rows:merged[(r['journal'].casefold(),str(r['year']))]=r
            self.store.write_metrics(list(merged.values()));self.load_metric_table();self.refresh();self.status.setText('指标表已合并。导入值按用户提供来源使用；软件不会让模型自动补全 JIF。')
        except Exception as e:self.job_error(safe_error(e))
    def export_metrics(self):
        file,_=QFileDialog.getSaveFileName(self,'导出 JIF 表（可按现有列添加期刊）','journal_metrics.csv','CSV (*.csv)')
        if file:shutil.copy2(self.store.root/'journal_metrics.csv',file)
    def refresh_logs(self):self.logs.setPlainText('\n\n'.join(f'{r["ts"]}  [{r["kind"]}]\n{r["message"]}' for r in self.store.events()))
    def show_normal(self):self.showNormal();self.raise_();self.activateWindow()
    def closeEvent(self,event):
        if self.want_quit:event.accept();return
        if self.tray.isVisible():self.hide();event.ignore();return
        if self.worker and self.worker.isRunning():self.want_quit=True;self.cancel();event.ignore();return
        event.accept();QApplication.quit()
    def quit_app(self):
        self.want_quit=True
        if self.worker and self.worker.isRunning():self.cancel();self.hide()
        else:QApplication.quit()
