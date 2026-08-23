import sys
import os
import subprocess
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow

DARK_STYLESHEET = """
    QMainWindow {
        background-color: #1E272C;
    }
    QTabWidget::pane {
        border: 1px solid #263238;
        border-radius: 6px;
        background-color: #263238;
    }
    QTabBar::tab {
        background-color: #37474F;
        color: #B0BEC5;
        border: 1px solid #263238;
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        padding: 8px 12px;
        min-width: 160px;
        font-weight: bold;
        text-align: center;
    }
    QTabBar::tab:selected {
        background-color: #263238;
        color: #FFFFFF;
        border-bottom: 2px solid #3498DB;
    }
    QTabBar::tab:hover {
        background-color: #455A64;
    }
    
    /* Strict Reset for Labels - No Borders, No Backgrounds */
    QLabel {
        border: none;
        background: transparent;
        color: #ECEFF1;
    }
    #MainTitle {
        font-size: 20px;
        font-weight: bold;
        color: #FFFFFF;
    }
    #MainSubtitle {
        font-size: 12px;
        color: #B0BEC5;
    }
    
    QGroupBox {
        font-weight: bold;
        border: 1px solid #37474F;
        border-radius: 6px;
        margin-top: 12px;
        padding-top: 12px;
        color: #ECEFF1;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 3px;
        color: #CFD8DC;
    }
    
    /* Dynamic Statistics and Estimate Boxes */
    #StatsFrame, #EstFrame {
        background-color: #263238;
        border: 1px solid #37474F;
        border-radius: 6px;
    }
    #StatsFrame QLabel, #EstFrame QLabel {
        border: none;
        background: transparent;
    }
    #FailedFilesLabel {
        color: #2ECC71;
        font-weight: bold;
    }
    
    /* Standard Buttons styling */
    QPushButton {
        background-color: #37474F;
        border: 1px solid #455A64;
        border-radius: 4px;
        padding: 6px 12px;
        color: #ECEFF1;
    }
    QPushButton:hover {
        background-color: #455A64;
    }
    QPushButton:pressed {
        background-color: #546E7A;
    }
    QPushButton:disabled {
        background-color: #2C3539;
        color: #5F6C72;
        border: 1px solid #3A454B;
    }
    
    /* Special Dynamic Accent Buttons */
    #ScanBackupBtn {
        background-color: #2980B9;
        border: 1px solid #1F618D;
        color: white;
        font-weight: bold;
        padding: 6px 16px;
    }
    #ScanBackupBtn:hover {
        background-color: #3498DB;
    }
    #StartRescueBtn {
        background-color: #27AE60;
        border: 1px solid #1E8449;
        color: white;
        font-weight: bold;
        font-size: 13px;
        min-height: 35px;
    }
    #StartRescueBtn:hover {
        background-color: #2ECC71;
    }
    #CancelBtn {
        background-color: #C0392B;
        border: 1px solid #922B21;
        color: white;
        font-weight: bold;
    }
    #CancelBtn:hover {
        background-color: #E74C3C;
    }
    #MigrateBtn {
        background-color: #2980B9;
        border: 1px solid #1F618D;
        color: white;
        font-weight: bold;
    }
    #MigrateBtn:hover {
        background-color: #3498DB;
    }
    #CompressBtn {
        background-color: #8E44AD;
        border: 1px solid #6C3483;
        color: white;
        font-weight: bold;
    }
    #CompressBtn:hover {
        background-color: #9B59B6;
    }
    
    QLineEdit {
        border: 1px solid #37474F;
        border-radius: 4px;
        padding: 5px;
        background-color: #2E3B42;
        color: #FFFFFF;
    }
    QLineEdit:focus {
        border: 1px solid #3498DB;
    }
    QCheckBox, QRadioButton {
        spacing: 6px;
        color: #ECEFF1;
    }
    QCheckBox::indicator, QRadioButton::indicator {
        border: 1px solid #455A64;
        background: #2E3B42;
        width: 14px;
        height: 14px;
        border-radius: 3px;
    }
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {
        background-color: #2ECC71;
        border: 1px solid #27AE60;
    }
    QListWidget {
        background-color: #2E3B42;
        color: #FFFFFF;
        border: 1px solid #37474F;
        border-radius: 4px;
        padding: 5px;
    }
    QProgressBar {
        border: 1px solid #37474F;
        border-radius: 4px;
        text-align: center;
        background-color: #2E3B42;
        color: #FFFFFF;
        font-weight: bold;
    }
    QProgressBar::chunk {
        background-color: #2ECC71;
        width: 10px;
        margin: 0.5px;
    }
    QDialog, QMessageBox {
        background-color: #1E272C;
    }
"""

def main():
    # 1. Self-elevation check for root privileges (Touch ID or password authentication)
    if os.geteuid() != 0:
        if getattr(sys, 'frozen', False):
            # Inside a PyInstaller bundled application
            executable = sys.executable
            # osascript requires escaped command
            script = f'do shell script "{executable}" with administrator privileges'
        else:
            # Script-level execution
            script = f'do shell script "python3 {sys.argv[0]}" with administrator privileges'
        
        try:
            subprocess.run(["osascript", "-e", script], check=True)
        except Exception as e:
            print(f"Self-elevation failed or cancelled: {e}")
        sys.exit(0)

    app = QApplication(sys.argv)
    
    # Permanently apply the beautiful high-contrast dark theme
    app.setStyleSheet(DARK_STYLESHEET)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
