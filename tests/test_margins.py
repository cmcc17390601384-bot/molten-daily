import json
from pathlib import Path
import fitz
from core import Store
from margins import MarginAnnotator,export_margins

class FakeMarginAI:
    calls=0
    def json(self,system,payload,max_tokens):
        self.calls+=1
        return {'id':payload['id'],'zh':'整段译文：'+payload['text'],'items':[{'kind':'word','quote':'electrodeposition','ipa':'/test/','zh':'电沉积'},
          {'kind':'phrase','quote':'molten salt','ipa':'','zh':'熔盐'},
          {'kind':'sentence','quote':payload['source'],'ipa':'','zh':'熔盐中的铀电沉积呈现枝晶生长。'},
          {'kind':'word','quote':'inventedword','ipa':'','zh':'不存在'}]}

def test_margin_source_location_cache_and_overflow(tmp_path):
    store=Store(tmp_path/'data');src=tmp_path/'source.pdf';d=fitz.open();p=d.new_page();p.insert_text((40,100),'Uranium electrodeposition in molten salt exhibits dendritic growth.');p.draw_rect(fitz.Rect(40,200,260,300),fill=(.1,.7,.3));d.save(src);d.close()
    client=FakeMarginAI();result=MarginAnnotator(store,client=client).run(src)
    assert result['audit']['categories']['phrase']==1 and result['audit']['categories']['sentence']==1
    assert result['audit']['categories']['paragraph']==1 and result['audit']['items']==4
    MarginAnnotator(store,client=client).run(src);assert client.calls==1
    with fitz.open(src) as a,fitz.open(result['pdf']) as b:
        assert b[0].rect.width==a[0].rect.width+600
        center=fitz.Rect(300,42,300+a[0].rect.width,42+a[0].rect.height)
        assert 'Uranium electrodeposition' in b[0].get_text(clip=center)
        assert '1.01' in b[0].get_text()
    state=json.loads((Path(result['folder'])/'margins.json').read_text('utf-8'))
    item=state['pages']['0'][0];state['pages']['0']=[dict(item,number=f'1.{i:02}',zh='长批注测试，完整保留每一条。'*12) for i in range(40)]
    overflow=export_margins(state,result['folder']);assert overflow['audit']['output_pages']>1
    assert len(overflow['audit']['mapping'])==40
