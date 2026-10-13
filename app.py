"""Windows native entry point. --smoke is an isolated, no-network executable check."""
import argparse, json, os, sys
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--tray',action='store_true');parser.add_argument('--smoke',type=str);parser.add_argument('--check-api',action='store_true');parser.add_argument('--data-dir');args=parser.parse_args()
    if args.data_dir:os.environ['MOLTEN_DAILY_HOME']=str(Path(args.data_dir).resolve())
    from PySide6.QtCore import QTimer,QLockFile,QStandardPaths
    from PySide6.QtWidgets import QApplication,QMessageBox
    from core import Store,APP,VERSION
    from ui import Window,STYLE
    from theme import prepare_theme,install_theme
    prepare_theme()
    app=QApplication(sys.argv);install_theme(app,STYLE);app.setApplicationName(APP);app.setQuitOnLastWindowClosed(False)
    store=Store(); lock=QLockFile(str(store.root/'app.lock'));lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None,APP,'软件已在运行，请切换到已有窗口。');return 0
    w=Window(store,smoke=bool(args.smoke))
    w.show()  # The simplified app has no background tray workflow.
    if args.smoke:
        dest=Path(args.smoke);dest.mkdir(parents=True,exist_ok=True)
        def capture():
            try:
                import fitz
                from core import protect,unprotect
                w.grab().save(str(dest/'exe_search.png'));w.tabs.setCurrentIndex(1);app.processEvents();w.grab().save(str(dest/'exe_library.png'));w.tabs.setCurrentIndex(3);app.processEvents();w.grab().save(str(dest/'exe_settings.png'))
                pdf=dest/'smoke_source.pdf';d=fitz.open();p=d.new_page()
                p.insert_textbox(fitz.Rect(40,60,550,145),'Uranium electrodeposition in molten salt proceeds at 453 K. The electrolyte controls dendritic growth and current efficiency.',fontsize=12)
                p.insert_textbox(fitz.Rect(40,165,550,240),'Operando measurements show a recovery yield of 94.6%. Recovery yield is not equivalent to current efficiency.',fontsize=12)
                p.draw_rect(fitz.Rect(40,290,360,465),fill=(.1,.6,.5));d.save(pdf);d.close()
                from margins import MarginAnnotator
                class LocalClient:
                    def json(self,system,payload,max_tokens):
                        return {'id':payload['id'],'zh':'完整中文译文：'+payload['text'],'items':[{'kind':'word','quote':'electrodeposition','ipa':'/ɪˌlektroʊˌdepəˈzɪʃən/','zh':'电沉积'},
                            {'kind':'phrase','quote':'recovery yield','ipa':'','zh':'回收率'}]}
                result=MarginAnnotator(store,client=LocalClient()).run(pdf,interactive=True)
                from pdf_process import run_margin_process
                import threading
                result=run_margin_process(store,pdf,threading.Event(),lambda message:None,interactive=True)
                w.tabs.setCurrentIndex(2);w.load_pdf(str(pdf));w.margin_done(result);app.processEvents();w.fit_width();app.processEvents();w.grab().save(str(dest/'exe_reader.png'))
                w.zoom.setValue(100);first_width=w.pdf_label.pixmap().width();w.change_zoom(50);assert w.pdf_label.pixmap().width()>first_width
                w.fit_width()
                assert len(w.source_doc)==1 and w.study_entries
                i,rect=next((i,r) for i,r in w.pdf_label.targets if w.study_entries[i]['kind']=='word')
                w.show_study_note(w.pdf_label.hit_test(rect.center()));assert '电沉积' in w.note_panel.toPlainText()
                w.grab().save(str(dest/'exe_reader.png'))
                from study_package import export_package,import_package
                package=dest/'smoke.mdstudy';export_package(store,pdf,package)
                receiver=Store(dest/'receiver');shared=import_package(receiver,package)
                assert shared['added']==2
                received=run_margin_process(receiver,pdf,threading.Event(),lambda message:None,interactive=True)
                assert received['paragraphs']==2 and receiver.tokens()==0
                encrypted=protect('local-self-test');assert unprotect(encrypted)=='local-self-test'
                report={'ok':True,'native_qt':True,'pages':w.tabs.count(),'version':VERSION,'dpapi_roundtrip':True,
                        'interactive_reader':True,'original_pages':len(w.source_doc),'hover_mapping':True,'portable_package':True,'package_receiver_tokens':receiver.tokens(),'isolated_pdf_process':True,'zoom':True,'daily_limit':store.settings()['daily_token_limit']}
                if args.check_api:
                    from services import DeepSeek
                    report['live_api_models']=DeepSeek(store).models()
            except Exception as e:
                from core import safe_error
                report={'ok':False,'error':safe_error(e)}
            (dest/'exe_smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8');app.exit(0 if report['ok'] else 1)
        QTimer.singleShot(1600,capture)
    result=app.exec();lock.unlock();return result

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    try:sys.exit(main())
    except Exception as e:
        import traceback
        from core import home_dir,safe_error
        root=home_dir();root.mkdir(parents=True,exist_ok=True);(root/'startup_error.txt').write_text(safe_error(e)+'\n'+safe_error(traceback.format_exc()),'utf-8')
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'启动失败：'+safe_error(e)+'\n详情见 userdata/startup_error.txt','熔盐日课',16)
        raise
