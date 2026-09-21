#!/bin/bash
# SmartTimeArchive installer (from source). Run it from the project folder:  ./install.sh
#
# Installs:
#   /usr/local/lib/smarttimearchive   the engine + its own Python environment (owned by root:
#                                     the engine runs as administrator, so nobody but an
#                                     administrator may be able to change this code)
#   /usr/local/bin/sta                the command  (sta, sta --tmux, sta plan|extract|verify ...)
#   /Applications/SmartTimeArchive.app  double-click: opens Terminal at the right size
#
# Needs: macOS 11+ (Intel or Apple silicon), Python 3.9+ (the one that comes with the Command Line Tools is fine),
# an internet connection once (to fetch Textual) and an administrator password.
# STA_PREFIX / STA_APPS / STA_NO_SUDO only exist so the installer itself can be tested.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
prefix="${STA_PREFIX:-/usr/local}"
apps="${STA_APPS:-/Applications}"
lib="$prefix/lib/smarttimearchive"
bin="$prefix/bin/sta"
app="$apps/SmartTimeArchive.app"
SUDO="sudo"
[ -n "${STA_NO_SUDO:-}" ] && SUDO=""

say() { printf '%s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

[ "$(uname)" = "Darwin" ] || die "SmartTimeArchive works only on macOS."
macos="$(sw_vers -productVersion 2>/dev/null || echo 0)"
[ "${macos%%.*}" -ge 11 ] 2>/dev/null || die "macOS 11 (Big Sur) or newer is required: Time Machine
saves APFS backups, the only kind this tool reads, since macOS 11 (you have $macos)."
[ -d "$here/sta" ] && [ -d "$here/packaging" ] || die "run this script from the SmartTimeArchive folder (sta/ not found)."

# --- a Python 3.9+ ---------------------------------------------------------------------
py=""
for c in "${STA_PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -n "$c" ] && [ -x "$c" ] || continue
  if [ "$c" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then continue; fi
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    py="$c"; break
  fi
done
[ -n "$py" ] || die "Python 3.9 or newer was not found.
Get one of these and run this installer again:
  - Apple's Command Line Tools (macOS 13 or newer include a suitable Python):
        xcode-select --install
  - or Python from https://www.python.org/downloads/macos/  (any 3.9 - 3.14),
  - or Homebrew:  brew install python"
version="$("$py" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
say "Using Python $version ($py)"

say "Administrator access is needed once to install the engine where only an administrator"
say "can change it."
if [ -n "$SUDO" ] && ! sudo -n true 2>/dev/null; then
  [ -t 0 ] || die "this needs a real Terminal window to ask for your password.
Open the Terminal app, go to the project folder and run ./install.sh there."
fi
[ -z "$SUDO" ] || sudo -v

# --- the engine ------------------------------------------------------------------------
marker="$lib/.smarttimearchive"
if [ -e "$lib" ] && [ ! -e "$marker" ]; then
  die "$lib exists and is not a SmartTimeArchive install: not touching it."
fi
say "Installing the engine in $lib ..."
$SUDO rm -rf "$lib"
$SUDO mkdir -p "$lib"
$SUDO rsync -a --exclude '__pycache__' --exclude '*.pyc' "$here/sta" "$lib/"
$SUDO cp "$here/requirements.txt" "$lib/requirements.txt"
$SUDO "$py" -m venv "$lib/venv"
say "Fetching the terminal UI library (Textual) ..."
$SUDO "$lib/venv/bin/python" -m pip install --quiet --disable-pip-version-check --no-cache-dir \
  -r "$lib/requirements.txt"
version_app="$(PYTHONPATH="$lib" "$lib/venv/bin/python" -c 'import sta; print(sta.__version__)')"
printf '%s\n' "$version_app" | $SUDO tee "$marker" >/dev/null

# --- the command, the terminal launcher and the app (same templates the .pkg uses) ----------
render() { sed -e "s|@LIB@|$lib|g" -e "s|@BIN@|$bin|g" -e "s|@VERSION@|$version_app|g" "$1"; }
pk="$here/packaging"

render "$pk/launch.command.in" | $SUDO tee "$lib/launch.command" >/dev/null
$SUDO chmod 755 "$lib/launch.command"

$SUDO mkdir -p "$prefix/bin"
render "$pk/sta.in" | $SUDO tee "$bin" >/dev/null
$SUDO chmod 755 "$bin"

say "Creating $app ..."
$SUDO rm -rf "$app"
$SUDO mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
[ -f "$here/app_icon.icns" ] && $SUDO cp "$here/app_icon.icns" "$app/Contents/Resources/app_icon.icns"
render "$pk/Info.plist.in" | $SUDO tee "$app/Contents/Info.plist" >/dev/null
render "$pk/app-exec.in" | $SUDO tee "$app/Contents/MacOS/SmartTimeArchive" >/dev/null
$SUDO chmod 755 "$app/Contents/MacOS/SmartTimeArchive"
$SUDO touch "$app"

$SUDO cp "$here/uninstall.sh" "$lib/uninstall.sh"
$SUDO chmod 755 "$lib/uninstall.sh"

# The engine runs as administrator: nothing in it may belong to (or be writable by) a normal user.
# (rsync -a copies the owner of the source files, so this must be enforced explicitly.)
if [ -n "$SUDO" ]; then
  $SUDO chown -R root:wheel "$lib" "$bin" "$app"
  $SUDO chmod -R go-w "$lib" "$bin" "$app"
  bad="$(find "$lib" "$bin" \( ! -user root -o -perm -0002 -o -perm -0020 \) 2>/dev/null | head -1)"
  [ -z "$bad" ] || die "internal check failed: $bad is not owned only by root."
fi

say ""
say "SmartTimeArchive $version_app is installed."
say "  Open it:      double-click SmartTimeArchive in Applications (or Launchpad)"
say "  From Terminal: sta          (or  sta --tmux  to keep it running if the window closes)"
say "  Remove it:    $lib/uninstall.sh"
