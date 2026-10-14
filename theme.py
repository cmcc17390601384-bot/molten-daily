"""Fixed light/green theme, independent of Windows light/dark/accent settings."""
import os
from PySide6.QtCore import QObject,QEvent,QTimer,Qt
from PySide6.QtGui import QPalette,QColor

COLORS={'Window':'#f3f7f6','WindowText':'#223c40','Base':'#ffffff','AlternateBase':'#edf4f0',
        'Text':'#223c40','Button':'#ffffff','ButtonText':'#223c40','BrightText':'#ffffff',
        'Light':'#ffffff','Midlight':'#edf4f0','Mid':'#c9d9d4','Dark':'#69817f','Shadow':'#294c45',
        'Highlight':'#08786a','HighlightedText':'#ffffff','ToolTipBase':'#fff9df','ToolTipText':'#243b37',
        'Link':'#08786a','LinkVisited':'#52746b','PlaceholderText':'#69817f','Accent':'#08786a'}

def fixed_palette():
    palette=QPalette()
    for group in (QPalette.Active,QPalette.Inactive,QPalette.Disabled):
        for role,color in COLORS.items():palette.setColor(group,getattr(QPalette.ColorRole,role),QColor(color))
    for role in ('WindowText','Text','ButtonText','PlaceholderText'):
        palette.setColor(QPalette.Disabled,getattr(QPalette.ColorRole,role),QColor('#879b95'))
    return palette

EXTRAS='''
QDialog,QMessageBox,QInputDialog,QFileDialog {background:#f3f7f6;color:#223c40;}
QMenu {background:#ffffff;color:#223c40;border:1px solid #c9d9d4;padding:5px;}
QMenu::item:selected {background:#deeee7;color:#15473e;}
QMenu::item:disabled {color:#879b95;}
QComboBox QAbstractItemView,QListView,QTreeView {background:#ffffff;color:#223c40;selection-background-color:#deeee7;selection-color:#15473e;}
QScrollBar:vertical {background:#edf4f0;width:13px;margin:0;}
QScrollBar:horizontal {background:#edf4f0;height:13px;margin:0;}
QScrollBar::handle {background:#b3c9c0;border-radius:5px;min-height:22px;min-width:22px;}
QScrollBar::handle:hover {background:#87aa9d;}
QScrollBar::add-line,QScrollBar::sub-line {width:0;height:0;}
QScrollBar::add-page,QScrollBar::sub-page {background:#edf4f0;}
QSplitter::handle {background:#e4eee9;}
'''

class FixedTheme(QObject):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.pending=False;self.applying=False
        app.installEventFilter(self);app.styleHints().colorSchemeChanged.connect(self.restore)
        self.restore()
    def restore(self,*_):
        if self.applying:return
        self.pending=False;self.applying=True
        try:
            self.app.styleHints().setColorScheme(Qt.ColorScheme.Light)
            if self.app.palette()!=fixed_palette():self.app.setPalette(fixed_palette())
        finally:self.applying=False
    def eventFilter(self,watched,event):
        if watched is self.app and event.type()==QEvent.ApplicationPaletteChange and not self.applying and not self.pending:
            self.pending=True;QTimer.singleShot(0,self.restore)
        return False

def prepare_theme():
    # Qt dialogs share the same fixed palette; native Explorer dialogs follow Windows.
    from PySide6.QtWidgets import QApplication
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs,True)
    if os.name=='nt':os.environ.setdefault('QT_QPA_PLATFORM','windows:darkmode=0')

def install_theme(app,stylesheet):
    # Replacing a live Qt style destroys the old native style object. Keep the
    # existing Fusion style on repeated installation (including dialog tests).
    if app.style().objectName().casefold()!='fusion':app.setStyle('Fusion')
    if app.styleSheet()!=stylesheet+EXTRAS:app.setStyleSheet(stylesheet+EXTRAS)
    if not hasattr(app,'_molten_theme'):app._molten_theme=FixedTheme(app)
    else:app._molten_theme.restore()
    return app._molten_theme
