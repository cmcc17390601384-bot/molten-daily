"""Explicitly dated publisher JIF data; no inference from CiteScore or paper year."""
import html,re,unicodedata
from datetime import date

NATURE_URL='https://www.nature.com/nature-portfolio/about-journals/journal-metrics'

def journal_key(name):
    return re.sub(r'[^\w]+','',unicodedata.normalize('NFKC',name).casefold().replace('&','and'))

def parse_nature_metrics(markup,checked=None):
    markup=html.unescape(markup)
    heading=re.search(r'<h2\b[^>]*id=["\']citation-impact["\'][^>]*>\s*(20\d{2})\s+Journal Metrics\s*</h2>',markup,re.I)
    if not heading:raise ValueError('出版社页面未明确标注 JIF 年份，未更新。')
    year=int(heading[1])
    if not 2020<=year<date.today().year:raise ValueError('出版社指标年份不合理，未更新。')
    rows=[]
    def cells(row):
        return [re.sub(r'\s+',' ',re.sub('<[^>]+>',' ',c)).strip() for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>',row,re.S|re.I)]
    for table in re.findall(r'<table\b[^>]*>(.*?)</table>',markup[heading.end():],re.S|re.I):
        trs=re.findall(r'<tr\b[^>]*>(.*?)</tr>',table,re.S|re.I)
        if not trs:continue
        headers=cells(trs[0])
        if 'Journal Impact Factor' not in headers:continue
        column=headers.index('Journal Impact Factor')
        for tr in trs[1:]:
            values=cells(tr)
            if len(values)<=column or not re.fullmatch(r'\d+(?:\.\d+)?',values[column]):continue
            number=float(values[column])
            if not 0<number<300:continue
            issn='2041-1723' if values[0]=='Nature Communications' else ''
            rows.append(dict(journal=values[0],issn=issn,year=year,value=number,source=NATURE_URL,checked=checked or date.today().isoformat()))
    if not rows:raise ValueError('出版社 JIF 表格结构改变，未更新。')
    return rows

def update_publisher_metrics(store,cancel=None,progress=lambda _:None):
    import requests
    from services import check_cancel
    progress('核对出版社公开 JIF 年份与指标…');check_cancel(cancel)
    response=requests.get(NATURE_URL,timeout=(10,30));response.raise_for_status();check_cancel(cancel)
    rows=parse_nature_metrics(response.text);added=store.merge_metrics(rows)
    return dict(rows=len(rows),added=added,year=max(r['year'] for r in rows))
