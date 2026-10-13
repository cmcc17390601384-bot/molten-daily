"""Isolate MuPDF from Qt and supervise cancel/stalled native PDF operations."""
import multiprocessing as mp
import time
from core import Store,safe_error
from services import Cancelled

def _margin_child(root,path,events,cancel,export_cache=None,interactive=False):
    try:
        from margins import MarginAnnotator,export_margins
        store=Store(root)
        def progress(message):events.send(('progress',message))
        def budget(details,event):
            events.send(('budget',details))
            while not cancel.is_set():
                if events.poll(.1):return events.recv()
            return False
        store.budget_handler=budget
        progress('正在读取 PDF 与已保存进度…')
        if export_cache:
            import json
            from pathlib import Path
            cache=Path(export_cache);state=json.loads(cache.read_text('utf-8'))
            progress('所有段落已缓存，正在生成左右留白 PDF…')
            result=dict(folder=str(cache.parent),**export_margins(state,cache.parent,cancel,progress))
        else:result=MarginAnnotator(store,cancel,progress).run(path,interactive=interactive)
        events.send(('result',result))
    except BaseException as error:events.send(('error',safe_error(error)))
    finally:events.close()

def run_margin_process(store,path,cancel,progress,export_cache=None,stall_seconds=120,interactive=False):
    ctx=mp.get_context('spawn');events,child_events=ctx.Pipe();child_cancel=ctx.Event()
    child=ctx.Process(target=_margin_child,args=(str(store.root),str(path),child_events,child_cancel,export_cache,interactive),daemon=True)
    child.start();child_events.close();last=time.monotonic();cancel_at=None;phase='read';finished=False
    try:
        while True:
            if cancel and cancel.is_set():
                child_cancel.set()
                if cancel_at is None:cancel_at=time.monotonic()
                # Translation gets time to cache a paid response; export may be killed safely.
                if phase!='translation' and time.monotonic()-cancel_at>2:
                    raise Cancelled('已暂停 PDF 排版；全部译文保留，继续时重新排版，不重复翻译。')
            if not events.poll(.1):
                if not child.is_alive():raise RuntimeError('PDF 子进程意外退出；已保存的段落可继续，请重试。')
                limit=420 if phase=='translation' else stall_seconds
                if time.monotonic()-last>limit:raise RuntimeError('PDF 任务长时间无进展，已停止以保持界面响应；译文缓存保留，可继续重试。')
                continue
            try:kind,value=events.recv()
            except EOFError:raise RuntimeError('PDF 子进程已退出；译文缓存保留，请重试。')
            last=time.monotonic()
            if kind=='progress':
                if value.startswith('已保存'):phase='translation'
                elif value.startswith(('所有段落','排版','保存','校验')):phase='export'
                progress(value)
            elif kind=='budget':
                approved=bool(store.budget_handler and store.budget_handler(value,cancel))
                events.send(approved);last=time.monotonic()
            elif kind=='error':raise RuntimeError(value)
            elif kind=='result':finished=True;return value
    finally:
        if not finished:child_cancel.set()
        child.join(timeout=1)
        if child.is_alive():child.terminate();child.join(timeout=3)
        events.close()
