"""Library, attachment and citation actions for the desktop window."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout,QLineEdit,QComboBox,QAbstractItemView,QTableWidgetItem,QHeaderView,QFileDialog,QInputDialog,QApplication,QMenu,QDialog,QFormLayout,QDialogButtonBox,QVBoxLayout,QLabel)
from core import safe_error
from library import import_pdf,link_pdf,reconcile
from citations import export_records,read_records,local_attachment
from services import Retriever

class LibraryUI:
    def tab_changed(self,index):
        if index==0:self.select_paper()
        elif index==1:self.select_library()
        elif index==3 and self.profile_url not in self.models_seen:self.model_timer.start(300)
        self.update_actions()
    def update_actions(self):
        if not hasattr(self,'export_btn'):return
        p=self.store.paper(self.selected) if self.selected else None
        busy=bool(self.worker and self.worker.isRunning());single=bool(p) and not busy
        pdf=single and bool(p.get('pdf')) and Path(p['pdf']).is_file()
        for name in ('search_read','library_read'):
            b=getattr(self,name);b.setEnabled(bool(pdf));b.setToolTip('打开已关联 PDF' if pdf else '请先选择一篇论文并获取或关联本地 PDF')
        for name in ('search_attach','search_classify','library_attach','library_edit'):getattr(self,name).setEnabled(single)
        can_get=single and (bool(p.get('pdf_url')) or p['doi'].startswith('10.')) and not pdf
        for name in ('search_get','library_get'):getattr(self,name).setEnabled(bool(can_get))
        self.search_doi.setEnabled(single and p.get('url','').startswith('https://'))
        ids=self.library_ids();self.library_classify.setEnabled(bool(ids) and not busy)
        for action,one in getattr(self,'selection_actions',[]):action.setEnabled((len(ids)==1 if one else bool(ids)) and not busy)
        self.generate_btn.setEnabled(bool(self.pdf_path) and Path(self.pdf_path).is_file() and not busy)
        self.export_btn.setEnabled(bool(self.margin_output) and Path(self.margin_output).is_file() and not busy)
    def show_library(self):
        doi=self.selected
        if self.tabs.currentIndex()==2 and self.pdf_path:
            doi=next((p['doi'] for p in self.store.papers() if p.get('pdf') and Path(p['pdf']).resolve()==Path(self.pdf_path).resolve()),doi)
        self.category.setCurrentIndex(0);self.library_filter.clear();self.tabs.setCurrentIndex(1);self.refresh_library()
        for i in range(self.library_table.rowCount()):
            if self.library_table.item(i,0).data(Qt.UserRole)==doi:self.library_table.selectRow(i);self.library_table.scrollToItem(self.library_table.item(i,0));break
    def edit_record(self):
        if not self.selected:return
        from citations import authors
        from library import edit_metadata
        p=self.store.paper(self.selected);dialog=QDialog(self);dialog.setWindowTitle('编辑文献资料');dialog.resize(610,480);form=QFormLayout(dialog);fields={}
        for key,title in [('title','标题'),('authors','作者（用 ; 分隔；可写 姓, 名）'),('date','年份'),('journal','期刊'),('doi','DOI（未知可留空）'),('volume','卷'),('issue','期'),('page_range','原文页码范围'),('article_number','文章编号')]:
            value='; '.join(authors(p)) if key=='authors' else (p.get('date','')[:4] if key=='date' else (p['doi'] if p['doi'].startswith('10.') else '') if key=='doi' else p.get(key,''))
            fields[key]=QLineEdit(value);form.addRow(title,fields[key])
        error=QLabel('手工信息仅用于管理和引用；JIF 必须由指标表匹配。');error.setWordWrap(True);form.addRow(error)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);form.addRow(buttons);buttons.rejected.connect(dialog.reject)
        def save():
            try:self.selected=edit_metadata(self.store,p['doi'],{k:v.text() for k,v in fields.items()});dialog.accept()
            except Exception as e:error.setText(safe_error(e))
        buttons.accepted.connect(save)
        if dialog.exec()==QDialog.Accepted:
            doi=self.selected;self.refresh();self.selected=doi;self.show_library();self.status.setText('文献资料已保存。')
    def make_library(self):
        from ui import button,label,table
        v=self.page('图书馆');row=QHBoxLayout();self.category=QComboBox();row.addWidget(self.category)
        self.category.currentIndexChanged.connect(self.refresh_library)
        for text,fn in [('新建分类',self.new_category),('重命名',self.rename_category),('删除分类',self.delete_category)]:row.addWidget(button(text,fn))
        self.library_filter=QLineEdit();self.library_filter.setPlaceholderText('筛选标题、DOI、期刊');self.library_filter.textChanged.connect(self.refresh_library);row.addWidget(self.library_filter,1);v.addLayout(row)
        self.library_table=table(['标题','期刊','年份','PDF','分类','阅读']);self.library_table.setSelectionMode(QAbstractItemView.ExtendedSelection);self.library_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.library_table.itemSelectionChanged.connect(self.select_library);v.addWidget(self.library_table,1)
        row=QHBoxLayout()
        self.library_read=button('阅读 PDF',self.open_reader_selected,True);self.library_get=button('获取开放 PDF',self.get_pdf);self.library_attach=button('关联 PDF',self.attach_pdf);self.library_edit=button('编辑题录',self.edit_record);self.library_classify=button('加入分类',self.classify)
        for widget in (self.library_read,self.library_get,self.library_attach,self.library_edit,self.library_classify):row.addWidget(widget)
        row.addStretch();row.addWidget(button('导入 PDF',self.batch_import));v.addLayout(row)
        row=QHBoxLayout();self.selection_actions=[]
        for title,actions in [ ('题录与整理',[('DOI 补全 / 合并',self.complete_doi,True),('移出当前分类',self.unclassify,False),('收藏 / 取消',lambda:self.mark_library('favorite'),False),('标记已读',lambda:self.mark_library('seen'),False)]),
                               ('导入 / 导出',[('导入 RIS / EndNote XML',self.import_citations,None),('导出所选 / 当前分类题录',self.export_citations,None)])]:

            control=button(title,lambda:None);menu=QMenu(control)
            for text,fn,single in actions:
                action=menu.addAction(text);action.triggered.connect(fn)
                if single is not None:self.selection_actions.append((action,single))
            control.setMenu(menu);row.addWidget(control)
        self.library_count=label('','muted');row.addWidget(self.library_count,1);v.addLayout(row)
        self.library_table.cellDoubleClicked.connect(lambda *_:self.open_reader_selected())
        self.refresh_categories()
    def refresh_categories(self):
        current=self.category.currentData();self.category.blockSignals(True);self.category.clear()
        for name,key in [('全部文献','all'),('未分类','none'),('收藏','favorite'),('未读','unread')]:self.category.addItem(name,key)
        for c in self.store.collections():self.category.addItem(f'{c["name"]} ({c["count"]})',c['id'])
        idx=self.category.findData(current);self.category.setCurrentIndex(max(0,idx));self.category.blockSignals(False);self.refresh_library()
    def refresh_library(self,*_):
        if not hasattr(self,'library_table'):return
        selected=set(self.library_ids());key=self.category.currentData();q=self.library_filter.text().strip().casefold();members=self.store.memberships();names={c['id']:c['name'] for c in self.store.collections()}
        rows=[]
        for p in self.store.papers():
            cats=members.get(p['doi'],set())
            if key=='none' and cats or key=='favorite' and not p['favorite'] or key=='unread' and p['seen'] or isinstance(key,int) and key not in cats:continue
            if q not in (p['title']+' '+p['doi']+' '+p.get('journal','')).casefold():continue
            rows.append(p)
        self.library_table.blockSignals(True);self.library_table.setRowCount(len(rows))
        for i,p in enumerate(rows):
            for j,value in enumerate([p['title'],p.get('journal',''),p.get('date','')[:4],'已关联' if p.get('pdf') and Path(p['pdf']).exists() else '未关联','、'.join(names[c] for c in members.get(p['doi'],set()) if c in names),'已读' if p['seen'] else '未读']):
                item=QTableWidgetItem(value);item.setData(Qt.UserRole,p['doi']);self.library_table.setItem(i,j,item)
                if p['doi'] in selected:item.setSelected(True)
        self.library_table.blockSignals(False)
        self.library_count.setText(f"{len(rows)} 篇 · 支持 Ctrl / Shift 多选")
        if self.tabs.currentIndex()==1:self.select_library()
    def library_ids(self):
        if not hasattr(self,'library_table'):return []
        return [self.library_table.item(x.row(),0).data(Qt.UserRole) for x in self.library_table.selectionModel().selectedRows()]
    def selected_ids(self):return self.library_ids() if self.tabs.currentIndex()==1 else ([self.selected] if self.selected else [])
    def select_library(self):
        ids=self.library_ids();self.selected=ids[0] if len(ids)==1 else None;self.update_actions()
    def new_category(self):
        name,ok=QInputDialog.getText(self,'新建分类','分类名称')
        if ok:
            try:cid=self.store.save_collection(name);self.refresh_categories();self.category.setCurrentIndex(self.category.findData(cid))
            except Exception as e:self.job_error(safe_error(e))
    def rename_category(self):
        cid=self.category.currentData()
        if not isinstance(cid,int):self.status.setText('请选择自建分类。');return
        name,ok=QInputDialog.getText(self,'重命名分类','新名称')
        if ok:
            try:self.store.save_collection(name,cid);self.refresh_categories()
            except Exception as e:self.job_error(safe_error(e))
    def delete_category(self):
        cid=self.category.currentData()
        if isinstance(cid,int):self.store.delete_collection(cid);self.refresh_categories();self.status.setText('分类已删除；文献、附件和精读记录均保留。')
    def classify(self):
        ids=self.selected_ids();cats=self.store.collections()
        if not ids:self.status.setText('请先选择文献。');return
        if not cats:self.new_category();cats=self.store.collections()
        if not cats:return
        name,ok=QInputDialog.getItem(self,'加入分类','分类',[c['name'] for c in cats],0,False)
        if ok:self.store.assign_collection(ids,next(c['id'] for c in cats if c['name']==name));self.refresh_categories();self.status.setText(f'已将 {len(ids)} 篇文献归类。')
    def unclassify(self):
        cid=self.category.currentData()
        if isinstance(cid,int):self.store.assign_collection(self.library_ids(),cid,remove=True);self.refresh_categories()
    def mark_library(self,key):
        for doi in self.library_ids():self.store.set_flag(doi,key,1 if key=='seen' else 1-self.store.paper(doi)[key])
        self.refresh_categories()
    def batch_import(self):
        paths,_=QFileDialog.getOpenFileNames(self,'批量导入 PDF','','PDF (*.pdf)')
        if not paths:return
        cid=self.category.currentData()
        def job(cancel,progress):
            done=[];errors=[]
            for path in paths:
                if cancel.is_set():break
                try:
                    p=import_pdf(self.store,path);done.append(p['doi'])
                    if isinstance(cid,int):self.store.assign_collection([p['doi']],cid)
                except Exception as e:errors.append(Path(path).name+'：'+safe_error(e))
                progress(f'导入 {len(done)} / {len(paths)}')
            return done,errors
        def finish(result):
            self.refresh()
            if result[0]:
                for i in range(self.library_table.rowCount()):
                    if self.library_table.item(i,0).data(Qt.UserRole)==result[0][0]:self.library_table.selectRow(i);break
            self.status.setText(f'已导入 / 识别重复 {len(result[0])} 篇。'+('；'.join(result[1]) if result[1] else '可直接阅读，或点击“编辑题录”自定义资料。'))
        self.run_job('导入 PDF…',job,finish)
    def attach_pdf(self):
        doi=self.selected
        if not doi:self.status.setText('请选择一篇题录，再关联对应 PDF。');return
        path,_=QFileDialog.getOpenFileName(self,'关联所选文献的 PDF','','PDF (*.pdf)')
        if path:
            self.run_job('关联 PDF…',lambda c,p:link_pdf(self.store,doi,path),lambda p:(self.refresh(),self.status.setText('PDF 已关联；独立导入的同一文件已合并。')))
    def complete_doi(self):
        old=self.selected
        if not old:self.status.setText('请选择一篇需要补全的文献。');return
        p=self.store.paper(old);candidate=old if old.startswith('10.') else next(iter(p.get('doi_candidates',[])),'')
        doi,ok=QInputDialog.getText(self,'核对 DOI 并补全','请输入这篇论文的 DOI；不同版本请勿合并。',text=candidate)
        if not ok or not doi.strip():return
        def job(c,pr):return Retriever(self.store,c,pr).by_doi(doi.strip())
        def finish(verified):
            # Identity changes require matching user-confirmed DOI; preserve existing attachments and categories.
            result=reconcile(self.store,old,verified);self.selected=result['doi'];self.refresh();self.status.setText('题录已按 DOI 补全，分类与 PDF 已保留。')
        self.run_job('查询 DOI 题录…',job,finish)
    def import_citations(self):
        path,_=QFileDialog.getOpenFileName(self,'导入 EndNote 导出的 XML 或 RIS','','题录 (*.ris *.xml)')
        if not path:return
        cid=self.category.currentData()
        def job(c,pr):
            records=read_records(path)
            if not records:raise ValueError('文件中没有可识别的题录，请从 EndNote 导出 XML 或 RIS。')
            linked=0;missing=0;processed=0
            for p in records:
                if c.is_set():break
                self.store.put(p)
                processed+=1
                if isinstance(cid,int):self.store.assign_collection([p['doi']],cid)
                for value in p.get('import_attachments',[]):
                    attachment=local_attachment(value,path)
                    if attachment:
                        try:link_pdf(self.store,p['doi'],attachment);linked+=1
                        except Exception:missing+=1
                    else:missing+=1
            return processed,linked,missing
        self.run_job('导入题录…',job,lambda r:(self.refresh(),self.status.setText(f'读取 {r[0]} 条题录，关联 {r[1]} 个附件，{r[2]} 个附件需手动关联。')))
    def export_selection(self):
        ids=self.selected_ids()
        if not ids and self.tabs.currentIndex()==1:ids=[self.library_table.item(i,0).data(Qt.UserRole) for i in range(self.library_table.rowCount())]
        return [self.store.paper(doi) for doi in ids if self.store.paper(doi)]
    def export_citations(self):
        records=self.export_selection()
        if not records:self.status.setText('请先选择文献，或打开需要导出的分类。');return
        path,kind=QFileDialog.getSaveFileName(self,'导出所选文献 / 当前分类','references.ris','RIS (*.ris);;EndNote Tagged (*.enw)')
        if path:
            try:
                if not Path(path).suffix:path+='.enw' if 'enw' in kind else '.ris'
                export_records(records,path);self.status.setText(f'已导出 {len(records)} 篇题录。缺失字段写入备注，附件为本机路径。')
            except Exception as e:self.job_error(safe_error(e))
