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
                result=MarginAnnotator(store,client=LocalClient()).run(pdf)
                from pdf_process import run_margin_process
                import threading
                result=run_margin_process(store,pdf,threading.Event(),lambda message:None)
                w.tabs.setCurrentIndex(2);w.load_pdf(str(pdf));w.margin_done(result);app.processEvents();w.fit_width();app.processEvents();w.grab().save(str(dest/'exe_reader.png'))
                w.zoom.setValue(100);first_width=w.pdf_label.pixmap().width();w.change_zoom(50);assert w.pdf_label.pixmap().width()>first_width
                w.fit_width()
                with fitz.open(result['pdf']) as output:output[0].get_pixmap(matrix=fitz.Matrix(1.3,1.3)).save(str(dest/'margin_preview.png'))
                encrypted=protect('local-self-test');assert unprotect(encrypted)=='local-self-test'
                report={'ok':True,'native_qt':True,'pages':w.tabs.count(),'version':VERSION,'dpapi_roundtrip':True,
                        'margin_export':result['audit'],'isolated_pdf_process':True,'zoom':True,'daily_limit':store.settings()['daily_token_limit']}
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
