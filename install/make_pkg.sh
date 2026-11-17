#!/bin/bash
# Builds the macOS installer package that puts FiberView.app into /Applications.
#   bash install/make_pkg.sh <version> <AppleSilicon|Intel> <hostArchitectures>
# Input: dist/FiberView.app (PyInstaller)   Output: dist/FiberView-<version>-macOS-<label>.pkg
set -euo pipefail
VERSION="$1"; LABEL="$2"; HOSTARCH="${3:-arm64}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/dist/FiberView.app"
WORK="$ROOT/build/pkg"
OUT="$ROOT/dist/FiberView-${VERSION}-macOS-${LABEL}.pkg"
[ -d "$APP" ] || { echo "missing $APP - run PyInstaller first"; exit 1; }

rm -rf "$WORK"; mkdir -p "$WORK/root"
ditto "$APP" "$WORK/root/FiberView.app"

# always install to /Applications, even if an older copy sits elsewhere
pkgbuild --analyze --root "$WORK/root" "$WORK/component.plist"
plutil -replace 0.BundleIsRelocatable -bool NO "$WORK/component.plist"

pkgbuild --root "$WORK/root" \
         --component-plist "$WORK/component.plist" \
         --identifier hu.pte.fiberview \
         --version "$VERSION" \
         --install-location /Applications \
         "$WORK/FiberView-app.pkg"

cat > "$WORK/distribution.xml" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>FiberView ${VERSION}</title>
    <options customize="never" require-scripts="false" hostArchitectures="${HOSTARCH}"/>
    <domains enable_localSystem="true"/>
    <choices-outline><line choice="default"><line choice="hu.pte.fiberview"/></line></choices-outline>
    <choice id="default"/>
    <choice id="hu.pte.fiberview" visible="false"><pkg-ref id="hu.pte.fiberview"/></choice>
    <pkg-ref id="hu.pte.fiberview" version="${VERSION}" onConclusion="none">FiberView-app.pkg</pkg-ref>
</installer-gui-script>
EOF

productbuild --distribution "$WORK/distribution.xml" --package-path "$WORK" "$OUT"
pkgutil --payload-files "$OUT" | head -5
ls -la "$OUT"
