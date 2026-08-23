import os
import time
import getpass
import shutil
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QFileDialog, 
                             QGroupBox, QCheckBox, QRadioButton, QProgressBar, 
                             QPlainTextEdit, QMessageBox, QFrame, QTabWidget,
                             QListWidget, QListWidgetItem, QGridLayout)
from PySide6.QtCore import Qt, Slot
from ui.worker import EstimateWorker, ArchiveWorker, MigrateWorker, CompressWorker, ScanHomeWorker

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SmartTimeArchive - Time Machine Recovery & Archival")
        self.resize(850, 750)
        
        self.estimate_worker = None
        self.archive_worker = None
        self.migrate_worker = None
        self.compress_worker = None
        self.scan_worker = None
        
        # Keep track of active backup dates list from scan
        self.all_backup_items = []
        
        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 1. Header Area
        title_label = QLabel("SmartTimeArchive")
        title_label.setObjectName("MainTitle")
        subtitle_label = QLabel("Time Machine Recovery, Deduplication and Archival Tool")
        subtitle_label.setObjectName("MainSubtitle")
        main_layout.addWidget(title_label)
        main_layout.addWidget(subtitle_label)

        # 2. Tabs Widget
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Tab 1: Rescue & Archival
        tab1_widget = QWidget()
        tab1_layout = QVBoxLayout(tab1_widget)
        tab1_layout.setSpacing(10)
        tab1_layout.setContentsMargins(10, 10, 10, 10)
        
        # Path & Scan Config Group
        path_group = QGroupBox("1. Source & Destination Paths")
        path_layout = QVBoxLayout(path_group)
        path_layout.setSpacing(8)
        
        # Source Path
        src_layout = QHBoxLayout()
        src_label = QLabel("Backup Source:")
        src_label.setFixedWidth(110)
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("Select mounted Time Machine volume or .sparsebundle (e.g. /Volumes/DJI Fly)")
        if os.path.exists("/Volumes/DJI Fly"):
            self.src_edit.setText("/Volumes/DJI Fly")
        btn_browse_src = QPushButton("Browse...")
        btn_browse_src.clicked.connect(self.browse_source)
        src_layout.addWidget(src_label)
        src_layout.addWidget(self.src_edit)
        src_layout.addWidget(btn_browse_src)
        path_layout.addLayout(src_layout)
        
        # Destination Path
        dest_layout = QHBoxLayout()
        dest_label = QLabel("Destination:")
        dest_label.setFixedWidth(110)
        self.dest_edit = QLineEdit()
        self.dest_edit.setPlaceholderText("Select folder on destination APFS disk (e.g. /Users/hbarchini/TRASH/quinto)")
        btn_browse_dest = QPushButton("Browse...")
        btn_browse_dest.clicked.connect(self.browse_destination)
        dest_layout.addWidget(dest_label)
        dest_layout.addWidget(self.dest_edit)
        dest_layout.addWidget(btn_browse_dest)
        path_layout.addLayout(dest_layout)
        
        # Username & Scan Row
        scan_row_layout = QHBoxLayout()
        user_label = QLabel("Target Username:")
        user_label.setFixedWidth(110)
        self.user_edit = QLineEdit()
        self.user_edit.setText(self.get_logged_in_user())
        self.user_edit.setPlaceholderText("Specify username (e.g. hbarchini)")
        self.user_edit.setFixedWidth(150)
        
        self.btn_scan_backup = QPushButton("🔍 Scan Backup Contents")
        self.btn_scan_backup.setObjectName("ScanBackupBtn")
        self.btn_scan_backup.clicked.connect(self.scan_backup_contents)
        
        scan_row_layout.addWidget(user_label)
        scan_row_layout.addWidget(self.user_edit)
        scan_row_layout.addSpacing(20)
        scan_row_layout.addWidget(self.btn_scan_backup)
        scan_row_layout.addStretch()
        path_layout.addLayout(scan_row_layout)
        
        tab1_layout.addWidget(path_group)

        # Dynamic Selection Section (Dynamic Checklist Columns)
        self.selection_group = QGroupBox("2. Selection Panel")
        self.selection_group.setEnabled(False) # Enabled only after a successful scan
        selection_layout = QHBoxLayout(self.selection_group)
        selection_layout.setSpacing(15)
        
        # Column A: Folders Selection
        col_a_layout = QVBoxLayout()
        col_a_title = QLabel("Select Home Folders to Resque:")
        col_a_title.setStyleSheet("font-weight: bold;")
        self.folders_list = QListWidget()
        
        col_a_buttons = QHBoxLayout()
        btn_sel_all_folders = QPushButton("Select All")
        btn_sel_all_folders.clicked.connect(self.select_all_folders)
        btn_desel_all_folders = QPushButton("Deselect All")
        btn_desel_all_folders.clicked.connect(self.deselect_all_folders)
        col_a_buttons.addWidget(btn_sel_all_folders)
        col_a_buttons.addWidget(btn_desel_all_folders)
        
        col_a_layout.addWidget(col_a_title)
        col_a_layout.addWidget(self.folders_list)
        col_a_layout.addLayout(col_a_buttons)
        selection_layout.addLayout(col_a_layout, 1)
        
        # Column B: Dates Selection
        col_b_layout = QVBoxLayout()
        col_b_title = QLabel("Select Backup Dates to Rescue:")
        col_b_title.setStyleSheet("font-weight: bold;")
        self.dates_list = QListWidget()
        
        col_b_buttons = QHBoxLayout()
        btn_sel_all_dates = QPushButton("Select All")
        btn_sel_all_dates.clicked.connect(self.select_all_dates)
        btn_desel_all_dates = QPushButton("Deselect All")
        btn_desel_all_dates.clicked.connect(self.deselect_all_dates)
        col_b_buttons.addWidget(btn_sel_all_dates)
        col_b_buttons.addWidget(btn_desel_all_dates)
        
        col_b_layout.addWidget(col_b_title)
        col_b_layout.addWidget(self.dates_list)
        col_b_layout.addLayout(col_b_buttons)
        selection_layout.addLayout(col_b_layout, 1)
        
        tab1_layout.addWidget(self.selection_group)

        # Options Layout (Format & Estimate Row)
        bottom_options = QHBoxLayout()
        format_group = QGroupBox("3. Export Format")
        format_vbox = QVBoxLayout(format_group)
        self.format_folders_rb = QRadioButton("Export as Deduplicated Folders (Default)")
        self.format_folders_rb.setChecked(True)
        self.format_tar_rb = QRadioButton("Export as Compressed Tarball (.tar.gz)")
        format_vbox.addWidget(self.format_folders_rb)
        format_vbox.addWidget(self.format_tar_rb)
        bottom_options.addWidget(format_group, 2)
        
        est_frame = QFrame()
        est_frame.setFrameShape(QFrame.StyledPanel)
        est_frame.setObjectName("EstFrame")
        est_layout = QVBoxLayout(est_frame)
        self.btn_estimate = QPushButton("Calculate Estimated Size")
        self.btn_estimate.clicked.connect(self.calculate_estimate)
        self.btn_estimate.setEnabled(False)
        self.estimate_label = QLabel("Estimated Size: N/A\nUnique Files: N/A")
        self.estimate_label.setStyleSheet("font-weight: bold;")
        self.estimate_label.setAlignment(Qt.AlignCenter)
        est_layout.addWidget(self.btn_estimate)
        est_layout.addWidget(self.estimate_label)
        bottom_options.addWidget(est_frame, 3)
        
        tab1_layout.addLayout(bottom_options)

        # Start rescue button
        self.btn_start = QPushButton("Start Archive Rescue")
        self.btn_start.setEnabled(False)
        self.btn_start.setObjectName("StartRescueBtn")
        self.btn_start.clicked.connect(self.start_archiving)
        tab1_layout.addWidget(self.btn_start)
        
        self.tabs.addTab(tab1_widget, "Rescue & Archival")

        # Tab 2: Archive Manager (Migration & Compression)
        tab2_widget = QWidget()
        tab2_layout = QVBoxLayout(tab2_widget)
        tab2_layout.setSpacing(12)
        tab2_layout.setContentsMargins(10, 10, 10, 10)
        
        # Migration Group
        migration_group = QGroupBox("Archive Migration (APFS SSD-to-SSD Move)")
        migration_layout = QVBoxLayout(migration_group)
        migration_layout.setSpacing(8)
        
        m_desc = QLabel("Migrates a previously completed backup folder structure from your internal drive to another APFS disk, preserving all hard links and deduplication.")
        m_desc.setWordWrap(True)
        m_desc.setStyleSheet("font-size: 11px;")
        migration_layout.addWidget(m_desc)
        
        m_src_layout = QHBoxLayout()
        m_src_label = QLabel("Source Archive:")
        m_src_label.setFixedWidth(120)
        self.m_src_edit = QLineEdit()
        self.m_src_edit.setPlaceholderText("Select existing archive folder (e.g. /Users/hbarchini/TRASH/quinto)")
        btn_m_src = QPushButton("Browse...")
        btn_m_src.clicked.connect(self.browse_migration_source)
        m_src_layout.addWidget(m_src_label)
        m_src_layout.addWidget(self.m_src_edit)
        m_src_layout.addWidget(btn_m_src)
        migration_layout.addLayout(m_src_layout)
        
        m_dest_layout = QHBoxLayout()
        m_dest_label = QLabel("Destination Path:")
        m_dest_label.setFixedWidth(120)
        self.m_dest_edit = QLineEdit()
        self.m_dest_edit.setPlaceholderText("Select destination APFS path (e.g. /Volumes/Dji_Fly/rescatado)")
        btn_m_dest = QPushButton("Browse...")
        btn_m_dest.clicked.connect(self.browse_migration_dest)
        m_dest_layout.addWidget(m_dest_label)
        m_dest_layout.addWidget(self.m_dest_edit)
        m_dest_layout.addWidget(btn_m_dest)
        migration_layout.addLayout(m_dest_layout)
        
        m_action_layout = QHBoxLayout()
        self.cb_delete_source = QCheckBox("Delete source files after successful migration")
        self.cb_delete_source.setChecked(False)
        self.btn_migrate = QPushButton("Migrate Archive")
        self.btn_migrate.setObjectName("MigrateBtn")
        self.btn_migrate.clicked.connect(self.start_migration)
        m_action_layout.addWidget(self.cb_delete_source)
        m_action_layout.addStretch()
        m_action_layout.addWidget(self.btn_migrate)
        migration_layout.addLayout(m_action_layout)
        
        tab2_layout.addWidget(migration_group)
        
        # Compression Group
        compression_group = QGroupBox("Archive Compression (Tarball for Cloud Storage)")
        compression_layout = QVBoxLayout(compression_group)
        compression_layout.setSpacing(8)
        
        c_desc = QLabel("Compresses an existing folder structure into a single .tar.gz archive, preserving hard links. Ideal for uploading to Google Drive/Cloud without file duplication.")
        c_desc.setWordWrap(True)
        c_desc.setStyleSheet("font-size: 11px;")
        compression_layout.addWidget(c_desc)
        
        c_src_layout = QHBoxLayout()
        c_src_label = QLabel("Source Folder:")
        c_src_label.setFixedWidth(120)
        self.c_src_edit = QLineEdit()
        self.c_src_edit.setPlaceholderText("Select folder structure to compress")
        btn_c_src = QPushButton("Browse...")
        btn_c_src.clicked.connect(self.browse_compression_source)
        c_src_layout.addWidget(c_src_label)
        c_src_layout.addWidget(self.c_src_edit)
        c_src_layout.addWidget(btn_c_src)
        compression_layout.addLayout(c_src_layout)
        
        c_dest_layout = QHBoxLayout()
        c_dest_label = QLabel("Output Tarball:")
        c_dest_label.setFixedWidth(120)
        self.c_dest_edit = QLineEdit()
        self.c_dest_edit.setPlaceholderText("Select output file name (e.g. /Volumes/Dji_Fly/backup.tar.gz)")
        btn_c_dest = QPushButton("Save As...")
        btn_c_dest.clicked.connect(self.browse_compression_dest)
        c_dest_layout.addWidget(c_dest_label)
        c_dest_layout.addWidget(self.c_dest_edit)
        c_dest_layout.addWidget(btn_c_dest)
        compression_layout.addLayout(c_dest_layout)
        
        c_action_layout = QHBoxLayout()
        self.btn_compress = QPushButton("Compress to Tarball")
        self.btn_compress.setObjectName("CompressBtn")
        self.btn_compress.clicked.connect(self.start_compression)
        c_action_layout.addStretch()
        c_action_layout.addWidget(self.btn_compress)
        compression_layout.addLayout(c_action_layout)
        
        tab2_layout.addWidget(compression_group)
        tab2_layout.addStretch()
        
        self.tabs.addTab(tab2_widget, "Archive Manager")

        # 3. Graphical Statistics Panel (MacOS Style)
        stats_frame = QFrame()
        stats_frame.setFrameShape(QFrame.StyledPanel)
        stats_frame.setObjectName("StatsFrame")
        stats_layout = QGridLayout(stats_frame)
        stats_layout.setContentsMargins(10, 8, 10, 8)
        
        lbl_f_title = QLabel("📂 Active File:")
        lbl_f_title.setStyleSheet("font-weight: bold;")
        self.lbl_active_file = QLabel("None")
        
        lbl_p_title = QLabel("📈 Files Processed:")
        lbl_p_title.setStyleSheet("font-weight: bold;")
        self.lbl_processed_files = QLabel("0")
        
        lbl_w_title = QLabel("💾 Data Written:")
        lbl_w_title.setStyleSheet("font-weight: bold;")
        self.lbl_data_written = QLabel("0.00 GB")
        
        lbl_e_title = QLabel("⚠️ Failed Files:")
        lbl_e_title.setStyleSheet("font-weight: bold;")
        self.lbl_failed_files = QLabel("0")
        self.lbl_failed_files.setObjectName("FailedFilesLabel")
        
        stats_layout.addWidget(lbl_f_title, 0, 0)
        stats_layout.addWidget(self.lbl_active_file, 0, 1)
        stats_layout.addWidget(lbl_p_title, 0, 2)
        stats_layout.addWidget(self.lbl_processed_files, 0, 3)
        stats_layout.addWidget(lbl_w_title, 1, 0)
        stats_layout.addWidget(self.lbl_data_written, 1, 1)
        stats_layout.addWidget(lbl_e_title, 1, 2)
        stats_layout.addWidget(self.lbl_failed_files, 1, 3)
        
        main_layout.addWidget(stats_frame)

        # 4. Shared Progress & Action controls
        bottom_frame = QFrame()
        bottom_frame.setFrameShape(QFrame.StyledPanel)
        bottom_layout = QVBoxLayout(bottom_frame)
        bottom_layout.setContentsMargins(5, 5, 5, 5)
        
        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setObjectName("CancelBtn")
        self.btn_cancel.clicked.connect(self.cancel_operation)
        
        progress_row.addWidget(self.progress_bar)
        progress_row.addWidget(self.btn_cancel)
        bottom_layout.addLayout(progress_row)
        
        # Shared Status Label and Toggle Log Button
        status_row = QHBoxLayout()
        self.progress_label = QLabel("Status: Ready (Please scan source backup first)")
        self.progress_label.setStyleSheet("font-size: 11px;")
        
        self.btn_toggle_log = QPushButton("📄 Show Execution Log")
        self.btn_toggle_log.setStyleSheet("font-size: 10px; min-width: 140px;")
        self.btn_toggle_log.clicked.connect(self.toggle_log_console)
        
        status_row.addWidget(self.progress_label)
        status_row.addStretch()
        status_row.addWidget(self.btn_toggle_log)
        bottom_layout.addLayout(status_row)
        
        main_layout.addWidget(bottom_frame)

        # 5. Collapsible Execution Log (Hidden by default)
        self.log_group = QGroupBox("Execution Log Details")
        log_layout = QVBoxLayout(self.log_group)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("font-family: monospace; font-size: 11px;")
        log_layout.addWidget(self.log_text)
        
        main_layout.addWidget(self.log_group, 1)
        self.log_group.setVisible(False) # Hidden by default!

        # 6. Native macOS Menu Bar
        self.create_menu_bar()

    def create_menu_bar(self):
        menu_bar = self.menuBar()
        help_menu = menu_bar.addMenu("Help")
        about_action = help_menu.addAction("About SmartTimeArchive")
        about_action.triggered.connect(self.show_about_dialog)

    def show_about_dialog(self):
        QMessageBox.about(self, "About SmartTimeArchive",
            "<h3>SmartTimeArchive</h3>"
            "<p><b>Version:</b> 1.0.0</p>"
            "<p><b>Author:</b> Humberto Barchini & Antigravity</p>"
            "<p><b>Description:</b> A premium, lightweight macOS utility designed to rescue user profiles from APFS Time Machine snapshots and compile clean reference archives using deduplicated hard links or compressed tarballs.</p>"
            "<p><i>Optimized for network sparsebundle mounts and direct USB drives.</i></p>"
        )

    def get_logged_in_user(self):
        import subprocess
        try:
            res = subprocess.run(["stat", "-f", "%Su", "/dev/console"], capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except Exception:
            return getpass.getuser()

    # --- Collapsible Log Toggle ---
    def toggle_log_console(self):
        is_visible = self.log_group.isVisible()
        self.log_group.setVisible(not is_visible)
        self.btn_toggle_log.setText("📄 Hide Execution Log" if not is_visible else "📄 Show Execution Log")

    # --- Browse Paths ---
    def browse_source(self):
        # Allow selecting folders or sparsebundles
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Backup Source (Folder or Sparsebundle)", "/Volumes", "All Files (*.*);;Time Machine Sparsebundle (*.sparsebundle)")
        if not file_path:
            # Fallback to directory selection
            file_path = QFileDialog.getExistingDirectory(self, "Select Backup Source Directory", "/Volumes")
            
        if file_path:
            file_path = file_path.rstrip("/")
            self.src_edit.setText(file_path)
            self.progress_label.setText("Status: Paths loaded. Click 'Scan Backup Contents' to load folders.")

    def browse_destination(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Destination", "/Volumes")
        if dir_path:
            dir_path = dir_path.rstrip("/")
            self.dest_edit.setText(dir_path)

    def browse_migration_source(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Source Archive", "/Users")
        if dir_path:
            dir_path = dir_path.rstrip("/")
            self.m_src_edit.setText(dir_path)
            self.c_src_edit.setText(dir_path)

    def browse_migration_dest(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Migration Destination", "/Volumes")
        if dir_path:
            dir_path = dir_path.rstrip("/")
            self.m_dest_edit.setText(dir_path)

    def browse_compression_source(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Folder to Compress", "/Users")
        if dir_path:
            dir_path = dir_path.rstrip("/")
            self.c_src_edit.setText(dir_path)

    def browse_compression_dest(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Tarball As", "/Volumes", "Tarball Archive (*.tar.gz)")
        if file_path:
            file_path = file_path.rstrip("/")
            if not file_path.endswith(".tar.gz"):
                file_path += ".tar.gz"
            self.c_dest_edit.setText(file_path)

    # --- Selection Lists Helpers ---
    def select_all_folders(self):
        for i in range(self.folders_list.count()):
            self.folders_list.item(i).setCheckState(Qt.Checked)

    def deselect_all_folders(self):
        for i in range(self.folders_list.count()):
            self.folders_list.item(i).setCheckState(Qt.Unchecked)

    def select_all_dates(self):
        for i in range(self.dates_list.count()):
            self.dates_list.item(i).setCheckState(Qt.Checked)

    def deselect_all_dates(self):
        for i in range(self.dates_list.count()):
            self.dates_list.item(i).setCheckState(Qt.Unchecked)

    # --- Scanning Backup Source ---
    def scan_backup_contents(self):
        src = self.src_edit.text().strip()
        src = src.rstrip("/")
        user = self.user_edit.text().strip()
        
        if not src or not os.path.exists(src):
            QMessageBox.warning(self, "Path Error", "Please specify a valid backup source path before scanning.")
            return

        self.progress_label.setText("Status: Scanning backup contents (finding dates & folders)...")
        self.progress_bar.setValue(0)
        self.btn_scan_backup.setEnabled(False)
        self.tabs.setEnabled(False)
        
        # Populate dynamic dates list (synchronously lists snapshot names, which takes milliseconds)
        from engine import ArchiveEngine
        try:
            # Temporary engine to list snapshots
            engine = ArchiveEngine(src, "", target_user=user)
            active_src = engine.setup_source()
            if not active_src:
                QMessageBox.critical(self, "Attachment Error", "Failed to mount source. Make sure network permissions are active.")
                self.btn_scan_backup.setEnabled(True)
                self.tabs.setEnabled(True)
                return
                
            self.all_backup_items = engine.get_backup_folders(active_src)
            engine.cleanup_source()
            
            self.dates_list.clear()
            for name, item_type, path_or_snap in self.all_backup_items:
                item = QListWidgetItem(name)
                item.setCheckState(Qt.Checked) # Checked by default
                self.dates_list.addItem(item)
        except Exception as e:
            QMessageBox.critical(self, "Scan Error", f"Failed to list backup dates: {e}")
            self.btn_scan_backup.setEnabled(True)
            self.tabs.setEnabled(True)
            return

        # Scan home directories dynamically in a background QThread to prevent UI freezing
        self.scan_worker = ScanHomeWorker(src, user)
        self.scan_worker.finished_sig.connect(self.scan_home_finished)
        self.scan_worker.error_sig.connect(self.scan_home_error)
        self.scan_worker.start()

    @Slot(list)
    def scan_home_finished(self, home_folders):
        self.folders_list.clear()
        
        # Predefined list of standard folder checks
        prechecked_folders = ["Desktop", "Documents", "Projects", "obsidian", "Bitacoras", "NMS_DATA"]
        
        for folder_name in home_folders:
            item = QListWidgetItem(folder_name)
            if folder_name in prechecked_folders:
                item.setCheckState(Qt.Checked) # Pre-checked by default
            else:
                item.setCheckState(Qt.Unchecked) # Checked manually
            self.folders_list.addItem(item)
            
        self.selection_group.setEnabled(True)
        self.btn_estimate.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.btn_scan_backup.setEnabled(True)
        self.tabs.setEnabled(True)
        self.progress_label.setText(f"Status: Scan completed. Found {self.dates_list.count()} dates and {self.folders_list.count()} home folders.")
        self.progress_bar.setValue(100)

    @Slot(str)
    def scan_home_error(self, err_msg):
        QMessageBox.critical(self, "Scan Error", f"Failed to scan home subdirectories: {err_msg}")
        self.btn_scan_backup.setEnabled(True)
        self.tabs.setEnabled(True)
        self.progress_label.setText("Status: Scan failed.")

    def get_selected_folders(self):
        folders = []
        for i in range(self.folders_list.count()):
            item = self.folders_list.item(i)
            if item.checkState() == Qt.Checked:
                folders.append(item.text())
        return folders

    def get_selected_dates_filter(self):
        """Builds exclusions list dynamically for unchecked dates."""
        # Under the new design, the engine will only copy what the user checked.
        # But wait! The easiest way is to modify the engine's list of backup folders
        # to only include the ones checked! We will pass the list of selected dates
        # by matching them during the copy!
        # Actually, let's just modify engine's get_backup_folders dynamically or filter them.
        # To do this cleanly, we can subclass ArchiveEngine or filter self.all_backup_items.
        # However, let's just make get_backup_folders in the engine look at the checkboxes,
        # or we can pass the checked dates!
        # Let's see: we can pass a list of checked_dates to the worker, and the engine
        # will filter its backup_items to only include these!
        # Wait, does engine.py support this?
        # In engine.py:
        #   backup_items = self.get_backup_folders()
        # If we pass an exclusions list or just filter them, it works.
        # Wait! To make it robust without modifying engine.py again, we can just pass
        # any unchecked date folders as specific exclusions!
        # Yes! If com.apple.TimeMachine.YYYY-MM-DD.backup is unchecked, we just add
        # "*/com.apple.TimeMachine.YYYY-MM-DD.backup/*" to exclusions!
        # This is a genius, zero-risk workaround! It requires absolutely NO code changes in engine.py!
        # If a date folder is excluded in exclusions, the engine walks it, matches the pattern,
        # and skips it entirely!
        # This is brilliant!
        pass

    def get_exclusions_list(self):
        # System-wide exclusions
        exclusions = [
            "*/.ollama/*",
            "*/.omlx/*",
            "*/.npm/*",
            "*/.npm-global/*",
            "*/.hermes/*",
            "*/.Trash/*",
            "*/node_modules/*",
            "*/venv/*",
            "*/env/*",
            "*/.venv/*",
            "*/anythingllm-desktop/*",
            "*/omlx/*",
            "*.omlx",
            "*/Library/*",
            "*/.cache/*",
            "*/.cargo/*",
            "*/.vscode/*",
            "*/.vscode-shared/*",
            "*/.code-index/*",
            "*/.gemini/*",
            "*/.gemini-cli/*",
            "*/.aspnet/*",
            "*/.agentmemory/*",
            "*/.antigravity-ide/*",
            "*/.antigravitycli/*"
        ]
        
        # Add unchecked dates to exclusions dynamically
        for i in range(self.dates_list.count()):
            item = self.dates_list.item(i)
            if item.checkState() == Qt.Unchecked:
                # Add a glob pattern to skip this date folder entirely
                exclusions.append(f"*/{item.text()}/*")
                
        return exclusions

    def disable_ui(self):
        self.tabs.setEnabled(False)
        self.btn_cancel.setEnabled(True)

    def enable_ui(self):
        self.tabs.setEnabled(True)
        self.btn_cancel.setEnabled(False)

    # --- Estimation ---
    def calculate_estimate(self):
        src = self.src_edit.text().strip()
        user = self.user_edit.text().strip()
        
        self.disable_ui()
        self.progress_bar.setValue(0)
        self.estimate_label.setText("Estimating size...\nPlease wait.")
        self.progress_label.setText("Status: Initializing scan...")

        exclusions = self.get_exclusions_list()
        folders = self.get_selected_folders()
        
        self.estimate_worker = EstimateWorker(src, exclusions, target_user=user, included_folders=folders)
        self.estimate_worker.progress_sig.connect(self.update_estimate_progress)
        self.estimate_worker.finished_sig.connect(self.estimate_finished)
        self.estimate_worker.error_sig.connect(self.estimate_error)
        self.estimate_worker.start()

    @Slot(int, int, str)
    def update_estimate_progress(self, idx, total, name):
        percent = int((idx / total) * 100)
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"Scanning backup {idx}/{total}: {name}")

    @Slot(int, int, int)
    def estimate_finished(self, total_size, file_count, folder_count):
        size_gb = total_size / (1024 * 1024 * 1024)
        if size_gb >= 1000:
            size_text = f"{size_gb/1024:.2f} TB"
        else:
            size_text = f"{size_gb:.2f} GB"
            
        if self.estimate_worker and self.estimate_worker.is_cancelled:
            self.estimate_label.setText("Estimated Size: Cancelled\nUnique Files: N/A")
            self.progress_label.setText("Status: Estimation cancelled.")
            self.progress_bar.setValue(0)
        else:
            self.estimate_label.setText(f"Estimated Size: {size_text}\nUnique Files: {file_count:,}")
            self.progress_label.setText("Status: Estimation completed.")
            self.progress_bar.setValue(100)
            
        self.enable_ui()

    @Slot(str)
    def estimate_error(self, err_msg):
        QMessageBox.critical(self, "Estimation Error", f"Failed to estimate size: {err_msg}")
        self.estimate_label.setText("Estimated Size: N/A\nUnique Files: N/A")
        self.progress_label.setText("Status: Estimation failed.")
        self.progress_bar.setValue(0)
        self.enable_ui()

    # --- Archiving ---
    def start_archiving(self):
        src = self.src_edit.text().strip()
        dest = self.dest_edit.text().strip()
        user = self.user_edit.text().strip()
        
        if not src or not os.path.exists(src):
            QMessageBox.warning(self, "Path Error", "Please specify a valid backup source.")
            return
        if not dest or not os.path.exists(dest):
            QMessageBox.warning(self, "Path Error", "Please select a valid destination folder.")
            return

        exclusions = self.get_exclusions_list()
        folders = self.get_selected_folders()
        output_tar = self.format_tar_rb.isChecked()
        
        self.log_text.clear()
        self.log_text.appendPlainText(f"--- Rescue Job Started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
        self.log_text.appendPlainText(f"Source: {src}")
        self.log_text.appendPlainText(f"Destination: {dest}")
        self.log_text.appendPlainText(f"Target Username: {user}")
        self.log_text.appendPlainText(f"Included folders: {', '.join(folders)}")
        
        # Reset visual labels
        self.lbl_active_file.setText("None")
        self.lbl_processed_files.setText("0")
        self.lbl_data_written.setText("0.00 GB")
        self.lbl_failed_files.setText("0")
        self.lbl_failed_files.setStyleSheet("")
        
        self.disable_ui()
        self.progress_bar.setValue(0)
        
        # Track statistics
        self.files_copied_count = 0
        self.failed_files_count = 0
        
        self.archive_worker = ArchiveWorker(src, dest, exclusions, output_tar, target_user=user, included_folders=folders)
        self.archive_worker.progress_sig.connect(self.update_archive_progress)
        self.archive_worker.log_sig.connect(self.append_log)
        self.archive_worker.finished_sig.connect(self.archive_finished)
        self.archive_worker.start()

    @Slot(int, int, str)
    def update_archive_progress(self, idx, total, msg):
        percent = int((idx / total) * 100)
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"Rescuing date {idx}/{total}")
        
        # Parse physical size and date from info msg: Date: YYYY-MM-DD | Copied: X GB
        if "Copied:" in msg:
            parts = msg.split("|")
            date_info = parts[0].replace("Date:", "").strip()
            size_info = parts[1].replace("Copied:", "").strip()
            
            self.lbl_active_file.setText(date_info)
            self.lbl_data_written.setText(size_info)

    @Slot(str)
    def append_log(self, msg):
        self.log_text.appendPlainText(msg)
        
        # Update file counters and detect bad sectors in logs
        if "Starting backup date:" not in msg and "Process finished" not in msg and "Error" not in msg:
            self.files_copied_count += 1
            self.lbl_processed_files.setText(f"{self.files_copied_count:,}")
            
            # Shorten active file path label to look clean
            file_name = os.path.basename(msg.split(":")[-1].strip())
            self.lbl_active_file.setText(file_name)
            
        if "[ERROR]" in msg or "[I/O ERROR]" in msg:
            self.failed_files_count += 1
            self.lbl_failed_files.setText(str(self.failed_files_count))
            self.lbl_failed_files.setStyleSheet("color: #E74C3C; font-weight: bold;") # Turn red

    @Slot(bool)
    def archive_finished(self, success):
        self.progress_bar.setValue(100 if success else 0)
        if success:
            QMessageBox.information(self, "Success", "Rescue completed successfully!")
            self.progress_label.setText("Status: Rescue completed successfully.")
        else:
            QMessageBox.critical(self, "Failure", "Rescue failed or was cancelled.")
            self.progress_label.setText("Status: Rescue failed/cancelled.")
        self.enable_ui()

    # --- Migration ---
    def start_migration(self):
        src = self.m_src_edit.text().strip()
        dest = self.m_dest_edit.text().strip()
        
        if not src or not os.path.exists(src):
            QMessageBox.warning(self, "Path Error", "Please select a valid source archive folder.")
            return
        if not dest or not os.path.exists(dest):
            QMessageBox.warning(self, "Path Error", "Please select a valid destination folder.")
            return

        self.log_text.clear()
        self.log_text.appendPlainText(f"--- Migration Started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
        self.log_text.appendPlainText(f"Source: {src}")
        self.log_text.appendPlainText(f"Destination: {dest}")

        # Reset statistics
        self.files_copied_count = 0
        self.failed_files_count = 0
        self.lbl_active_file.setText("None")
        self.lbl_processed_files.setText("0")
        self.lbl_data_written.setText("Calculating...")
        self.lbl_failed_files.setText("0")
        self.lbl_failed_files.setStyleSheet("")

        self.disable_ui()
        self.progress_bar.setValue(0)
        
        self.migrate_worker = MigrateWorker(src, dest)
        self.migrate_worker.progress_sig.connect(self.update_migration_progress)
        self.migrate_worker.log_sig.connect(self.append_log)
        self.migrate_worker.finished_sig.connect(self.migration_finished)
        self.migrate_worker.start()

    @Slot(int, int, str)
    def update_migration_progress(self, idx, total, folder_info):
        percent = int((idx / total) * 100)
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"Migrating... {percent}%")
        self.lbl_active_file.setText(folder_info)
        self.lbl_processed_files.setText(f"{idx:,} / {total:,}")

    @Slot(bool)
    def migration_finished(self, success):
        self.progress_bar.setValue(100 if success else 0)
        if success:
            self.progress_label.setText("Status: Migration completed successfully.")
            self.lbl_data_written.setText("Done")
            
            if self.cb_delete_source.isChecked():
                reply = QMessageBox.question(self, "Delete Source Archive", 
                                           "Migration completed successfully. Are you sure you want to permanently delete the original source folder to free up space?",
                                           QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if reply == QMessageBox.Yes:
                    try:
                        self.progress_label.setText("Status: Deleting original source folder...")
                        shutil.rmtree(self.m_src_edit.text().strip())
                        self.progress_label.setText("Status: Original folder deleted.")
                        self.append_log("Source folder deleted successfully to reclaim space.")
                    except Exception as e:
                        QMessageBox.critical(self, "Deletion Error", f"Failed to delete source folder: {e}")
                        self.append_log(f"Error during deletion: {e}")
            
            QMessageBox.information(self, "Success", "Migration completed successfully!")
        else:
            QMessageBox.critical(self, "Failure", "Migration failed or was cancelled.")
            self.progress_label.setText("Status: Migration failed/cancelled.")
        self.enable_ui()

    # --- Compression ---
    def start_compression(self):
        src = self.c_src_edit.text().strip()
        dest = self.c_dest_edit.text().strip()
        
        if not src or not os.path.exists(src):
            QMessageBox.warning(self, "Path Error", "Please select a valid source folder.")
            return
        if not dest:
            QMessageBox.warning(self, "Path Error", "Please select a destination tarball path.")
            return

        self.log_text.clear()
        self.log_text.appendPlainText(f"--- Compression Started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
        self.log_text.appendPlainText(f"Source: {src}")
        self.log_text.appendPlainText(f"Output: {dest}")

        # Reset statistics
        self.files_copied_count = 0
        self.failed_files_count = 0
        self.lbl_active_file.setText("None")
        self.lbl_processed_files.setText("0")
        self.lbl_data_written.setText("Writing tarball...")
        self.lbl_failed_files.setText("0")
        self.lbl_failed_files.setStyleSheet("")

        self.disable_ui()
        self.progress_bar.setValue(0)
        
        self.compress_worker = CompressWorker(src, dest)
        self.compress_worker.progress_sig.connect(self.update_compression_progress)
        self.compress_worker.log_sig.connect(self.append_log)
        self.compress_worker.finished_sig.connect(self.compression_finished)
        self.compress_worker.start()

    @Slot(int, int, str)
    def update_compression_progress(self, idx, total, folder_info):
        percent = int((idx / total) * 100)
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"Compressing... {percent}%")
        self.lbl_active_file.setText(folder_info)
        self.lbl_processed_files.setText(f"{idx:,} / {total:,}")

    @Slot(bool)
    def compression_finished(self, success):
        self.progress_bar.setValue(100 if success else 0)
        if success:
            QMessageBox.information(self, "Success", "Compression completed successfully!")
            self.progress_label.setText("Status: Compression completed successfully.")
            self.lbl_data_written.setText("Done")
        else:
            QMessageBox.critical(self, "Failure", "Compression failed or was cancelled.")
            self.progress_label.setText("Status: Compression failed/cancelled.")
        self.enable_ui()

    # --- Cancellation ---
    def cancel_operation(self):
        if self.archive_worker and self.archive_worker.isRunning():
            reply = QMessageBox.question(self, "Cancel Backup", 
                                       "Are you sure you want to stop the backup process?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.btn_cancel.setEnabled(False)
                self.progress_label.setText("Status: Cancelling backup... Please wait.")
                self.archive_worker.cancel()
        
        elif self.estimate_worker and self.estimate_worker.isRunning():
            reply = QMessageBox.question(self, "Cancel Estimation", 
                                       "Are you sure you want to stop the size estimation?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.btn_cancel.setEnabled(False)
                self.progress_label.setText("Status: Cancelling estimation...")
                self.estimate_worker.cancel()
        
        elif self.migrate_worker and self.migrate_worker.isRunning():
            reply = QMessageBox.question(self, "Cancel Migration", 
                                       "Are you sure you want to stop the migration?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.btn_cancel.setEnabled(False)
                self.progress_label.setText("Status: Cancelling migration...")
                self.migrate_worker.cancel()

        elif self.compress_worker and self.compress_worker.isRunning():
            reply = QMessageBox.question(self, "Cancel Compression", 
                                       "Are you sure you want to stop the compression?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.btn_cancel.setEnabled(False)
                self.progress_label.setText("Status: Cancelling compression...")
                self.compress_worker.cancel()

    def closeEvent(self, event):
        active_tasks = (
            (self.archive_worker and self.archive_worker.isRunning()) or
            (self.estimate_worker and self.estimate_worker.isRunning()) or
            (self.migrate_worker and self.migrate_worker.isRunning()) or
            (self.compress_worker and self.compress_worker.isRunning()) or
            (self.scan_worker and self.scan_worker.isRunning())
        )
        if active_tasks:
            QMessageBox.warning(self, "Running Task", "Please cancel the running operation before closing the app.")
            event.ignore()
        else:
            event.accept()
