from PySide6.QtCore import QThread, Signal
from engine import ArchiveEngine, migrate_archive, compress_archive_to_tar

class EstimateWorker(QThread):
    progress_sig = Signal(int, int, str)  # folder_idx, total_folders, folder_name
    finished_sig = Signal(float, int, int)  # total_size (float to prevent overflow), file_count, folder_count
    error_sig = Signal(str)

    def __init__(self, source_dir, exclusions, target_user, included_folders=None):
        super().__init__()
        self.source_dir = source_dir
        self.exclusions = exclusions
        self.target_user = target_user
        self.included_folders = included_folders if included_folders else []
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        try:
            engine = ArchiveEngine(
                self.source_dir, 
                "", 
                self.exclusions, 
                target_user=self.target_user,
                included_folders=self.included_folders
            )
            
            def progress_callback(folder_idx, total_folders, folder_name):
                self.progress_sig.emit(folder_idx, total_folders, folder_name)
                
            def cancel_check():
                return self.is_cancelled
                
            total_size, file_count, folder_count = engine.estimate_size(
                progress_callback=progress_callback,
                cancel_check=cancel_check
            )
            self.finished_sig.emit(float(total_size), file_count, folder_count)
        except Exception as e:
            self.error_sig.emit(str(e))


class ArchiveWorker(QThread):
    progress_sig = Signal(int, int, str)  # folder_idx, total_folders, folder_name
    log_sig = Signal(str)
    finished_sig = Signal(bool)

    def __init__(self, source_dir, dest_dir, exclusions, output_tar, target_user, included_folders=None):
        super().__init__()
        self.source_dir = source_dir
        self.dest_dir = dest_dir
        self.exclusions = exclusions
        self.output_tar = output_tar
        self.target_user = target_user
        self.included_folders = included_folders if included_folders else []
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        try:
            engine = ArchiveEngine(
                self.source_dir, 
                self.dest_dir, 
                self.exclusions, 
                self.output_tar, 
                target_user=self.target_user,
                included_folders=self.included_folders
            )
            
            def progress_callback(folder_idx, total_folders, folder_name):
                self.progress_sig.emit(folder_idx, total_folders, folder_name)
                
            def log_callback(msg):
                self.log_sig.emit(msg)
                
            def cancel_check():
                return self.is_cancelled
                
            success = engine.run_archive(
                progress_callback=progress_callback, 
                log_callback=log_callback, 
                cancel_check=cancel_check
            )
            self.finished_sig.emit(success)
        except Exception as e:
            self.log_sig.emit(f"Worker Error: {e}")
            self.finished_sig.emit(False)


class MigrateWorker(QThread):
    progress_sig = Signal(int, int, str)
    log_sig = Signal(str)
    finished_sig = Signal(bool)

    def __init__(self, src_dir, dest_dir):
        super().__init__()
        self.src_dir = src_dir
        self.dest_dir = dest_dir
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        try:
            def progress_callback(folder_idx, total_folders, folder_name):
                self.progress_sig.emit(folder_idx, total_folders, folder_name)
                
            def log_callback(msg):
                self.log_sig.emit(msg)
                
            def cancel_check():
                return self.is_cancelled

            success = migrate_archive(
                self.src_dir,
                self.dest_dir,
                progress_callback=progress_callback,
                log_callback=log_callback,
                cancel_check=cancel_check
            )
            self.finished_sig.emit(success)
        except Exception as e:
            self.log_sig.emit(f"Migration Error: {e}")
            self.finished_sig.emit(False)


class CompressWorker(QThread):
    progress_sig = Signal(int, int, str)
    log_sig = Signal(str)
    finished_sig = Signal(bool)

    def __init__(self, src_dir, dest_tar_path):
        super().__init__()
        self.src_dir = src_dir
        self.dest_tar_path = dest_tar_path
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        try:
            def progress_callback(folder_idx, total_folders, folder_name):
                self.progress_sig.emit(folder_idx, total_folders, folder_name)
                
            def log_callback(msg):
                self.log_sig.emit(msg)
                
            def cancel_check():
                return self.is_cancelled

            success = compress_archive_to_tar(
                self.src_dir,
                self.dest_tar_path,
                progress_callback=progress_callback,
                log_callback=log_callback,
                cancel_check=cancel_check
            )
            self.finished_sig.emit(success)
        except Exception as e:
            self.log_sig.emit(f"Compression Error: {e}")
            self.finished_sig.emit(False)


class ScanHomeWorker(QThread):
    finished_sig = Signal(list)
    error_sig = Signal(str)

    def __init__(self, source_dir, target_user):
        super().__init__()
        self.source_dir = source_dir
        self.target_user = target_user

    def run(self):
        try:
            from engine import ArchiveEngine
            engine = ArchiveEngine(self.source_dir, "", target_user=self.target_user)
            folders = engine.get_unique_home_folders()
            self.finished_sig.emit(folders)
        except Exception as e:
            self.error_sig.emit(str(e))
