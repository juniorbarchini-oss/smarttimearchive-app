#!/bin/bash
# Builds dist/SmartTimeArchive-<version>.pkg (unsigned): the engine, its own Python 3.12 for both
# Apple silicon and Intel, Textual, the `sta` command and the app. Needs internet the first time
# (Python and Textual are downloaded and cached in build/cache). Run on a Mac:  packaging/build_pkg.sh
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
cd "$here"
version="$(python3 -c 'import re,sys; print(re.search(r"__version__ = \"(.+?)\"", open("sta/__init__.py").read()).group(1))')"
pbs_tag="20260901"
pyver="3.12.14"
work="$here/build/pkg"
cache="$here/build/cache"
lib_rel="usr/local/lib/smarttimearchive"
lib_abs="/usr/local/lib/smarttimearchive"
bin_abs="/usr/local/bin/sta"

echo "Building SmartTimeArchive $version"
rm -rf "$work"; mkdir -p "$work/root/$lib_rel" "$work/root/usr/local/bin" "$work/root/Applications" \
  "$work/scripts" "$cache" "$here/dist"
root="$work/root"

# --- Python for both architectures (python-build-standalone, checked against its checksums) ----
base="https://github.com/astral-sh/python-build-standalone/releases/download/$pbs_tag"
sums="$cache/SHA256SUMS-$pbs_tag"
[ -f "$sums" ] || curl -fsSL -o "$sums" "$base/SHA256SUMS"
for arch in aarch64 x86_64; do
  f="cpython-$pyver+$pbs_tag-$arch-apple-darwin-install_only.tar.gz"
  [ -f "$cache/$f" ] || { echo "Downloading $f"; curl -fSL -o "$cache/$f" "$base/$f"; }
  want="$(grep " $f\$" "$sums" | cut -d' ' -f1)"
  got="$(shasum -a 256 "$cache/$f" | cut -d' ' -f1)"
  [ -n "$want" ] && [ "$want" = "$got" ] || { echo "checksum mismatch for $f" >&2; exit 1; }
  label="arm64"; [ "$arch" = "x86_64" ] && label="x86_64"
  mkdir -p "$work/py-$label" && tar -xzf "$cache/$f" -C "$work/py-$label"
  mv "$work/py-$label/python" "$root/$lib_rel/python-$label"
done

# --- Textual and friends: pure Python, so one copy serves both architectures --------------------
"${STA_BUILD_PYTHON:-python3}" -m pip install --quiet --disable-pip-version-check --no-cache-dir \
  --only-binary=:all: --target "$root/$lib_rel/site-packages" -r requirements.txt
if find "$root/$lib_rel/site-packages" -name "*.so" -o -name "*.dylib" | grep -q .; then
  echo "a dependency ships compiled code: the shared site-packages would not work on both" \
       "architectures" >&2
  exit 1
fi
find "$root/$lib_rel/site-packages" -name "__pycache__" -prune -exec rm -rf {} +

# --- the engine and the rendered launchers ---------------------------------------------------
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' sta "$root/$lib_rel/"
cp requirements.txt uninstall.sh "$root/$lib_rel/"
printf '%s\n' "$version" > "$root/$lib_rel/.smarttimearchive"
render() { sed -e "s|@LIB@|$lib_abs|g" -e "s|@BIN@|$bin_abs|g" -e "s|@VERSION@|$version|g" "$1"; }
render packaging/launch.command.in > "$root/$lib_rel/launch.command"
render packaging/sta.in > "$root/usr/local/bin/sta"
app="$root/Applications/SmartTimeArchive.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp app_icon.icns "$app/Contents/Resources/app_icon.icns"
render packaging/Info.plist.in > "$app/Contents/Info.plist"
render packaging/app-exec.in > "$app/Contents/MacOS/SmartTimeArchive"
chmod 755 "$root/$lib_rel/launch.command" "$root/usr/local/bin/sta" \
  "$app/Contents/MacOS/SmartTimeArchive" "$root/$lib_rel/uninstall.sh"

# --- install scripts ---------------------------------------------------------------------------
cp packaging/preinstall packaging/postinstall "$work/scripts/"
chmod 755 "$work/scripts/preinstall" "$work/scripts/postinstall"

# --- the package -------------------------------------------------------------------------------
id="com.juniorbarchini.smarttimearchive"
pkgbuild --quiet --root "$root" --scripts "$work/scripts" --identifier "$id" --version "$version" \
  --ownership recommended --install-location / "$work/component.pkg"
sed -e "s|@VERSION@|$version|g" -e "s|@ID@|$id|g" packaging/distribution.xml.in > "$work/distribution.xml"
out="$here/dist/SmartTimeArchive-$version.pkg"
productbuild --quiet --distribution "$work/distribution.xml" --package-path "$work" \
  --resources packaging/resources "$out"
echo "Built $out ($(du -h "$out" | cut -f1)) - unsigned"
shasum -a 256 "$out"
