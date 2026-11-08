#!/bin/bash
# Construye dist/MacRAR.app: una app de macOS que abre macrar.py con doble clic
# y aparece en "Abrir con" para archivos comprimidos.
set -euo pipefail
cd "$(dirname "$0")"

APP="dist/MacRAR.app"

if ! /usr/bin/python3 -c "import PySide6" 2>/dev/null; then
  echo "Falta PySide6. Instálalo con:  /usr/bin/python3 -m pip install --user PySide6"
  exit 1
fi

rm -rf "$APP" build
mkdir -p dist build

# 1. App de AppleScript que lanza Python.
osacompile -o "$APP" app/launcher.applescript
cp macrar.py "$APP/Contents/Resources/macrar.py"

# 2. Icono (se genera a partir del dibujo de macrar.py).
QT_QPA_PLATFORM=offscreen /usr/bin/python3 app/make_icon.py build/MacRAR.iconset 2>/dev/null
iconutil -c icns build/MacRAR.iconset -o build/MacRAR.icns
PLIST="$APP/Contents/Info.plist"
ICON_NAME=$(plutil -extract CFBundleIconFile raw "$PLIST")
cp build/MacRAR.icns "$APP/Contents/Resources/$ICON_NAME.icns"
rm -f "$APP/Contents/Resources/Assets.car"
plutil -remove CFBundleIconName "$PLIST" >/dev/null 2>&1 || true

# 3. Nombre, identificador y tipos de archivo que puede abrir.
plutil -replace CFBundleName -string "MacRAR" "$PLIST"
plutil -replace CFBundleIdentifier -string "io.github.ericcgame.macrar" "$PLIST"
plutil -replace CFBundleDocumentTypes -json '[{
  "CFBundleTypeName": "Archivo comprimido",
  "CFBundleTypeRole": "Viewer",
  "LSHandlerRank": "Alternate",
  "CFBundleTypeExtensions": ["rar", "7z", "zip", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz", "iso", "cab", "001"],
  "LSItemContentTypes": ["com.rarlab.rar-archive", "org.7-zip.7-zip-archive", "public.zip-archive",
                         "public.tar-archive", "org.gnu.gnu-zip-archive", "org.gnu.gnu-tar-archive",
                         "public.bzip2-archive", "org.tukaani.xz-archive", "public.iso-image", "public.archive"]
}]' "$PLIST"

# 4. Firma local (ad hoc) para que macOS la abra sin quejarse en este equipo.
codesign --force --deep -s - "$APP"

echo "Listo: $APP"
echo "Puedes arrastrarla a la carpeta Aplicaciones."
