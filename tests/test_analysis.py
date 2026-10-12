from pathlib import Path
import fitz,pytest
from core import Store
from analysis_report import import_local,PaperAnalyzer,validate_report
from services import ApiError

def make_pdf(path):
    d=fitz.open();p=d.new_page();p.insert_text((40,80),'Uranium electrodeposition in molten salt shows dendritic growth at 453 K.');d.save(path);d.close()

class FakeAnalysis:
    calls=0
    def json(self,system,payload,max_tokens):
        self.calls+=1;u=payload[0]
        return {'title':'铀沉积研究','sections':{'findings':[{'text':'观察到枝晶生长。','kind':'fact','evidence':[{'unit':u['id'],'quote':u['text']}]},
            {'text':'没有证据的结论','kind':'fact','evidence':[{'unit':u['id'],'quote':'This quotation is invented and not in the paper.'}]}]},'questions':['枝晶形成受什么控制？']}

def test_local_import_deduplicates_without_overwriting_existing(tmp_path):
    store=Store(tmp_path/'data');pdf=tmp_path/'a.pdf';make_pdf(pdf)
    p=import_local(store,pdf);store.set_flag(p['doi'],'favorite',1);p2=import_local(store,pdf)
    assert p['doi']==p2['doi'] and len(store.papers())==1 and p2['favorite']==1
    assert p2['journal']=='本地导入 / 期刊未核实' and Path(p2['pdf']).exists()

def test_analysis_evidence_validation_cache_and_report(tmp_path):
    store=Store(tmp_path/'data');pdf=tmp_path/'a.pdf';make_pdf(pdf);client=FakeAnalysis()
    r=PaperAnalyzer(store,client=client).run(pdf);assert r['state']['report']['rejected_claims']==1
    assert len(r['state']['report']['sections']['findings'])==1 and '第 1 页' in Path(r['markdown']).read_text('utf-8')
    PaperAnalyzer(store,client=client).run(pdf);assert client.calls==1
    with pytest.raises(ApiError):validate_report({'sections':{}},[])
