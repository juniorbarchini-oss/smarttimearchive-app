import os
import sys
import time
import errno
import shutil
import tarfile
import re
import plistlib
import subprocess

def get_device_node(path):
    """Retrieves the device node (e.g. /dev/disk5s2) for a given mount path."""
    try:
        res = subprocess.run(["diskutil", "info", path], capture_output=True, text=True, check=True)
        for line in res.stdout.splitlines():
            if "Device Node:" in line or "Device Identifier:" in line:
                node = line.split(":", 1)[1].strip()
                if not node.startswith("/dev/"):
                    node = "/dev/" + node
                return node
    except Exception:
        pass
    return None

def get_apfs_snapshots(path):
    """Lists APFS snapshots on the volume containing the path."""
    device = get_device_node(path)
    if not device:
        return []
    try:
        res = subprocess.run(["diskutil", "apfs", "listSnapshots", path], capture_output=True, text=True, check=True)
        names = re.findall(r'Name:\s+([^\s\n\r]+)', res.stdout)
        return sorted(names)
    except Exception:
        pass
    return []

def mount_apfs_snapshot(snapshot_name, device, mount_point):
    """Mounts a specific APFS snapshot to the mount point as read-only."""
    os.makedirs(mount_point, exist_ok=True)
    res = subprocess.run(["mount_apfs", "-s", snapshot_name, device, mount_point], capture_output=True, text=True)
    return res.returncode == 0

def unmount_apfs_snapshot(mount_point):
    """Unmounts a mounted APFS snapshot."""
    subprocess.run(["umount", mount_point], capture_output=True)
    try:
        os.rmdir(mount_point)
    except Exception:
        pass

def attach_sparsebundle(sparsebundle_path):
    """
    Attaches a sparsebundle image and returns the mount point.
    Returns (mount_point, success_flag)
    """
    try:
        res = subprocess.run(["hdiutil", "attach", "-plist", sparsebundle_path], capture_output=True, text=True, check=True)
        data = plistlib.loads(res.stdout.encode('utf-8'))
        mount_point = None
        for entity in data.get("system-entities", []):
            if "mount-point" in entity:
                mount_point = entity["mount-point"]
                break
        if mount_point:
            return mount_point, True
    except Exception:
        pass
    return None, False

def detach_sparsebundle(mount_point):
    """Detaches a mounted sparsebundle volume."""
    try:
        subprocess.run(["hdiutil", "detach", mount_point], capture_output=True, check=True)
        return True
    except Exception:
        return False

def robust_copy2(src, dest, progress_bytes_callback=None):
    """
    Copies a file, verifying if it already exists on the destination with the
    same size and modification time (rsync Quick-Check). If it matches, skips copying.
    Also handles EINVAL/EPERM metadata errors gracefully.
    """
    try:
        src_stat = os.lstat(src)
        
        # Incremental Quick-Check: size and modification time
        if os.path.exists(dest) and not os.path.islink(dest):
            try:
                dest_stat = os.lstat(dest)
                if dest_stat.st_size == src_stat.st_size and abs(dest_stat.st_mtime - src_stat.st_mtime) < 1.0:
                    # Skip copying, file is identical!
                    return False
            except OSError:
                pass

        shutil.copy2(src, dest, follow_symlinks=False)
        if progress_bytes_callback:
            progress_bytes_callback(src_stat.st_size)
        return True
    except OSError as e:
        if os.path.exists(dest) or os.path.islink(dest):
            return False
        if not os.path.islink(src):
            try:
                shutil.copyfile(src, dest, follow_symlinks=False)
                if progress_bytes_callback:
                    progress_bytes_callback(src_stat.st_size)
                return True
            except Exception:
                raise e
        else:
            raise e


class ArchiveEngine:
    def __init__(self, source_dir, dest_dir, exclusions=None, output_tar=False, target_user=None, included_folders=None):
        self.source_dir = source_dir.rstrip("/") if source_dir else ""
        self.dest_dir = dest_dir.rstrip("/") if dest_dir else ""
        self.exclusions = exclusions if exclusions else []
        self.output_tar = output_tar
        self.target_user = target_user
        self.included_folders = included_folders if included_folders else []
        
        self.total_physical_bytes_written = 0
        
        # Sparsebundle tracking
        self.sparsebundle_mount = None
        self.is_sparsebundle = False
        
        if self.source_dir and self.source_dir.endswith(".sparsebundle"):
            self.is_sparsebundle = True

        # Compile exclusion patterns
        self.compiled_exclusions = []
        for pattern in self.exclusions:
            regex_pat = re.escape(pattern).replace(r'\*', '.*')
            self.compiled_exclusions.append(re.compile(regex_pat, re.IGNORECASE))

    def setup_source(self, log_callback=None):
        """Attaches sparsebundle if necessary."""
        if self.is_sparsebundle:
            if log_callback:
                log_callback(f"Attaching network sparsebundle: {self.source_dir}")
            mnt, success = attach_sparsebundle(self.source_dir)
            if success:
                self.sparsebundle_mount = mnt
                if log_callback:
                    log_callback(f"Attached sparsebundle successfully to: {mnt}")
                return mnt
            else:
                if log_callback:
                    log_callback("Error: Failed to attach network sparsebundle.")
                return None
        return self.source_dir

    def cleanup_source(self, log_callback=None):
        """Detaches sparsebundle if attached."""
        if self.sparsebundle_mount:
            if log_callback:
                log_callback(f"Detaching network sparsebundle: {self.sparsebundle_mount}")
            detach_sparsebundle(self.sparsebundle_mount)
            self.sparsebundle_mount = None

    def is_excluded(self, path):
        for pattern in self.compiled_exclusions:
            if pattern.search(path):
                return True
        return False

    def get_backup_folders(self, active_source_dir=None):
        """
        Returns backup items sorted chronologically.
        Supports APFS Snapshots and directory listings.
        """
        src = active_source_dir if active_source_dir else self.source_dir
        if not src:
            return []

        # 1. Try APFS snapshots
        snapshots = get_apfs_snapshots(src)
        if snapshots:
            return [(snap, "snapshot", snap) for snap in snapshots]

        # 2. Fallback to normal directories
        if not os.path.exists(src):
            return []
        
        folders = []
        for name in os.listdir(src):
            path = os.path.join(src, name)
            if os.path.isdir(path):
                if "com.apple.TimeMachine" in name or re.search(r'\d{4}-\d{2}-\d{2}', name):
                    folders.append((name, "directory", path))
        
        folders.sort(key=lambda x: x[0])
        return folders

    def find_user_root(self, root_path):
        """Locates the target user folder path inside a backup mount point."""
        # Resolve intermediate .backup date directories in APFS TM mounts
        actual_root = root_path
        if os.path.exists(root_path):
            for entry in os.listdir(root_path):
                if entry.endswith(".backup") or re.search(r'\d{4}-\d{2}-\d{2}', entry):
                    p = os.path.join(root_path, entry)
                    if os.path.isdir(p):
                        actual_root = p
                        break

        if not self.target_user:
            return actual_root
            
        possible_paths = [
            os.path.join(actual_root, "Data", "Users", self.target_user),
            os.path.join(actual_root, "Users", self.target_user)
        ]
        for path in possible_paths:
            if os.path.exists(path):
                return path
                
        # Safe Fallback: Search for any user folder except Shared/Guest
        users_dirs = [
            os.path.join(actual_root, "Data", "Users"),
            os.path.join(actual_root, "Users")
        ]
        for u_dir in users_dirs:
            if os.path.exists(u_dir):
                for entry in os.listdir(u_dir):
                    if entry not in ["Shared", "Shared.localized", "Guest", ".localized"] and not entry.startswith("."):
                        p = os.path.join(u_dir, entry)
                        if os.path.isdir(p):
                            return p
                            
        # If no user directory whatsoever is found, return dummy path
        return os.path.join(actual_root, "Data", "Users", "nonexistent_placeholder_user")

    def get_unique_home_folders(self):
        """
        Scans the oldest and newest snapshots to build a unified list of
        home subdirectories, skipping dot-folders, Shared, and Guest.
        """
        active_src = self.setup_source()
        if not active_src:
            return []

        try:
            backup_items = self.get_backup_folders(active_src)
            if not backup_items:
                return []

            folders_set = set()
            device = get_device_node(active_src)

            # We scan the oldest (first) and newest (last) backups
            indices_to_scan = list(set([0, len(backup_items) - 1]))
            
            for idx in indices_to_scan:
                if idx >= len(backup_items):
                    continue
                name, item_type, path_or_snap = backup_items[idx]
                folder_path = None
                is_mounted = False
                
                if item_type == "snapshot":
                    mount_point = f"/tmp/smarttimearchive_mounts_scan_{name}"
                    if mount_apfs_snapshot(name, device, mount_point):
                        folder_path = mount_point
                        is_mounted = True
                    else:
                        continue
                else:
                    folder_path = path_or_snap
                    
                user_root = self.find_user_root(folder_path)
                if os.path.exists(user_root):
                    try:
                        for entry in os.listdir(user_root):
                            if not entry.startswith(".") and os.path.isdir(os.path.join(user_root, entry)):
                                folders_set.add(entry)
                    except Exception:
                        pass

                if is_mounted:
                    unmount_apfs_snapshot(folder_path)

            return sorted(list(folders_set))
        finally:
            self.cleanup_source()

    def increment_physical_bytes(self, size):
        self.total_physical_bytes_written += size

    def estimate_size(self, progress_callback=None, cancel_check=None):
        """
        Walks the source backup targets, mounts snapshots,
        applies user filters and exclusions, and estimates size.
        Skips all hidden dotfiles/dotfolders.
        """
        active_src = self.setup_source()
        if not active_src:
            return 0, 0, 0

        try:
            backup_items = self.get_backup_folders(active_src)
            if not backup_items:
                return 0, 0, 0

            device = get_device_node(active_src)
            total_size = 0
            unique_files = 0
            total_folders = 0
            copied_inodes = set()
            
            for item_idx, (name, item_type, path_or_snap) in enumerate(backup_items):
                if cancel_check and cancel_check():
                    break

                if progress_callback:
                    progress_callback(item_idx, len(backup_items), name)
                    
                folder_path = None
                is_mounted = False
                
                if item_type == "snapshot":
                    mount_point = f"/tmp/smarttimearchive_mounts/{name}"
                    if mount_apfs_snapshot(name, device, mount_point):
                        folder_path = mount_point
                        is_mounted = True
                    else:
                        continue
                else:
                    folder_path = path_or_snap
                    
                user_root = self.find_user_root(folder_path)
                
                # Loop over user-selected folders or walk user_root
                scan_roots = []
                if self.included_folders:
                    for f in self.included_folders:
                        p = os.path.join(user_root, f)
                        if os.path.exists(p):
                            scan_roots.append((f, p))
                else:
                    scan_roots.append(("", user_root))

                for label, s_root in scan_roots:
                    for root, dirs, files in os.walk(s_root):
                        if cancel_check and cancel_check():
                            break

                        # Skip hidden directories starting with '.' and excluded directories
                        dirs[:] = [d for d in dirs if not d.startswith(".") and not self.is_excluded(os.path.join(root, d))]
                        total_folders += len(dirs)
                        
                        for file in files:
                            if cancel_check and cancel_check():
                                break

                            # Skip hidden files
                            if file.startswith("."):
                                continue

                            file_path = os.path.join(root, file)
                            if self.is_excluded(file_path):
                                continue
                                
                            try:
                                stat = os.lstat(file_path)
                                inode = stat.st_ino
                                if inode not in copied_inodes:
                                    copied_inodes.add(inode)
                                    total_size += stat.st_size
                                    unique_files += 1
                            except OSError:
                                continue
                
                if is_mounted:
                    unmount_apfs_snapshot(folder_path)
                            
            return total_size, unique_files, total_folders
        finally:
            self.cleanup_source()

    def run_archive(self, progress_callback=None, log_callback=None, cancel_check=None):
        """
        Extracts user profiles, mounts snapshots, applies deduplication,
        skips dotfiles/dotfolders, and saves files.
        """
        active_src = self.setup_source(log_callback)
        if not active_src:
            return False

        backup_items = self.get_backup_folders(active_src)
        if not backup_items:
            if log_callback:
                log_callback("Error: No backup folders/snapshots found in source.")
            self.cleanup_source(log_callback)
            return False

        if not os.path.exists(self.dest_dir):
            try:
                os.makedirs(self.dest_dir, exist_ok=True)
            except Exception as e:
                if log_callback:
                    log_callback(f"Error creating destination: {e}")
                self.cleanup_source(log_callback)
                return False

        device = get_device_node(active_src)
        log_file_path = os.path.join(self.dest_dir, "smarttimearchive_failed_files.log")
        failed_files_count = 0
        copied_inodes_map = {}
        self.total_physical_bytes_written = 0
        
        if self.output_tar:
            tar_path = os.path.join(self.dest_dir, "smarttimearchive_backup.tar.gz")
            if log_callback:
                log_callback(f"Creating compressed tarball at: {tar_path}")
            tar = tarfile.open(tar_path, "w:gz")
        else:
            tar = None
            if log_callback:
                log_callback(f"Exporting folder structures to: {self.dest_dir}")

        try:
            with open(log_file_path, "w") as log_file:
                log_file.write("--- SmartTimeArchive Bad Sectors / Failed Files Log ---\n")
                log_file.write(f"Source: {self.source_dir}\n")
                log_file.write(f"Start Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

                for item_idx, (name, item_type, path_or_snap) in enumerate(backup_items):
                    if cancel_check and cancel_check():
                        if log_callback:
                            log_callback("Process cancelled by user.")
                        break

                    size_gb = self.total_physical_bytes_written / (1024 * 1024 * 1024)
                    progress_info = f"Date: {name} | Copied: {size_gb:.2f} GB"
                    
                    if progress_callback:
                        progress_callback(item_idx, len(backup_items), progress_info)
                    if log_callback:
                        log_callback(f"Starting backup date: {name} | Rescued Space: {size_gb:.2f} GB")

                    folder_path = None
                    is_mounted = False
                    
                    if item_type == "snapshot":
                        mount_point = f"/tmp/smarttimearchive_mounts/{name}"
                        if mount_apfs_snapshot(name, device, mount_point):
                            folder_path = mount_point
                            is_mounted = True
                        else:
                            if log_callback:
                                log_callback(f"[ERROR] Failed to mount snapshot: {name}")
                            log_file.write(f"[ERROR] Failed to mount snapshot: {name}\n")
                            continue
                    else:
                        folder_path = path_or_snap

                    user_root = self.find_user_root(folder_path)

                    # Determine target subdirectories
                    scan_roots = []
                    if self.included_folders:
                        for f in self.included_folders:
                            p = os.path.join(user_root, f)
                            if os.path.exists(p):
                                scan_roots.append((f, p))
                    else:
                        scan_roots.append(("", user_root))

                    for label, s_root in scan_roots:
                        for root, dirs, files in os.walk(s_root):
                            if cancel_check and cancel_check():
                                break

                            # Exclude directories starting with '.' and excluded paths
                            dirs[:] = [d for d in dirs if not d.startswith(".") and not self.is_excluded(os.path.join(root, d))]

                            if not self.output_tar:
                                # Recreate directories relative to user_root
                                for d in dirs:
                                    rel_path = os.path.relpath(os.path.join(root, d), user_root)
                                    dest_sub_dir = os.path.join(self.dest_dir, name, rel_path)
                                    os.makedirs(dest_sub_dir, exist_ok=True)

                            for file in files:
                                if cancel_check and cancel_check():
                                    break

                                # Skip hidden files
                                if file.startswith("."):
                                    continue

                                file_path = os.path.join(root, file)
                                if self.is_excluded(file_path):
                                    continue

                                rel_file_path = os.path.relpath(file_path, user_root)
                                dest_file_path = os.path.join(self.dest_dir, name, rel_file_path)

                                try:
                                    stat = os.lstat(file_path)
                                    inode = stat.st_ino

                                    if self.output_tar:
                                        tar_name = os.path.join(name, rel_file_path)
                                        if inode in copied_inodes_map:
                                            existing_tar_name = copied_inodes_map[inode]
                                            tarinfo = tarfile.TarInfo(name=tar_name)
                                            tarinfo.type = tarfile.LNKTYPE
                                            tarinfo.linkname = existing_tar_name
                                            tarinfo.size = 0
                                            tar.addfile(tarinfo)
                                        else:
                                            tar.add(file_path, arcname=tar_name, recursive=False)
                                            copied_inodes_map[inode] = tar_name
                                            self.total_physical_bytes_written += stat.st_size
                                    else:
                                        dest_dir_only = os.path.dirname(dest_file_path)
                                        os.makedirs(dest_dir_only, exist_ok=True)

                                        # Quick-check for existing files before hard linking/copying
                                        if os.path.exists(dest_file_path) and not os.path.islink(dest_file_path):
                                            try:
                                                dest_stat = os.lstat(dest_file_path)
                                                if dest_stat.st_size == stat.st_size and abs(dest_stat.st_mtime - stat.st_mtime) < 1.0:
                                                    # Link is already there or matches exactly. Skip!
                                                    continue
                                            except OSError:
                                                pass

                                        if inode in copied_inodes_map:
                                            existing_dest_path = copied_inodes_map[inode]
                                            try:
                                                os.link(existing_dest_path, dest_file_path)
                                            except OSError:
                                                robust_copy2(file_path, dest_file_path, self.increment_physical_bytes)
                                                copied_inodes_map[inode] = dest_file_path
                                        else:
                                            robust_copy2(file_path, dest_file_path, self.increment_physical_bytes)
                                            copied_inodes_map[inode] = dest_file_path

                                except OSError as e:
                                    failed_files_count += 1
                                    if e.errno == errno.EIO:
                                        err_msg = f"[I/O ERROR] Bad sector on file: {file_path}"
                                    else:
                                        err_msg = f"[OS ERROR {e.errno}] {e.strerror}: {file_path}"
                                    if log_callback:
                                        log_callback(err_msg)
                                    log_file.write(err_msg + "\n")
                                except Exception as e:
                                    failed_files_count += 1
                                    err_msg = f"[ERROR] {str(e)}: {file_path}"
                                    if log_callback:
                                        log_callback(err_msg)
                                    log_file.write(err_msg + "\n")

                    if is_mounted:
                        unmount_apfs_snapshot(folder_path)

                log_file.write(f"\nEnd Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                log_file.write(f"Total files that failed: {failed_files_count}\n")
                
            if self.output_tar and tar:
                tar.close()

            if log_callback:
                log_callback(f"Process finished. Total files failed: {failed_files_count}")
                log_callback(f"Check error log at: {log_file_path}")
            
            self.cleanup_source(log_callback)
            return True

        except Exception as e:
            if self.output_tar and tar:
                tar.close()
            if log_callback:
                log_callback(f"Fatal error during execution: {e}")
            self.cleanup_source(log_callback)
            return False


# --- Static Utility Migration/Compression Methods ---

def migrate_archive(src_dir, dest_dir, progress_callback=None, log_callback=None, cancel_check=None):
    """
    Copies a previously completed archive folder structure from src_dir to dest_dir,
    re-applying hard links and using file-level progress reporting.
    """
    if not os.path.exists(src_dir):
        if log_callback:
            log_callback(f"Error: Source archive folder does not exist: {src_dir}")
        return False

    try:
        os.makedirs(dest_dir, exist_ok=True)
    except Exception as e:
        if log_callback:
            log_callback(f"Error creating destination folder: {e}")
        return False

    copied_inodes_map = {}
    total_folders_to_copy = []
    
    for name in os.listdir(src_dir):
        path = os.path.join(src_dir, name)
        if os.path.isdir(path) and not name.startswith("."):
            total_folders_to_copy.append((name, path))
            
    total_folders_to_copy.sort(key=lambda x: x[0])
    
    if not total_folders_to_copy:
        if log_callback:
            log_callback("Error: No archive folders found to migrate.")
        return False

    if log_callback:
        log_callback("Scanning files in source archive to calculate total count...")
    total_files_count = 0
    for folder_name, folder_path in total_folders_to_copy:
        for root, dirs, files in os.walk(folder_path):
            total_files_count += len(files)

    if log_callback:
        log_callback(f"Starting migration of {total_files_count:,} files inside {len(total_folders_to_copy)} folders...")

    copied_files_count = 0
    last_percent = -1

    try:
        for folder_idx, (folder_name, folder_path) in enumerate(total_folders_to_copy):
            if cancel_check and cancel_check():
                if log_callback:
                    log_callback("Migration cancelled by user.")
                return False

            if log_callback:
                log_callback(f"Migrating folder: {folder_name}")

            for root, dirs, files in os.walk(folder_path):
                if cancel_check and cancel_check():
                    break

                for d in dirs:
                    rel_path = os.path.relpath(os.path.join(root, d), folder_path)
                    dest_sub = os.path.join(dest_dir, folder_name, rel_path)
                    os.makedirs(dest_sub, exist_ok=True)

                for file in files:
                    if cancel_check and cancel_check():
                        break

                    src_file = os.path.join(root, file)
                    rel_file = os.path.relpath(src_file, folder_path)
                    dest_file = os.path.join(dest_dir, folder_name, rel_file)

                    try:
                        stat = os.lstat(src_file)
                        inode = stat.st_ino

                        dest_dir_only = os.path.dirname(dest_file)
                        os.makedirs(dest_dir_only, exist_ok=True)

                        # Quick-check incremental skip
                        if os.path.exists(dest_file) and not os.path.islink(dest_file):
                            try:
                                dest_stat = os.lstat(dest_file)
                                if dest_stat.st_size == stat.st_size and abs(dest_stat.st_mtime - stat.st_mtime) < 1.0:
                                    copied_files_count += 1
                                    continue
                            except OSError:
                                pass

                        if inode in copied_inodes_map:
                            existing_dest = copied_inodes_map[inode]
                            try:
                                os.link(existing_dest, dest_file)
                            except OSError:
                                robust_copy2(src_file, dest_file)
                                copied_inodes_map[inode] = dest_file
                        else:
                            robust_copy2(src_file, dest_file)
                            copied_inodes_map[inode] = dest_file

                    except Exception as e:
                        if log_callback:
                            log_callback(f"[ERROR] Failed to migrate file {src_file}: {e}")

                    copied_files_count += 1
                    if total_files_count > 0:
                        percent = int((copied_files_count / total_files_count) * 100)
                        if percent != last_percent or copied_files_count % 200 == 0:
                            if progress_callback:
                                progress_callback(copied_files_count, total_files_count, f"{folder_name} (File {copied_files_count:,}/{total_files_count:,})")
                            last_percent = percent

        if log_callback:
            log_callback("Migration completed successfully!")
        return True
    except Exception as e:
        if log_callback:
            log_callback(f"Fatal error during migration: {e}")
        return False


def compress_archive_to_tar(src_dir, dest_tar_path, progress_callback=None, log_callback=None, cancel_check=None):
    """
    Compresses an existing archive folder structure into a deduplicated .tar.gz file.
    """
    if not os.path.exists(src_dir):
        if log_callback:
            log_callback(f"Error: Source archive folder does not exist: {src_dir}")
        return False

    total_folders_to_compress = []
    for name in os.listdir(src_dir):
        path = os.path.join(src_dir, name)
        if os.path.isdir(path) and not name.startswith("."):
            total_folders_to_compress.append((name, path))
            
    total_folders_to_compress.sort(key=lambda x: x[0])
    
    if not total_folders_to_compress:
        if log_callback:
            log_callback("Error: No archive folders found to compress.")
        return False

    if log_callback:
        log_callback("Scanning files in source folder to calculate total count...")
    total_files_count = 0
    for folder_name, folder_path in total_folders_to_compress:
        for root, dirs, files in os.walk(folder_path):
            total_files_count += len(files)

    if log_callback:
        log_callback(f"Compressing {total_files_count:,} files inside {len(total_folders_to_compress)} folders to: {dest_tar_path}")

    copied_inodes_map = {}
    copied_files_count = 0
    last_percent = -1

    try:
        tar_dir = os.path.dirname(dest_tar_path)
        if tar_dir:
            os.makedirs(tar_dir, exist_ok=True)
            
        with tarfile.open(dest_tar_path, "w:gz") as tar:
            for idx, (folder_name, folder_path) in enumerate(total_folders_to_compress):
                if cancel_check and cancel_check():
                    if log_callback:
                        log_callback("Compression cancelled.")
                    return False

                if log_callback:
                    log_callback(f"Compressing folder: {folder_name}")

                for root, dirs, files in os.walk(folder_path):
                    if cancel_check and cancel_check():
                        break

                    for file in files:
                        if cancel_check and cancel_check():
                            break

                        src_file = os.path.join(root, file)
                        rel_file = os.path.relpath(src_file, folder_path)
                        tar_name = os.path.join(folder_name, rel_file)

                        try:
                            stat = os.lstat(src_file)
                            inode = stat.st_ino

                            if inode in copied_inodes_map:
                                existing_tar_name = copied_inodes_map[inode]
                                tarinfo = tarfile.TarInfo(name=tar_name)
                                tarinfo.type = tarfile.LNKTYPE
                                tarinfo.linkname = existing_tar_name
                                tarinfo.size = 0
                                tar.addfile(tarinfo)
                            else:
                                tar.add(src_file, arcname=tar_name, recursive=False)
                                copied_inodes_map[inode] = tar_name
                        except Exception as e:
                            if log_callback:
                                log_callback(f"[ERROR] Failed to add file {src_file} to tar: {e}")

                        copied_files_count += 1
                        if total_files_count > 0:
                            percent = int((copied_files_count / total_files_count) * 100)
                            if percent != last_percent or copied_files_count % 200 == 0:
                                if progress_callback:
                                    progress_callback(copied_files_count, total_files_count, f"{folder_name} (File {copied_files_count:,}/{total_files_count:,})")
                                last_percent = percent

        if log_callback:
            log_callback("Compression completed successfully!")
        return True
    except Exception as e:
        if log_callback:
            log_callback(f"Fatal error during compression: {e}")
        return False
