#!/bin/bash
# Removes SmartTimeArchive. Your archives (the disk images and folders you made) are never touched.
set -euo pipefail
prefix="${STA_PREFIX:-/usr/local}"
apps="${STA_APPS:-/Applications}"
lib="$prefix/lib/smarttimearchive"
SUDO="sudo"
[ -n "${STA_NO_SUDO:-}" ] && SUDO=""

[ -e "$lib/.smarttimearchive" ] || { echo "SmartTimeArchive is not installed in $lib."; exit 1; }
[ -z "$SUDO" ] || sudo -v
$SUDO rm -rf "$apps/SmartTimeArchive.app" "$prefix/bin/sta" "$lib"
echo "SmartTimeArchive was removed."

cache="$HOME/Library/Caches/sta"
if [ -d "$cache" ]; then
  size="$(du -sh "$cache" | cut -f1)"
  printf 'Also delete the scan cache (%s in %s)? It only makes the next scan faster. [y/N] ' "$size" "$cache"
  read -r answer || answer=""
  case "$answer" in
    [yY]*) rm -rf "$cache"; echo "Cache deleted." ;;
    *) echo "Cache kept." ;;
  esac
fi
