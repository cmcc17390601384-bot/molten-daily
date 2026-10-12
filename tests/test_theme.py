from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette,QColor
from PySide6.QtWidgets import QApplication,QDialog,QLineEdit,QMenu
from PySide6.QtTest import QTest
from theme import prepare_theme,install_theme,fixed_palette
from ui import STYLE

def test_palette_restores_after_system_palette_and_scheme_changes():
    prepare_theme();app=QApplication.instance() or QApplication([]);install_theme(app,STYLE)
    dark=QPalette();dark.setColor(QPalette.Window,QColor('#101010'));dark.setColor(QPalette.Text,QColor('white'))
    app.setPalette(dark);app.styleHints().setColorScheme(Qt.ColorScheme.Dark);QTest.qWait(60)
    assert app.palette()==fixed_palette()
    assert app.styleHints().colorScheme()==Qt.ColorScheme.Light
    assert app.palette().color(QPalette.Inactive,QPalette.Window).name()=='#f3f7f6'

def test_popup_and_dialog_use_fixed_light_colors():
    prepare_theme();app=QApplication.instance() or QApplication([]);install_theme(app,STYLE)
    dialog=QDialog();entry=QLineEdit(dialog);menu=QMenu();menu.addAction('打开');dialog.show();QTest.qWait(30)
    assert entry.palette().color(QPalette.Base).name()=='#ffffff'
    assert entry.palette().color(QPalette.Text).name()=='#223c40'
    assert menu.palette().color(QPalette.Window).name()=='#ffffff'
    assert QApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    dialog.close();menu.close()
