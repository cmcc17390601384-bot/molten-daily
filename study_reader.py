"""Read existing paid caches and paint hover targets without modifying the PDF."""
import html
import fitz
from PySide6.QtCore import Qt,Signal,QRectF
from PySide6.QtGui import QColor,QPainter,QPen
from PySide6.QtWidgets import QLabel
from translation import scientific_html

COLORS={'word':'#edb940','phrase':'#548fdf','sentence':'#46ad89'}
LABELS={'word':'专业词','phrase':'短语','sentence':'长难句'}

def page_notes(state,page_no):
    """Use results directly, including incomplete sessions and repeated occurrences."""
    from margins import study_parts
    paragraphs=[];notes=[]
    document=state.get('document',{})
    for unit in study_parts(dict(document,units=[u for u in document.get('units',[]) if u['page']==page_no])):
        result=state.get('results',{}).get(unit['id'])
        if not result:continue
        paragraphs.append(dict(unit=unit['id'],rects=[unit['bbox']],zh=result.get('zh',''),kind='paragraph'))
        for item in result.get('items',[]):
            if item.get('rects') and item.get('zh') and item.get('kind') in COLORS:
                notes.append(dict(item,unit=unit['id'],number=str(len(notes)+1)))
    return notes+paragraphs

def panels(entries,hits):
    if not hits:return ('移到原文高亮处查看音标与语境释义。','移到原文段落查看完整中文翻译。')
    active=[entries[i] for i in hits];notes=[n for n in active if n['kind']!='paragraph']
    # One PDF block can have several long-text chunks; show all its translations.
    unit=active[0]['unit'].split('-S')[0]
    translations=[n for n in entries if n['kind']=='paragraph' and n['unit'].split('-S')[0]==unit]
    left=[]
    for n in notes:
        left.append('<p><b>['+html.escape(n.get('number',''))+'] '+LABELS[n['kind']]+'</b></p>'
                    '<p>'+scientific_html(n.get('quote',''))+'</p>'+
                    ('<p style="color:#5b6775">'+html.escape(n['ipa'])+'</p>' if n.get('ipa') else '')+
                    '<p>'+scientific_html(n['zh'])+'</p>')
    return ('<hr>'.join(left) or '本段没有此处的词句批注；右侧显示整段翻译。',
            '<p style="color:#5b6775">'+html.escape(unit)+'</p>'+''.join('<p>'+scientific_html(n['zh'])+'</p>' for n in translations))

class StudyPage(QLabel):
    hovered=Signal(object)
    def __init__(self,parent=None):
        super().__init__(parent);self.setMouseTracking(True);self.entries=[];self.targets=[];self.active=();self.scale=1
    def set_notes(self,entries,page,scale):
        self.entries=entries;self.scale=scale;self.targets=[];self.active=()
        for i,note in enumerate(entries):
            for coords in note.get('rects',[]):
                rect=fitz.Rect(coords)*page.rotation_matrix
                self.targets.append((i,QRectF(rect.x0*scale,rect.y0*scale,rect.width*scale,rect.height*scale)))
        self.update()
    def hit_test(self,point):
        hits={i for i,r in self.targets if r.contains(point)}
        order={'word':0,'phrase':1,'sentence':2,'paragraph':3}
        return tuple(sorted(hits,key=lambda i:(order[self.entries[i]['kind']],sum((r[2]-r[0])*(r[3]-r[1]) for r in self.entries[i]['rects']),i)))
    def mouseMoveEvent(self,event):
        hits=self.hit_test(event.position())
        if hits and hits!=self.active:
            self.active=hits;self.hovered.emit(hits);self.update()
        # Keep the last note readable while moving into either side panel.
        super().mouseMoveEvent(event)
    def paintEvent(self,event):
        painter=QPainter(self)
        painter.fillRect(self.rect(),QColor('white'))
        pixmap=self.pixmap()
        if pixmap and not pixmap.isNull():painter.drawPixmap(self.rect(),pixmap)
        for i,rect in reversed(self.targets):
            kind=self.entries[i]['kind']
            if kind=='paragraph':continue
            color=QColor(COLORS[kind]);color.setAlpha(65 if i in self.active else 36)
            painter.fillRect(rect,color)
            if i in self.active:painter.setPen(QPen(QColor(COLORS[kind]),1));painter.drawRect(rect)
        painter.end()
