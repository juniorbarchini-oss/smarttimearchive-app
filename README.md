# SmartTimeArchive 📂⏱️

[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/framework-PySide6%20%2F%20Qt6-green.svg)](https://pypi.org/project/PySide6/)
[![macOS Compatible](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://apple.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**SmartTimeArchive** is a premium, lightweight, and native-feeling macOS desktop utility designed to scan, rescue, and consolidate user profiles from damaged, legacy, or network Time Machine backups. 

By leveraging native macOS APIs, APFS snapshotting, and directory-level hard link analysis, it successfully pulls back your data without duplicating physical storage space, resolving the extreme latencies of bad sectors or network lags.

---

## ☕ Support the Project (Buy Me a Coffee)

If this tool saved your precious files, rescued a damaged USB drive, or preserved your deduplicated network backups, consider showing some love!

[![Ko-fi Support](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/hbarchini)

---

## 🌟 Key Features

*   **⚡ Smart Hard Link Deduplication (APFS Native):** Keeps files linked across chronological snapshots, exactly like Apple's Time Machine. Copying 10 days of backup snapshots won't multiply your disk usage!
*   **🔌 Sparsebundle Network Mounting:** Automatically attaches, mounts, and cleans up remote `.sparsebundle` files (e.g. from ZimaOS, Synology, or SMB shares) directly within the UI.
*   **🎛️ Selective Rescues:** Choose exactly which directories (`Desktop`, `Documents`, `Pictures`, etc.) and which specific backup dates you want to extract.
*   **🚀 Zero Terminal Dependency (Touch ID / Admin Self-Elevation):** Launches with native macOS security prompts. Just double-click the `.app` bundle, authenticate with Touch ID, and run the tool as an administrator natively.
*   **🌗 Premium Dark Mode Interface:** Permanent slate-gray, high-contrast dark theme designed to match macOS Sonoma/Sequoia vibes perfectly.
*   **📦 Compilations & Tarballs:** Choose between extracting as reference folders or compressing everything into a single `.tar.gz` archive with full hard-link preservation.
*   **📊 Live Progress Dashboard:** Real-time stats on files processed, physical data written, active directory paths, and error counters for damaged storage sectors.

---

## 🛠️ How it Works under the Hood

```
[Time Machine Backup]
   ├── Local APFS Snapshot Volume
   └── Remote Network .sparsebundle
         │
         ▼ (Auto-mounts via hdiutil & mount_apfs)
   [SmartTimeArchive GUI] ◄─── (User Selects Folders & Dates)
         │
         ▼ (Calculates st_ino inodes in memory)
   [Deduplicated Copy Engine]
         ├── If new file: copy bytes
         └── If duplicate: link physically via os.link
         │
         ▼
   [Target SSD APFS Directory]
```

---

## 📦 How to Compile and Bundle Nativity

If you want to compile and build the standalone macOS `.app` yourself:

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/hbarchini/smarttimearchive-app.git
    cd smarttimearchive-app
    ```

2.  **Create and Activate Virtual Environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install Dependencies:**
    ```bash
    pip install PySide6 pyinstaller
    ```

4.  **Bundle into a Native App:**
    Use PyInstaller to compile it with its high-res icon:
    ```bash
    pyinstaller --windowed --noconfirm --clean --icon=app_icon.icns --name="SmartTimeArchive" main.py
    ```

The compiled `SmartTimeArchive.app` bundle will be ready in the `dist/` directory!

---

## 🚀 Running the App

### Script Mode
```bash
sudo venv/bin/python main.py
```

### Standalone App Bundle (.app)
1. Go to the `dist/` folder in Finder.
2. Double-click `SmartTimeArchive.app`.
3. Authenticate with Touch ID or enter your user password when prompted.
4. Scan and start rescuing your data!

---

## 📁 Repository Structure

*   `main.py`: Entry point, stylesheet configuration, and admin elevation check.
*   `engine.py`: Core backup parser, APFS snapshot mounter, and deduplicated copy engine.
*   `ui/main_window.py`: PySide6 window layout, dynamic checklists, and stats widgets.
*   `ui/worker.py`: Background worker threads for non-blocking UI scans and copies.
*   `app_icon.icns`: High-resolution Sonoma-style application icon.

---

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🏷️ Tags
`backup` | `time-machine` | `apfs` | `snapshot` | `deduplication` | `hard-links` | `sparsebundle` | `recovery` | `macOS` | `pyside6` | `pyinstaller` | `gui`
