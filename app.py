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
        QMessageBox.information(None,APP,'软件已在运行。请从系统托盘打开。');return 0
    w=Window(store,smoke=bool(args.smoke))
    if not args.tray or args.smoke:w.show()
    if args.smoke:
        dest=Path(args.smoke);dest.mkdir(parents=True,exist_ok=True)
        def capture():
            try:
                import fitz
                from translation import Translator
                from core import protect,unprotect
                w.grab().save(str(dest/'exe_dashboard.png')); w.nav.setCurrentRow(4);app.processEvents();w.grab().save(str(dest/'exe_settings.png'))
                w.topic_mode.setCurrentIndex(1);w.topic_input.setPlainText('钠离子电池正极材料的结构演变与原位表征')
                w.queries.setPlainText('sodium ion battery cathode operando\nsodium ion battery structural evolution')
                assert w.save_settings() and store.settings()['topic_mode']=='custom'
                app.processEvents();w.grab().save(str(dest/'exe_custom_topic.png'))
                pdf=dest/'smoke_source.pdf';d=fitz.open();p=d.new_page()
                p.insert_text((40,80),'Uranium electrodeposition in molten NaAlCl4 at 453 K.')
                p.draw_rect(fitz.Rect(40,130,260,240),fill=(.1,.6,.5));d.save(pdf);d.close()
                class LocalTestClient:
                    def json(self,system,payload,max_tokens):
                        return {'id':payload['id'],'zh':'本地排版测试：'+payload['text'],'glossary':[]}
                result=Translator(Store(dest/'data'),client=LocalTestClient()).run(pdf)
                from margins import MarginAnnotator
                class LocalMarginClient:
                    def json(self,system,payload,max_tokens):
                        return {'items':[{'unit':payload[0]['id'],'kind':'phrase','quote':'molten NaAlCl4','ipa':'','zh':'NaAlCl4 熔盐'}]}
                margin_result=MarginAnnotator(Store(dest/'data'),client=LocalMarginClient()).run(pdf)
                from analysis_report import PaperAnalyzer,import_local,report_html
                class LocalAnalysisClient:
                    def json(self,system,payload,max_tokens):
                        u=payload[0];return {'title':'本地打包测试','sections':{'overview':[{'text':'本地证据定位测试。','kind':'fact','evidence':[{'unit':u['id'],'quote':u['text']}]}]},'questions':['测试问题']}
                import_local(Store(dest/'data'),pdf)
                analysis_result=PaperAnalyzer(Store(dest/'data'),client=LocalAnalysisClient()).run(pdf)
                w.analysis_state=analysis_result['state'];w.analysis_view.setHtml(report_html(analysis_result['state']));w.nav.setCurrentRow(6);app.processEvents();w.grab().save(str(dest/'exe_analysis.png'))
                w.load_pdf(str(pdf));w.nav.setCurrentRow(2);app.processEvents();w.render_page();w.grab().save(str(dest/'exe_reader.png'))
                encrypted=protect('local-self-test');assert unprotect(encrypted)=='local-self-test'
                report={'ok':True,'native_qt':True,'pages':w.stack.count(),'version':VERSION,'exe':sys.executable,'dpapi_roundtrip':True,'pdf_export':result['audit'],'margin_export':margin_result['audit'],'local_import_analysis':True,'custom_topic_settings':True}
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
    try:sys.exit(main())
    except Exception as e:
        import traceback
        from core import home_dir,safe_error
        root=home_dir();root.mkdir(parents=True,exist_ok=True);(root/'startup_error.txt').write_text(safe_error(e)+'\n'+safe_error(traceback.format_exc()),'utf-8')
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'启动失败：'+safe_error(e)+'\n详情见 userdata/startup_error.txt','熔盐日课',16)
        raise
