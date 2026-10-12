"""Research preferences shared by retrieval, ranking and evidence reports."""
import hashlib
import json
import re

DEFAULT_TOPIC = '熔盐电化学，优先核能、核燃料循环和锕系元素分离'


def custom_topic(settings):
    return settings.get('topic_mode', 'molten') == 'custom'


def research_topic(settings):
    return settings.get('research_topic', '').strip() if custom_topic(settings) else DEFAULT_TOPIC


def topic_key(settings):
    fields = [settings.get('topic_mode', 'molten'), research_topic(settings),
              settings.get('queries', []), settings.get('nuclear_only', False) if not custom_topic(settings) else False]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()[:20]


def current_assessment(paper, settings):
    assessment = paper.get('ai', {})
    # Old unscoped assessments must not constrain a newly selected direction.
    return assessment if assessment.get('topic_key') == topic_key(settings) else {}


def query_lines(value):
    if not isinstance(value, list) or not value or len(value) > 6:
        raise ValueError('请输入 1–6 组检索词，每行一组。')
    if any(not isinstance(q, str) or not q.strip() or len(q.strip()) > 240 for q in value):
        raise ValueError('每组检索词需为 1–240 个字符。')
    return list(dict.fromkeys(q.strip() for q in value))


def lexical_match(paper, settings):
    """Conservative fallback: any query group must match all its meaningful words."""
    text = (paper.get('title', '') + ' ' + paper.get('abstract', '')).casefold()
    stop = {'and', 'or', 'the', 'of', 'in', 'for', 'on', 'a', 'an', 'with', 'to', 'by'}
    best = 0.0
    for query in settings.get('queries', []):
        words = [w for w in re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+', query.casefold()) if w not in stop]
        if not words:
            continue
        hits = sum(bool(re.search(r'(?<![a-z0-9])' + re.escape(w) + r'(?![a-z0-9])', text))
                   if re.fullmatch(r'[a-z0-9]+', w) else w in text for w in words)
        best = max(best, hits / len(words))
    return round(80 * best), best == 1.0, False
