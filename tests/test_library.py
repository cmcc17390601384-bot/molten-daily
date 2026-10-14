from datetime import date
import json
import xml.etree.ElementTree as ET
import pytest
from pathlib import Path
from core import Store,SEEDS
from library import import_pdf,link_pdf,reconcile
from citations import export_records,read_records,local_attachment
from services import cr_paper,eligibility
from test_core import sample_pdf

def paper():return dict(SEEDS[0],date=date.today().isoformat(),author_list=[{'family':'Zhang','given':'San'},{'family':'O’Brien','given':'A & B'}],volume='12',issue='2',page_range='10-15',keywords=['核能','熔盐'])

def test_category_lifecycle_and_merge(tmp_path):
    s=Store(tmp_path);s.put(paper());p=dict(paper(),doi='local:test');s.put(p)
    a=s.save_collection('核能');b=s.save_collection('电化学');s.assign_collection([p['doi']],a);s.assign_collection([SEEDS[0]['doi']],b)
    s.set_flag(p['doi'],'favorite',1);s.set_flag(p['doi'],'note','学习笔记');s.merge_papers(p['doi'],SEEDS[0]['doi'])
    assert len(s.papers())==1 and s.paper(SEEDS[0]['doi'])['note']=='学习笔记'
    assert s.memberships()[SEEDS[0]['doi']]=={a,b}
    assert s.paper(SEEDS[0]['doi'])['favorite']==1
    s.save_collection('燃料循环',a);s.delete_collection(a)
    assert len(s.papers())==1 and s.memberships()[SEEDS[0]['doi']]=={b}
    assert Store(tmp_path).collections()[0]['count']==1

def test_duplicate_category_rejected(tmp_path):
    s=Store(tmp_path);s.save_collection('Chemistry')
    with pytest.raises(ValueError):s.save_collection(' chemistry ')

def test_pdf_link_and_reimport_dedup(tmp_path):
    s=Store(tmp_path/'data');pdf=tmp_path/'source.pdf';sample_pdf(pdf)
    local=import_pdf(s,pdf);cid=s.save_collection('导入');s.assign_collection([local['doi']],cid)
    s.put(paper());link_pdf(s,SEEDS[0]['doi'],pdf)
    assert len(s.papers())==1 and s.memberships()[SEEDS[0]['doi']]=={cid}
    assert import_pdf(s,pdf)['doi']==SEEDS[0]['doi'] and len(s.papers())==1
    assert Path(s.papers()[0]['pdf']).read_bytes()==pdf.read_bytes()

def test_doi_reconcile_retains_pdf_identity(tmp_path):
    s=Store(tmp_path/'data');pdf=tmp_path/'source.pdf';sample_pdf(pdf);local=import_pdf(s,pdf)
    p=reconcile(s,local['doi'],paper());assert p['pdf_sha256']==local['pdf_sha256']
    assert import_pdf(s,pdf)['doi']==p['doi'] and len(s.papers())==1

def test_ris_roundtrip_and_safe_line_fields(tmp_path):
    p=paper();p['title']='Science & <salt>\nDO  - malicious';path=tmp_path/'export.ris';export_records([p],path);r=read_records(path)[0]
    assert r['doi']==p['doi'] and len(r['author_list'])==2 and r['page_range']=='10-15' and r['keywords']==p['keywords']
    assert r['author_list'][1]['given']=='A & B'
    assert '\nDO  - malicious' not in path.read_text('utf-8')

def test_endnote_tagged_format(tmp_path):
    path=tmp_path/'test.enw';export_records([paper()],path);s=path.read_text('utf-8')
    assert '%0 Journal Article' in s and '%A Zhang, San' in s and '%R '+SEEDS[0]['doi'] in s

def test_xml_import_unicode_and_entities(tmp_path):
    path=tmp_path/'refs.xml';path.write_text('<xml><records><record><titles><title><style>盐 &amp; Metal</style></title><secondary-title>Journal</secondary-title></titles><dates><year>2024</year></dates><contributors><authors><author>Zhang, San</author></authors></contributors><electronic-resource-num>10.1234/ABC</electronic-resource-num></record></records></xml>','utf-8')
    p=read_records(path)[0];assert p['title']=='盐 & Metal' and p['doi']=='10.1234/abc' and p['date']=='2024'
    path.write_text('<!DOCTYPE a><xml/>','utf-8')
    with pytest.raises(ValueError):read_records(path)

def test_attachment_traversal_rejected(tmp_path):
    assert local_attachment('internal-pdf://../../other.pdf',tmp_path/'ref.xml') is None
    assert local_attachment('https://example.com/test.pdf',tmp_path/'ref.xml') is None

def test_full_author_list_not_truncated():
    p=cr_paper(dict(DOI='10.1234/a',author=[dict(family=f'Name{i}',given='A') for i in range(30)]))
    assert len(p['author_list'])==30

def test_metric_conflicting_issn_and_conflicting_values_fail_closed(tmp_path):
    s=Store(tmp_path);p=paper();assert eligibility(p,s)[0]
    assert not eligibility(dict(p,issn=['9999-9999']),s)[0]
    ms=s.metrics();ms.append(dict(ms[0],value='20'));s.write_metrics(ms)
    assert s.metric(p,2024) is None

def test_metric_missing_source_or_date_never_qualifies(tmp_path):
    s=Store(tmp_path);s.update_settings(metric_year=2024);ms=s.metrics();ms[0]['checked']='';s.write_metrics(ms)
    assert not eligibility(paper(),s)[0]

def test_ui_strict_jif_and_category_filters(tmp_path):
    from PySide6.QtWidgets import QApplication
    from ui import Window
    app=QApplication.instance() or QApplication([]);s=Store(tmp_path);s.put(paper());s.put(dict(paper(),doi='10.1234/unknown',issn=[],journal='Unknown'))
    s.update_settings(metric_year=2024);s.save_search_results(s.settings(),[p['doi'] for p in s.papers()],state='complete')
    w=Window(s,smoke=True)
    assert w.tabs.widget(0).isAncestorOf(w.queries) and not w.tabs.widget(3).isAncestorOf(w.queries)
    assert w.lib_table.rowCount()==1 and w.library_table.rowCount()==2
    w.minif.setValue(15.7);assert w.lib_table.rowCount()==0
    w.minif.setValue(10);w.year.setValue(2025);assert w.lib_table.rowCount()==0
    w.year.setValue(2024);assert w.lib_table.rowCount()==1
    cid=s.save_collection('燃料循环');s.assign_collection([SEEDS[0]['doi']],cid);w.refresh_categories();w.category.setCurrentIndex(w.category.findData(cid))
    assert w.library_table.rowCount()==1
    w.close();w.deleteLater();app.processEvents()

def test_reader_actions_and_navigation(tmp_path):
    from PySide6.QtWidgets import QApplication
    from ui import Window
    app=QApplication.instance() or QApplication([]);s=Store(tmp_path/'data');s.put(paper());s.put(dict(paper(),doi='10.1234/second'))
    s.save_search_results(s.settings(),[p['doi'] for p in s.papers()],state='complete')
    w=Window(s,smoke=True);w.show();app.processEvents()
    assert not w.search_read.isEnabled() and not w.generate_btn.isEnabled()
    w.lib_table.selectRow(0);app.processEvents();doi=w.selected
    assert not w.search_read.isEnabled() and w.search_attach.isEnabled() and w.search_get.isEnabled()
    pdf=tmp_path/'test.pdf';sample_pdf(pdf);link_pdf(s,doi,pdf);w.refresh();assert w.selected==doi
    assert w.search_read.isEnabled() and not w.search_get.isEnabled()
    w.open_reader_selected();assert w.tabs.currentIndex()==2 and w.generate_btn.isEnabled()
    w.show_library();assert w.tabs.currentIndex()==1 and w.selected==doi and w.library_read.isEnabled()
    w.library_table.selectAll();assert w.selected is None and not w.library_read.isEnabled() and not w.library_edit.isEnabled()
    w.tabs.setCurrentIndex(0);assert w.selected==doi and w.search_read.isEnabled()
    w.source_doc.close();w.source_doc=None;Path(s.paper(doi)['pdf']).unlink();w.update_actions();assert not w.search_read.isEnabled()
    w.filter.setText('nothing');assert not w.search_attach.isEnabled() and w.selected is None
    w.close();w.deleteLater();app.processEvents()

def test_edit_metadata_retains_flags_and_clears_changed_journal_issn(tmp_path):
    from library import edit_metadata
    s=Store(tmp_path);p=paper();s.put(p);s.set_flag(p['doi'],'favorite',1)
    values=dict(title='自定义标题',authors='Li, Ming; Organization',date='2025',journal='Custom Journal',doi=p['doi'],volume='',issue='',page_range='',article_number='')
    edit_metadata(s,p['doi'],values);r=s.paper(p['doi'])
    assert r['title']=='自定义标题' and r['favorite']==1 and r['issn']==[] and r['volume']==''
    assert r['author_list']==[dict(family='Li',given='Ming'),dict(name='Organization')]
    assert s.metric(r,2024) is None
    with pytest.raises(ValueError):edit_metadata(s,p['doi'],dict(values,title=''))
    with pytest.raises(ValueError):edit_metadata(s,p['doi'],dict(values,doi='invalid'))

def test_edit_local_doi_preserves_categories(tmp_path):
    from library import edit_metadata
    s=Store(tmp_path/'data');pdf=tmp_path/'paper.pdf';sample_pdf(pdf);p=import_pdf(s,pdf);cid=s.save_collection('自定义');s.assign_collection([p['doi']],cid)
    doi=edit_metadata(s,p['doi'],dict(title='自定义论文',authors='Zhang, San',date='2025',journal='Unknown',doi='10.1234/local'))
    assert doi=='10.1234/local' and len(s.papers())==1 and s.memberships()[doi]=={cid}
    assert Path(s.paper(doi)['pdf']).exists()
