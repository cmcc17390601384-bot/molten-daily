"""Isolated query runs, paged recall and evidence-backed precision filtering."""
from datetime import date,datetime,timedelta
from urllib.parse import urlencode
from topics import custom_topic,current_assessment
from core import safe_error,unprotect

def search(retriever,on_demand=False):
    from services import DeepSeek,request_json,check_cancel,Cancelled,ApiError,cr_paper,oa_paper,eligibility,topic_score
    store=retriever.store;cancel=retriever.cancel;progress=retriever.progress;s=store.settings()
    store.save_search_results(s,[],state='running')
    store.stage_search_papers([])
    start=(date.today()-timedelta(days=s['days'])).isoformat();end=date.today().isoformat()
    if not s['queries']:
        if not s.get('api_secret'):raise ApiError('请在找文献页填写英文检索词，或配置 API 后自动生成。')
        progress('正在生成保留核心研究对象与问题的英文检索词…')
        s['queries']=DeepSeek(store,s,cancel).plan_queries();store.update_settings(queries=s['queries'])
        store.save_search_results(s,[],state='running')
    merged={};errors=[];success=0;disabled=set();limit=max(1,min(10,int(s.get('search_pages',3))));page_size=100;truncated=False
    for index,query in enumerate(s['queries'][:6]):
        for source,converter in [('Crossref',cr_paper),('OpenAlex',oa_paper)]:
            if source in disabled:continue
            cursor='*';seen_cursors=set();seen_dois=set()
            for page in range(limit):
                check_cancel(cancel);progress(f'{source} · 检索词 {index+1}/{len(s["queries"])} · 第 {page+1}/{limit} 页 · 已找到 {len(merged)} 条题录')
                if source=='Crossref':
                    params=dict(query=query,filter=f'from-pub-date:{start},until-pub-date:{end},type:journal-article',rows=page_size,sort='relevance',cursor=cursor)
                    if s.get('email'):params['mailto']=s['email']
                    url='https://api.crossref.org/works?'+urlencode(params)
                else:
                    params={'search':query,'filter':f'from_publication_date:{start},to_publication_date:{end},type:article','per_page':page_size,'cursor':cursor}
                    if s.get('openalex_secret'):params['api_key']=unprotect(s['openalex_secret'])
                    url='https://api.openalex.org/works?'+urlencode(params)
                try:
                    response=request_json(url,cancel=cancel,timeout=35)
                    if source=='Crossref':
                        items=response.get('message',{}).get('items');next_cursor=response.get('message',{}).get('next-cursor')
                        if response.get('status')!='ok':raise ApiError('Crossref 数据结构异常')
                    else:items=response.get('results');next_cursor=response.get('meta',{}).get('next_cursor')
                    if not isinstance(items,list):raise ApiError(source+' 未返回有效题录列表')
                    success+=1;fresh=0
                    for item in items:
                        if not item.get('DOI' if source=='Crossref' else 'doi'):continue
                        paper=converter(item)
                        if paper['doi'] not in seen_dois:fresh+=1;seen_dois.add(paper['doi'])
                        retriever._merge(merged,paper)
                    if len(items)<page_size or not next_cursor or next_cursor in seen_cursors or not fresh:break
                    if page+1==limit:truncated=True
                    seen_cursors.add(cursor);cursor=next_cursor
                except Cancelled:raise
                except Exception as e:
                    errors.append(source+'：'+safe_error(e));disabled.add(source);break
    if not success:raise ApiError('所有检索来源都失败，不能判定没有新文献。\n'+'\n'.join(errors))
    # Broad queries can fill every page with unverified journals. Supplement
    # recall within a small, allowlisted set without relaxing either threshold.
    targeted=0
    if len(merged)>=100 and s.get('ai_ranking') and s.get('api_secret') and sum(bool((m:=store.metric(p,s['metric_year'])) and float(m['value'])>s['min_if']) for p in merged.values())<10:
        from journal_metrics import journal_key
        allowed={}
        for row in store.metrics():
            metric=store.metric({'journal':row['journal']},s['metric_year'])
            if metric and float(metric['value'])>s['min_if']:allowed[journal_key(row['journal'])]=row['journal']
        try:
            progress('常规结果中的达标期刊较少，正在选择相关期刊补充检索…')
            from topics import research_topic
            plan=DeepSeek(store,s,cancel).json('从给定期刊名单中选最多3本与研究方向最相关、可能刊登该方向研究的期刊。优先专业期刊。只返回 {"journals":["名单中的完整期刊名"]}。不能补造期刊、论文或影响因子。',{'direction':research_topic(s),'journals':list(allowed.values())},700)
            names=list(dict.fromkeys(allowed[journal_key(n)] for n in plan.get('journals',[]) if isinstance(n,str) and journal_key(n) in allowed))[:3]
            for name in names:
                for query in s['queries'][:2]:
                    cursor='*';seen=set()
                    for page in range(limit):
                        check_cancel(cancel);progress(f'补充检索 · {name} · 第 {page+1}/{limit} 页')
                        params={'query':query,'query.container-title':name,'filter':f'from-pub-date:{start},until-pub-date:{end},type:journal-article','rows':page_size,'sort':'relevance','cursor':cursor}
                        if s.get('email'):params['mailto']=s['email']
                        response=request_json('https://api.crossref.org/works?'+urlencode(params),cancel=cancel,timeout=35)
                        data=response.get('message',{});items=data.get('items')
                        if response.get('status')!='ok' or not isinstance(items,list):raise ApiError('期刊补充检索返回无效数据')
                        success+=1
                        for item in items:
                            if not item.get('DOI'):continue
                            paper=cr_paper(item)
                            if journal_key(paper['journal'])==journal_key(name):
                                targeted+=paper['doi'] not in merged;retriever._merge(merged,paper)
                        next_cursor=data.get('next-cursor')
                        if len(items)<page_size or not next_cursor or next_cursor in seen:break
                        if page+1==limit:truncated=True
                        seen.add(cursor);cursor=next_cursor
        except Cancelled:raise
        except Exception as e:errors.append('期刊补充检索未完成：'+safe_error(e))
    candidates=[];missing_metrics=set();unknown=0;below=0;excluded=0
    for paper in merged.values():
        if not start<=paper.get('date','')<=end or paper.get('retracted'):excluded+=1;continue
        metric=store.metric(paper,s['metric_year'])
        if not metric:
            unknown+=1;missing_metrics.add(paper.get('journal','未知期刊'));continue
        if float(metric['value'])<=s['min_if']:below+=1;continue
        if not eligibility(paper,store,s,check_topic=False)[0]:excluded+=1;continue
        prior=store.paper(paper['doi'],include_deleted=True)
        if prior and prior.get('deleted'):continue
        if prior and not paper.get('abstract') and prior.get('abstract'):paper['abstract']=prior['abstract']
        # Scoped evidence assessments survive retries, including rejected candidates.
        assessment=store.cached_assessment(paper,s)
        if assessment:paper['ai']=assessment
        candidates.append(paper)
    candidates.sort(key=lambda p:(topic_score(p,s)[0],p.get('date','')),reverse=True)
    if s.get('ai_ranking') and s.get('api_secret'):
        pending=[p for p in candidates if not current_assessment(p,s)]
        for offset in range(0,len(pending),8):
            check_cancel(cancel);batch=pending[offset:offset+8]
            progress(f'严格核对主题证据 {offset+1}–{offset+len(batch)}/{len(pending)} 篇；相同证据的已评估结果直接复用')
            try:
                analyses=DeepSeek(store,s,cancel).assess(batch)
                for paper in batch:
                    if paper['doi'] in analyses:
                        paper['ai']=analyses[paper['doi']];store.cache_assessment(paper,paper['ai'],s)
            except Cancelled:raise
            except Exception as e:
                errors.append('主题核验未完成，缺少证据的论文不显示；已评估内容保留：'+safe_error(e));break
    selected=[];new=0;topic_rejected=0;unassessed=0
    for paper in candidates:
        check_cancel(cancel)
        if not eligibility(paper,store,s)[0]:
            if s.get('ai_ranking') and s.get('api_secret') and not current_assessment(paper,s):unassessed+=1
            else:topic_rejected+=1
            continue
        if not paper.get('seed'):
            new+=not bool(store.paper(paper['doi']));selected.append(paper)
    selected.sort(key=lambda p:(eligibility(p,store,s)[1],p.get('date','')),reverse=True)
    store.stage_search_papers(selected)
    stats=dict(fetched=len(merged),relevant=len(selected),new=new,errors=list(dict.fromkeys(errors)),success_sources=success,
               targeted_added=targeted,
               unknown_jif=unknown,below_jif=below,other_excluded=excluded,topic_rejected=topic_rejected,unassessed=unassessed,
               missing_journals=sorted(missing_metrics),truncated=truncated,state='complete')
    store.save_search_results(s,[p['doi'] for p in selected],**stats)
    store.update_settings(last_success=datetime.now().isoformat(timespec='seconds'))
    import json
    store.event('检索',json.dumps(stats,ensure_ascii=False));return dict(stats,selected=selected)
