#!/usr/bin/env python3
"""MacRAR: interfaz gráfica (PySide6) para comprimir y extraer archivos en macOS.

Formatos:
  RAR                         -> /Applications/rar/rar
  7z, zip, iso, gz, xz, ...   -> /Applications/7zip/7zz  (7-Zip oficial)
  tar, tar.gz, tgz, tar.xz... -> bsdtar (incluido en macOS)

Requisito:  python3 -m pip install --user PySide6
Uso:        python3 macrar.py [archivo | carpeta]
"""
import codecs
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

from PySide6.QtCore import QEvent, QFileInfo, QProcess, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QDialog, QDialogButtonBox, QFileDialog, QFileIconProvider, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget)


def find_bin(candidates, names):
  for candidate in candidates:
    if os.path.exists(candidate):
      return candidate
  for name in names:
    found = shutil.which(name)
    if found:
      return found
  return None


RAR_BIN = find_bin(("/Applications/rar/rar", "/usr/local/bin/rar", "/opt/homebrew/bin/rar"), ("rar",))
SEVENZIP_BIN = find_bin(("/Applications/7zip/7zz", "/usr/local/bin/7zz", "/opt/homebrew/bin/7zz"), ("7zz", "7z"))
BSDTAR_BIN = find_bin(("/usr/bin/bsdtar",), ("bsdtar", "tar"))

RAR_RE = re.compile(r"\.(rar|r\d\d)$", re.I)
TAR_RE = re.compile(r"\.(tar|tgz|tbz|tbz2|txz|tar\.gz|tar\.bz2|tar\.xz)$", re.I)
SEVENZIP_RE = re.compile(r"\.(7z|zip|zipx|z\d\d|gz|bz2|xz|zst|lzma|iso|cab|arj|lzh|lha|wim|jar|apk|cpio|rpm|deb|xar"
                         r"|(7z|zip|tar|iso)\.\d{3})$", re.I)
PCT_END_RE = re.compile(r"(\d{1,3})%$")
PCT_START_RE = re.compile(r"^(\d{1,3})%(\s+\d+)?\s*")
RAR_FILE_RE = re.compile(r"^(Extracting|Adding|Testing|Updating|Deleting|Creating|\.\.\.)\s+(.+?)(\s+OK)?$")
SEVENZIP_FILE_RE = re.compile(r"^[-+TU=] (.+)$")
PASSWORD_KEYS = ("incorrect password", "wrong password", "enter password")
ERROR_KEYS = ("error", "cannot", "crc failed", "checksum", "corrupt", "unexpected end", "is not rar",
              "no files", "not volume", "access denied", "failed", "not found in archive")
SUMMARY_RE = re.compile(r"^(total errors|sub items errors|archives with errors|errors:)", re.I)
COMPRESSION_LEVELS = [("Almacenar", 0), ("Más rápido", 1), ("Rápido", 2),
                      ("Normal", 3), ("Bueno", 4), ("Mejor", 5)]
SEVENZIP_LEVELS = {0: 0, 1: 1, 2: 3, 3: 5, 4: 7, 5: 9}
SEVENZIP_OVERWRITE = {"-o+": "-aoa", "-o-": "-aos", "-or": "-aou"}
ROLE_ID = Qt.UserRole


# --------------------------------------------------------------------------- utilidades

def human_size(n):
  if n is None:
    return ""
  for unit in ("B", "KB", "MB", "GB", "TB"):
    if n < 1024 or unit == "TB":
      return f"{n:,.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
    n /= 1024


def first_volume(path):
  """Si abren la parte 3 de un multivolumen, empezamos por la parte 1."""
  m = re.match(r"^(.*\.part)(\d+)(\.rar)$", path, re.I)
  if m and int(m.group(2)) != 1:
    candidate = m.group(1) + "1".zfill(len(m.group(2))) + m.group(3)
    if os.path.exists(candidate):
      return candidate
  m = re.match(r"^(.*)\.r\d\d$", path, re.I)
  if m and os.path.exists(m.group(1) + ".rar"):
    return m.group(1) + ".rar"
  m = re.match(r"^(.*)\.(\d{3})$", path)
  if m and int(m.group(2)) != 1 and os.path.exists(m.group(1) + ".001"):
    return m.group(1) + ".001"
  m = re.match(r"^(.*)\.z\d\d$", path, re.I)
  if m and os.path.exists(m.group(1) + ".zip"):
    return m.group(1) + ".zip"
  return path


def archive_basename(path):
  name = os.path.basename(path)
  name = re.sub(r"\.\d{3}$", "", name)
  name = re.sub(r"(\.part\d+)?\.(rar|r\d\d)$", "", name, flags=re.I)
  name = re.sub(r"\.(tar\.gz|tar\.bz2|tar\.xz|tgz|tbz2?|txz|tar|7z|zipx?|z\d\d|gz|bz2|xz|zst|iso)$", "", name,
                flags=re.I)
  return name or os.path.basename(path)


def archive_kind(name):
  m = re.search(r"\.(tar\.gz|tar\.bz2|tar\.xz|[^.]+)$", re.sub(r"\.\d{3}$", "", name), re.I)
  return "Archivo " + (m.group(1).upper() if m else "comprimido")


def open_in_finder(path):
  subprocess.Popen(["open", path])


def run_tool(program, args):
  proc = subprocess.run([program] + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, errors="replace")
  return proc.returncode, proc.stdout


def has_password_error(text):
  low = text.lower()
  return any(k in low for k in PASSWORD_KEYS)


def translate_error(line):
  m = re.match(r"Cannot find volume (.+)", line)
  if m:
    return f"Falta el volumen: {os.path.basename(m.group(1))}"
  m = re.match(r"Incorrect password for (.+)", line)
  if m:
    return f"Contraseña incorrecta: {m.group(1)}"
  m = re.match(r"ERROR: Data Error in encrypted file\. Wrong password\? : (.+)", line)
  if m:
    return f"Contraseña incorrecta: {m.group(1)}"
  m = re.match(r"(.+?)\s+- checksum error", line)
  if m:
    return f"Error de suma de control (archivo dañado o incompleto): {m.group(1)}"
  m = re.match(r"ERROR: (?:CRC Failed|Data Error) : (.+)", line)
  if m:
    return f"Archivo dañado o incompleto: {m.group(1)}"
  m = re.match(r"ERROR: Missing volume : (.+)", line)
  if m:
    return f"Falta el volumen: {os.path.basename(m.group(1))}"
  if "No files to extract" in line:
    return "No hay archivos para extraer"
  if "is not RAR archive" in line:
    return line.replace("is not RAR archive", "no es un archivo RAR")
  if "Cannot open the file as archive" in line:
    return "No se reconoce el formato del archivo (¿dañado o incompleto?)"
  return line


# --------------------------------------------------------------------------- listado

class Node:
  __slots__ = ("name", "path", "is_dir", "size", "packed", "mtime", "enc", "children")

  def __init__(self, name, path, is_dir):
    self.name, self.path, self.is_dir = name, path, is_dir
    self.size = self.packed = 0
    self.mtime = ""
    self.enc = False
    self.children = {}


def new_entry(kind="File"):
  return {"type": kind, "size": 0, "packed": 0, "mtime": "", "enc": False}


def to_int(value):
  try:
    return int(value.split()[0])
  except (ValueError, IndexError):
    return 0


def parse_rar_listing(out):
  """Interpreta la salida de `rar lt -v` (listado técnico de todos los volúmenes)."""
  entries, info = {}, {"volumes": 0, "details": "", "hp": False, "type": "rar"}
  current = None
  for line in out.splitlines():
    m = re.match(r"^\s*([A-Za-z][A-Za-z0-9 \-]*?):\s?(.*)$", line)
    if not m:
      continue
    key, value = m.group(1).strip(), m.group(2).strip()
    if key == "Archive":
      info["volumes"] += 1
      current = None
    elif key == "Details":
      if not info["details"]:
        info["details"] = value
      if "encrypted headers" in value:
        info["hp"] = True
    elif key == "Name":
      current = entries.setdefault(value, new_entry())
    elif current is not None:
      if key == "Type":
        current["type"] = value
      elif key == "Size":
        current["size"] = to_int(value)
      elif key == "Packed size":
        current["packed"] += to_int(value)  # un archivo dividido aparece una vez por volumen
      elif key == "mtime":
        current["mtime"] = value[:16]
      elif key == "Flags":
        current["enc"] = current["enc"] or "encrypted" in value
  return entries, info


def parse_7z_listing(out):
  """Interpreta la salida de `7zz l -slt`."""
  entries, info = {}, {"volumes": 1, "details": "", "hp": False, "type": ""}
  in_files = False
  current = None
  method = ""
  for line in out.splitlines():
    if line.startswith("----------"):
      in_files = True
      continue
    if " = " not in line and not line.endswith(" ="):
      current = None if in_files else current
      continue
    key, _, value = line.partition(" =")
    value = value.strip()
    if not in_files:
      if key == "Type":
        info["type"] = value.lower()
      elif key == "Volumes":
        info["volumes"] = to_int(value) or 1
      elif key == "Method":
        method = value
      continue
    if key == "Path":
      current = entries.setdefault(value, new_entry())
    elif current is not None:
      if key == "Folder" and value == "+":
        current["type"] = "Directory"
      elif key == "Attributes" and value.startswith("D"):
        current["type"] = "Directory"
      elif key == "Size":
        current["size"] = to_int(value)
      elif key == "Packed Size":
        current["packed"] = to_int(value)
      elif key == "Modified":
        current["mtime"] = value[:16]
      elif key == "Encrypted":
        current["enc"] = value == "+"
  info["details"] = info["type"].upper() + (f", {method}" if method else "")
  return entries, info


def list_tar(path):
  entries, info = {}, {"volumes": 1, "details": "TAR", "hp": False, "type": "tar", "dot_prefix": False}
  with tarfile.open(path) as tf:
    for member in tf:
      name = member.name
      if name.startswith("./"):
        info["dot_prefix"] = True
        name = name[2:]
      name = name.rstrip("/")
      if not name or name == ".":
        continue
      entry = new_entry("Directory" if member.isdir() else "File")
      entry["size"] = member.size if member.isfile() else 0
      entry["mtime"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(member.mtime))
      entries[name] = entry
  comp = re.search(r"\.(gz|tgz|bz2|tbz2?|xz|txz)$", path, re.I)
  if comp:
    info["details"] = "TAR + " + {"tgz": "GZ", "tbz": "BZ2", "tbz2": "BZ2", "txz": "XZ"}.get(
        comp.group(1).lower(), comp.group(1).upper())
  return entries, info


def build_tree(entries):
  root = Node("", "", True)
  nodes = {"": root}
  for path, e in entries.items():
    parts = [p for p in path.split("/") if p]
    parent = root
    for i, part in enumerate(parts):
      sub = "/".join(parts[:i + 1])
      last = i == len(parts) - 1
      node = parent.children.get(part)
      if node is None:
        node = Node(part, sub, not last or e["type"] == "Directory")
        parent.children[part] = node
        nodes[sub] = node
      if last:
        node.is_dir = node.is_dir or e["type"] == "Directory"
        node.mtime, node.enc = e["mtime"], e["enc"]
        if not node.is_dir:
          node.size, node.packed = e["size"], e["packed"]
      parent = node

  def totals(node):
    if node.is_dir:
      node.size = node.packed = 0
      for child in node.children.values():
        totals(child)
        node.size += child.size
        node.packed += child.packed
  totals(root)
  return root, nodes


# --------------------------------------------------------------------------- motores

class RarEngine:
  kind = "rar"
  label = "RAR"
  program = RAR_BIN
  passwords = True

  @staticmethod
  def pw(password):
    # -p- evita que rar se quede esperando una contraseña por teclado.
    return ["-p" + password] if password else ["-p-"]

  def list(self, path, password):
    rc, out = run_tool(self.program, ["lt", "-v"] + self.pw(password) + ["--", path])
    entries, info = parse_rar_listing(out)
    return entries, info, not entries and (rc == 11 or has_password_error(out)), rc, out

  def needs_password(self, path):
    rc, out = run_tool(self.program, ["lt", "-p-", "--", path])
    return rc == 11 or has_password_error(out) or "Flags: encrypted" in out

  def masks(self, path, is_dir):
    return [path, path + "/*"] if is_dir else [path]

  def extract(self, path, dest, masks, password, overwrite, keep_paths, inner_dir, info):
    ap = ["-ap" + inner_dir] if inner_dir and masks and keep_paths else []
    return (["x" if keep_paths else "e", "-y", overwrite] + self.pw(password) + ap
            + ["--", path] + masks + [dest.rstrip("/") + "/"])

  def test(self, path, password):
    return ["t"] + self.pw(password) + ["--", path]

  def can_modify(self, info):
    return info["volumes"] <= 1

  def delete(self, path, masks, password):
    return ["d"] + self.pw(password) + ["--", path] + masks

  def add(self, path, sources, password, info, inner_dir):
    args = ["a", "-ep1", "-r", "-y"] + (["-ap" + inner_dir] if inner_dir else [])
    if password:
      args.append(("-hp" if info["hp"] else "-p") + password)
    return args + ["--", path] + list(sources)

  def create(self, opts, sources):
    args = ["a", "-ep1", "-r", "-y", f"-m{opts['level']}"]
    if opts["volume_kb"]:
      args.append(f"-v{opts['volume_kb']}k")
    if opts["password"]:
      args.append(("-hp" if opts["hide_names"] else "-p") + opts["password"])
    return args + ["--", opts["name"]] + sources


class SevenZipEngine:
  kind = "7z"
  label = "7-Zip"
  program = SEVENZIP_BIN
  passwords = True

  @staticmethod
  def pw(password):
    # Sin -p, 7zz intenta preguntar; con la entrada cerrada falla con "Enter password" y lo detectamos.
    return ["-p" + password] if password else []

  def list(self, path, password):
    rc, out = run_tool(self.program, ["l", "-slt", "-sccUTF-8"] + self.pw(password) + ["--", path])
    entries, info = parse_7z_listing(out)
    return entries, info, not entries and has_password_error(out), rc, out

  def needs_password(self, path):
    entries, _, need, _, _ = self.list(path, None)
    return need or any(e["enc"] for e in entries.values())

  def masks(self, path, is_dir):
    return [path]

  def extract(self, path, dest, masks, password, overwrite, keep_paths, inner_dir, info):
    return (["x" if keep_paths else "e", "-y", SEVENZIP_OVERWRITE[overwrite], "-bsp1", "-bb1", "-sccUTF-8"]
            + self.pw(password) + ["-o" + dest, "--", path] + masks)

  def test(self, path, password):
    return ["t", "-bsp1", "-bb1", "-sccUTF-8"] + self.pw(password) + ["--", path]

  def can_modify(self, info):
    return info["volumes"] <= 1 and info["type"] in ("7z", "zip", "tar")

  def delete(self, path, masks, password):
    return ["d", "-bsp1", "-bb1"] + self.pw(password) + ["--", path] + masks

  def add(self, path, sources, password, info, inner_dir):
    args = ["a", "-bsp1", "-bb1"] + self.pw(password)
    if password and info["type"] == "7z" and info["hp"]:
      args.append("-mhe=on")
    if password and info["type"] == "zip":
      args.append("-mem=AES256")
    return args + ["--", path] + list(sources)

  def create(self, opts, sources):
    zip_format = opts["format"] == "zip"
    args = ["a", "-tzip" if zip_format else "-t7z", f"-mx{SEVENZIP_LEVELS[opts['level']]}", "-bsp1", "-bb1", "-y"]
    if opts["volume_kb"]:
      args.append(f"-v{opts['volume_kb']}k")
    if opts["password"]:
      args.append("-p" + opts["password"])
      args.append("-mem=AES256" if zip_format else ("-mhe=on" if opts["hide_names"] else "-mhe=off"))
    return args + ["--", opts["name"]] + sources


class TarEngine:
  kind = "tar"
  label = "tar"
  program = BSDTAR_BIN
  passwords = False

  def list(self, path, password):
    try:
      entries, info = list_tar(path)
      return entries, info, False, 0, ""
    except (tarfile.TarError, OSError, EOFError) as e:
      return {}, {}, False, 2, f"ERROR: {e}"

  def needs_password(self, path):
    return False

  def masks(self, path, is_dir):
    return [path]

  def extract(self, path, dest, masks, password, overwrite, keep_paths, inner_dir, info):
    args = ["-x", "-v", "-f", path, "-C", dest]
    if overwrite != "-o+":
      args.append("-k")  # bsdtar no sabe renombrar: conserva los existentes
    if inner_dir and masks and keep_paths:
      args += ["--strip-components", str(inner_dir.count("/") + 1)]
    if not keep_paths:
      args += ["-s", ",.*/,,"]
    prefix = "./" if info and info.get("dot_prefix") else ""
    return args + [prefix + m for m in masks]

  def test(self, path, password):
    return ["-t", "-f", path]

  def can_modify(self, info):
    return False


ENGINES = {"rar": RarEngine(), "7z": SevenZipEngine(), "tar": TarEngine()}
CREATE_FORMATS = [(label, fmt) for label, fmt, program in (("RAR", "rar", RAR_BIN), ("7Z", "7z", SEVENZIP_BIN),
                                                            ("ZIP", "zip", SEVENZIP_BIN)) if program]


def engine_for(path):
  if TAR_RE.search(path):
    return ENGINES["tar"]
  if RAR_RE.search(path) or re.search(r"\.rar\.\d{3}$", path, re.I):
    return ENGINES["rar"] if RAR_BIN or not SEVENZIP_BIN else ENGINES["7z"]
  if SEVENZIP_RE.search(path):
    return ENGINES["7z"]
  return None


def is_archive(path):
  return engine_for(path) is not None


# --------------------------------------------------------------------------- salida de los programas

class ToolOutput:
  """Interpreta la salida a medida que llega: porcentaje, archivo, volumen y errores."""

  def __init__(self, kind, sizes=None):
    self.kind = kind
    self.sizes = sizes or {}
    self.total = sum(self.sizes.values())
    self.done_bytes = 0
    self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
    self.buf = self.raw = ""
    self.pct = 0
    self.file = self.volume = ""
    self.errors = []
    self.bad_password = False

  def feed(self, data):
    text = self.decoder.decode(data)
    self.raw += text
    self.buf += text
    # rar y 7zz reescriben el porcentaje con retrocesos (\b), así que partimos también por \b.
    parts = re.split(r"[\b\r\n]", self.buf)
    self.buf = parts.pop()
    for part in parts:
      self.handle(part)

  def finish(self, rc):
    if self.buf:
      self.handle(self.buf)
      self.buf = ""
    if rc == 0:
      self.pct = 100

  def log(self):
    text = re.sub(r"\x08+ *\d{1,3}%( \d+)?", "", self.raw)
    return re.sub(r"[ \t]*\x08+[ \t]*", "", text)

  def handle(self, token):
    s = token.strip()
    if not s:
      return
    if self.kind == "tar":
      self.handle_tar(s)
      return
    m = PCT_START_RE.match(s)
    if m:
      self.pct = min(int(m.group(1)), 100)
      s = s[m.end():].strip()
    m = PCT_END_RE.search(s)
    if m:
      self.pct = min(int(m.group(1)), 100)
      s = s[:m.start()].strip()
    if not s or s.isdigit():
      return
    for prefix in ("Extracting from ", "Creating archive ", "Testing archive ", "Updating archive ",
                   "Processing archive ", "Extracting archive: "):
      if s.startswith(prefix):
        self.volume = os.path.basename(s[len(prefix):])
        return
    m = RAR_FILE_RE.match(s) if self.kind == "rar" else SEVENZIP_FILE_RE.match(s)
    if m:
      self.file = m.group(2) if self.kind == "rar" else m.group(1)
    self.check_error(s)

  def handle_tar(self, s):
    if s.startswith(("tar: ", "bsdtar: ")):
      self.errors.append(s)
      return
    name = s[2:] if s.startswith("x ") else s
    if name.startswith("./"):
      name = name[2:]
    self.file = name
    if self.total:
      self.done_bytes += self.sizes.get(name.rstrip("/"), 0)
      self.pct = min(99, self.done_bytes * 100 // self.total)

  def check_error(self, s):
    low = s.lower()
    if any(k in low for k in PASSWORD_KEYS):
      self.bad_password = True
      self.errors.append(s)
    elif not SUMMARY_RE.match(s) and any(k in low for k in ERROR_KEYS):
      self.errors.append(s)


# --------------------------------------------------------------------------- iconos

def paint_icon(draw, size=32):
  """Dibuja un icono en una cuadrícula de 16x16 escalada a `size`."""
  pix = QPixmap(size, size)
  pix.fill(Qt.transparent)
  p = QPainter(pix)
  p.scale(size / 16, size / 16)

  def r(color, x0, y0, x1, y1):
    p.fillRect(x0, y0, x1 - x0, y1 - y0, QColor(color))

  draw(r)
  p.end()
  return QIcon(pix)


def _folder(r):
  r("#C9961A", 1, 3, 7, 5); r("#C9961A", 1, 5, 15, 14); r("#F5C542", 2, 6, 14, 13)


def _file(r):
  r("#8A8A8A", 3, 1, 13, 15); r("#FFFFFF", 4, 2, 12, 14)
  for y in (5, 8, 11):
    r("#B0B0B0", 6, y, 10, y + 1)


def _archive(r):
  # Caja de cartón con cinta: icono de MacRAR y de los archivos RAR.
  r("#B86E25", 1, 3, 15, 6); r("#C97A2C", 1, 6, 15, 15); r("#F0A54E", 2, 7, 14, 14)
  r("#FFF1C9", 7, 3, 9, 15); r("#FFFFFF", 3, 10, 6, 13)


def _archive_other(r):
  # Caja gris con cremallera para 7z, zip, tar...
  r("#5F6B7A", 2, 2, 14, 14); r("#8C99A8", 3, 3, 13, 13)
  for y in range(2, 14, 2):
    r("#E0B020", 7, y, 9, y + 1)


def _down_arrow(r, color, x, y):
  r(color, x + 2, y, x + 4, y + 4)
  for i in range(3):
    r(color, x + i, y + 3 + i, x + 6 - i, y + 4 + i)


def _add(r):
  _archive(r); r("#FFFFFF", 8, 8, 16, 16); r("#1E9E3A", 11, 9, 13, 16); r("#1E9E3A", 9, 11, 16, 13)


def _extract_to(r):
  _folder(r); _down_arrow(r, "#6C2E8C", 5, 5)


def _extract_here(r):
  _archive(r); r("#FFFFFF", 9, 8, 16, 16); _down_arrow(r, "#1E9E3A", 9, 9)


def _test(r):
  for i in range(3):
    r("#1E9E3A", 2 + i, 8 + i, 4 + i, 10 + i)
  for i in range(8):
    r("#1E9E3A", 5 + i, 10 - i, 7 + i, 12 - i)


def _view(r):
  _file(r); r("#1F6FC5", 8, 8, 15, 15); r("#FFFFFF", 10, 10, 13, 13)


def _delete(r):
  for i in range(10):
    r("#C0392B", 3 + i, 3 + i, 5 + i, 5 + i); r("#C0392B", 11 - i, 3 + i, 13 - i, 5 + i)


def _info(r):
  r("#1F6FC5", 2, 2, 14, 14); r("#FFFFFF", 7, 4, 9, 6); r("#FFFFFF", 7, 7, 9, 12)


def _up(r):
  for i in range(6):
    r("#2E8B3D", 7 - i, 2 + i, 9 + i, 3 + i)
  r("#2E8B3D", 6, 8, 10, 14)


def make_icons():
  icons = {name: paint_icon(fn) for name, fn in (
      ("add", _add), ("extract_to", _extract_to), ("extract_here", _extract_here), ("test", _test),
      ("view", _view), ("delete", _delete), ("info", _info), ("up", _up), ("archive", _archive),
      ("archive_other", _archive_other))}
  provider = QFileIconProvider()
  icons["folder"] = provider.icon(QFileIconProvider.IconType.Folder)
  icons["file"] = provider.icon(QFileIconProvider.IconType.File)
  icons["provider"] = provider
  return icons


def archive_icon(icons, name):
  return icons["archive"] if engine_for(name) is ENGINES["rar"] else icons["archive_other"]


# --------------------------------------------------------------------------- diálogos

def ok_cancel(dialog, layout):
  buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
  buttons.button(QDialogButtonBox.Ok).setText("Aceptar")
  buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
  buttons.accepted.connect(dialog.accept)
  buttons.rejected.connect(dialog.reject)
  layout.addWidget(buttons)


def warn(parent, title, text):
  QMessageBox.warning(parent, title, text)


class PasswordDialog(QDialog):
  def __init__(self, parent, archive, message):
    super().__init__(parent)
    self.setWindowTitle("Introducir contraseña")
    layout = QVBoxLayout(self)
    title = QLabel(os.path.basename(archive))
    title.setFont(QFont(title.font().family(), 14, QFont.Bold))
    layout.addWidget(title)
    label = QLabel(message)
    label.setWordWrap(True)
    layout.addWidget(label)
    self.entry = QLineEdit()
    self.entry.setEchoMode(QLineEdit.Password)
    self.entry.setMinimumWidth(320)
    layout.addWidget(self.entry)
    show = QCheckBox("Mostrar contraseña")
    show.toggled.connect(lambda on: self.entry.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
    layout.addWidget(show)
    ok_cancel(self, layout)

  def accept(self):
    if self.entry.text():
      super().accept()


def ask_password(parent, archive, message="Este archivo está protegido. Introduce la contraseña:"):
  dlg = PasswordDialog(parent, archive, message)
  return dlg.entry.text() if dlg.exec() == QDialog.Accepted else None


class ExtractDialog(QDialog):
  def __init__(self, parent, default_dest):
    super().__init__(parent)
    self.setWindowTitle("Rutas y opciones de extracción")
    layout = QVBoxLayout(self)
    layout.addWidget(QLabel("Carpeta de destino (se crea si no existe):"))
    row = QHBoxLayout()
    self.dest = QLineEdit(default_dest)
    self.dest.setMinimumWidth(420)
    row.addWidget(self.dest)
    browse = QPushButton("Examinar…")
    browse.clicked.connect(self.browse)
    row.addWidget(browse)
    layout.addLayout(row)

    box = QGroupBox("Modo de actualización")
    box_layout = QVBoxLayout(box)
    self.modes = QButtonGroup(self)
    for i, (text, value) in enumerate((("Sobrescribir sin preguntar", "-o+"), ("Omitir archivos existentes", "-o-"),
                                       ("Renombrar automáticamente", "-or"))):
      radio = QRadioButton(text)
      radio.setProperty("switch", value)
      radio.setChecked(i == 0)
      self.modes.addButton(radio)
      box_layout.addWidget(radio)
    layout.addWidget(box)
    self.keep_paths = QCheckBox("Conservar las rutas de las carpetas")
    self.keep_paths.setChecked(True)
    layout.addWidget(self.keep_paths)
    ok_cancel(self, layout)

  def browse(self):
    path = QFileDialog.getExistingDirectory(self, "Carpeta de destino", os.path.dirname(self.dest.text()))
    if path:
      self.dest.setText(path)

  def accept(self):
    if not self.dest.text().strip():
      warn(self, "Destino", "Escribe una carpeta de destino.")
      return
    super().accept()

  def options(self):
    return {"dest": os.path.expanduser(self.dest.text().strip()),
            "overwrite": self.modes.checkedButton().property("switch"),
            "keep_paths": self.keep_paths.isChecked()}


class AddDialog(QDialog):
  def __init__(self, parent, default_name, sources):
    super().__init__(parent)
    self.setWindowTitle("Nombre y parámetros del archivo")
    layout = QVBoxLayout(self)
    count = len(sources)
    layout.addWidget(QLabel(f"Se comprimirán {count} elemento{'s' if count != 1 else ''}."))
    row = QHBoxLayout()
    self.name = QLineEdit(default_name)
    self.name.setMinimumWidth(420)
    row.addWidget(self.name)
    browse = QPushButton("Examinar…")
    browse.clicked.connect(self.browse)
    row.addWidget(browse)
    form = QFormLayout()
    form.addRow("Nombre del archivo:", row)
    self.format = QComboBox()
    for label, fmt in CREATE_FORMATS:
      self.format.addItem(label, fmt)
    self.format.currentIndexChanged.connect(self.format_changed)
    form.addRow("Formato:", self.format)
    self.level = QComboBox()
    self.level.addItems([n for n, _ in COMPRESSION_LEVELS])
    self.level.setCurrentText("Normal")
    form.addRow("Método de compresión:", self.level)
    self.volume = QLineEdit()
    self.volume.setPlaceholderText("vacío = sin dividir")
    self.volume.setMaximumWidth(160)
    form.addRow("Dividir en volúmenes (MB):", self.volume)
    layout.addLayout(form)

    box = QGroupBox("Contraseña (opcional)")
    box_form = QFormLayout(box)
    self.pw1, self.pw2 = QLineEdit(), QLineEdit()
    for entry in (self.pw1, self.pw2):
      entry.setEchoMode(QLineEdit.Password)
    box_form.addRow("Contraseña:", self.pw1)
    box_form.addRow("Confirmar:", self.pw2)
    self.hide_names = QCheckBox("Cifrar también los nombres de archivo")
    box_form.addRow(self.hide_names)
    layout.addWidget(box)
    ok_cancel(self, layout)

  def format_changed(self):
    fmt = self.format.currentData()
    # ZIP no puede ocultar los nombres de archivo.
    self.hide_names.setEnabled(fmt != "zip")
    if fmt == "zip":
      self.hide_names.setChecked(False)
    name = self.name.text().strip()
    if name:
      self.name.setText(re.sub(r"\.(rar|7z|zip)$", "", name, flags=re.I) + "." + fmt)

  def browse(self):
    fmt = self.format.currentData()
    path, _ = QFileDialog.getSaveFileName(self, "Guardar como", self.name.text(), f"Archivo {fmt.upper()} (*.{fmt})")
    if path:
      self.name.setText(path)

  def accept(self):
    fmt = self.format.currentData()
    name = os.path.expanduser(self.name.text().strip())
    if not name:
      warn(self, "Nombre", "Escribe un nombre para el archivo.")
      return
    if not name.lower().endswith("." + fmt):
      name += "." + fmt
    volume_kb = None
    if self.volume.text().strip():
      try:
        volume_kb = int(float(self.volume.text().replace(",", ".")) * 1024)
        if volume_kb <= 0:
          raise ValueError
      except ValueError:
        warn(self, "Volúmenes", "El tamaño de volumen debe ser un número de MB.")
        return
    if self.pw1.text() != self.pw2.text():
      warn(self, "Contraseña", "Las contraseñas no coinciden.")
      return
    self.result_options = {"name": name, "format": fmt, "level": dict(COMPRESSION_LEVELS)[self.level.currentText()],
                           "volume_kb": volume_kb, "password": self.pw1.text() or None,
                           "hide_names": self.hide_names.isChecked()}
    super().accept()


def show_result(parent, title, message, errors, log):
  dlg = QDialog(parent)
  dlg.setWindowTitle(title)
  layout = QVBoxLayout(dlg)
  label = QLabel(message)
  label.setFont(QFont(label.font().family(), 14, QFont.Bold))
  layout.addWidget(label)
  text = QPlainTextEdit()
  text.setReadOnly(True)
  text.setFont(QFont("Menlo", 11))
  text.setMinimumSize(640, 320)
  lines = []
  if errors:
    lines.append("Problemas encontrados:")
    for msg in dict.fromkeys(translate_error(e) for e in errors):
      lines.append("  • " + msg)
    lines.append("")
  lines.append("--- Registro ---")
  lines.append(log.strip())
  text.setPlainText("\n".join(lines))
  layout.addWidget(text)
  buttons = QDialogButtonBox(QDialogButtonBox.Close)
  buttons.button(QDialogButtonBox.Close).setText("Cerrar")
  buttons.rejected.connect(dlg.reject)
  layout.addWidget(buttons)
  dlg.exec()


class ProgressDialog(QDialog):
  """Ejecuta rar / 7zz / bsdtar con QProcess y muestra porcentaje, archivo, volumen y tiempo."""

  def __init__(self, parent, title, engine, args, cwd, sizes=None):
    super().__init__(parent)
    self.setWindowTitle(title)
    self.setMinimumWidth(500)
    layout = QVBoxLayout(self)
    heading = QLabel(title)
    heading.setFont(QFont(heading.font().family(), 14, QFont.Bold))
    layout.addWidget(heading)
    self.vol_label = QLabel("")
    self.vol_label.setStyleSheet("color: gray")
    layout.addWidget(self.vol_label)
    self.file_label = QLabel("Preparando…")
    layout.addWidget(self.file_label)
    self.bar = QProgressBar()
    self.bar.setRange(0, 100)
    layout.addWidget(self.bar)
    row = QHBoxLayout()
    self.pct_label = QLabel("0%")
    self.pct_label.setFont(QFont(heading.font().family(), 22, QFont.Bold))
    row.addWidget(self.pct_label)
    row.addStretch()
    self.time_label = QLabel("")
    self.time_label.setStyleSheet("color: gray")
    self.time_label.setAlignment(Qt.AlignRight)
    row.addWidget(self.time_label)
    layout.addLayout(row)
    self.cancel_btn = QPushButton("Cancelar")
    self.cancel_btn.clicked.connect(self.reject)
    buttons = QHBoxLayout()
    buttons.addStretch()
    buttons.addWidget(self.cancel_btn)
    layout.addLayout(buttons)

    self.out = ToolOutput(engine.kind, sizes)
    # bsdtar no informa porcentaje: sin tamaños previos mostramos una barra de actividad.
    self.indeterminate = engine.kind == "tar" and not self.out.total
    if self.indeterminate:
      self.bar.setRange(0, 0)
      self.pct_label.setText("…")
    self.cancelled = False
    self.result = None
    self.start = time.time()
    self.proc = QProcess(self)
    self.proc.setProgram(engine.program)
    self.proc.setArguments(args)
    if cwd:
      self.proc.setWorkingDirectory(cwd)
    self.proc.setProcessChannelMode(QProcess.MergedChannels)
    self.proc.setStandardInputFile(QProcess.nullDevice())
    self.proc.readyReadStandardOutput.connect(self.read_output)
    self.proc.finished.connect(self.on_finished)
    self.proc.errorOccurred.connect(self.on_error)
    self.timer = QTimer(self, interval=250, timeout=self.update_labels)
    self.timer.start()
    QTimer.singleShot(0, self.proc.start)

  def read_output(self):
    self.out.feed(bytes(self.proc.readAllStandardOutput()))
    self.update_labels()

  def update_labels(self):
    out = self.out
    if not self.indeterminate:
      self.bar.setValue(out.pct)
      self.pct_label.setText(f"{out.pct}%")
    if out.volume:
      self.vol_label.setText("Volumen: " + out.volume)
    if out.file:
      name = os.path.basename(out.file.rstrip("/")) or out.file  # la ruta completa no cabe
      self.file_label.setText(name if not self.cancelled else "Cancelando…")
    elapsed = time.time() - self.start
    text = "Transcurrido: " + time.strftime("%M:%S", time.gmtime(elapsed))
    if not self.indeterminate and 0 < out.pct < 100 and elapsed > 2:
      text += "\nRestante: " + time.strftime("%M:%S", time.gmtime(elapsed * (100 - out.pct) / out.pct))
    self.time_label.setText(text)

  def on_error(self, error):
    if error == QProcess.FailedToStart:
      self.result = {"rc": -1, "log": self.proc.errorString(), "errors": [self.proc.errorString()],
                     "bad_password": False, "cancelled": False}
      self.done(QDialog.Accepted)

  def on_finished(self, code, status):
    self.read_output()
    rc = code if status == QProcess.NormalExit else 255
    self.out.finish(rc)
    self.result = {"rc": rc, "log": self.out.log(), "errors": self.out.errors,
                   "bad_password": self.out.bad_password or (self.out.kind == "rar" and rc == 11),
                   "cancelled": self.cancelled}
    self.timer.stop()
    self.done(QDialog.Accepted)

  def reject(self):
    # Cerrar la ventana o pulsar Cancelar detiene el proceso; el diálogo se cierra cuando termina.
    if self.proc.state() == QProcess.NotRunning:
      if self.result is None:
        self.result = {"rc": 255, "log": "", "errors": [], "bad_password": False, "cancelled": True}
      super().reject()
      return
    self.cancelled = True
    self.cancel_btn.setEnabled(False)
    self.file_label.setText("Cancelando…")
    self.proc.terminate()


def run_job(parent, title, engine, args, cwd, sizes=None):
  dlg = ProgressDialog(parent, title, engine, args, cwd, sizes)
  dlg.exec()
  return dlg.result


def missing_tool_message(engine):
  if engine.kind == "rar":
    return "No se encontró rar en /Applications/rar/rar."
  if engine.kind == "7z":
    return ("No se encontró 7-Zip (7zz).\nDescárgalo de https://www.7-zip.org (versión para macOS) "
            "y copia el archivo 7zz en /Applications/7zip/.")
  return "No se encontró bsdtar."


# --------------------------------------------------------------------------- ventana principal

class MainWindow(QMainWindow):
  def __init__(self, initial=None):
    super().__init__()
    self.setWindowTitle("MacRAR")
    self.resize(980, 600)
    self.setAcceptDrops(True)
    self.icons = make_icons()
    self.mode = "fs"                # "fs" = explorando carpetas, "archive" = dentro de un archivo comprimido
    downloads = os.path.expanduser("~/Downloads")
    self.fs_dir = downloads if os.path.isdir(downloads) else os.path.expanduser("~")
    self.arc = None                 # datos del archivo abierto
    self.arc_dir = ""               # carpeta actual dentro del archivo
    self.passwords = {}             # contraseñas correctas de esta sesión
    self.sort_col, self.sort_rev = 0, False

    self.build_actions()
    self.build_toolbar()
    self.build_menu()
    self.build_central()
    if not RAR_BIN and not SEVENZIP_BIN:
      QMessageBox.critical(self, "Faltan programas", "No se encontró ni rar ni 7-Zip (7zz).")
    QTimer.singleShot(0, lambda: self.open_path(initial or self.fs_dir))

  # ---- construcción de la interfaz

  def build_actions(self):
    def action(text, icon, slot, shortcut=None):
      act = QAction(self.icons[icon] if icon else QIcon(), text, self)
      act.triggered.connect(slot)
      if shortcut:
        act.setShortcut(QKeySequence(shortcut))
      return act

    self.a_add = action("Agregar", "add", self.add)
    self.a_extract_to = action("Extraer en", "extract_to", self.extract_to, "Ctrl+E")
    self.a_extract_here = action("Extraer aquí", "extract_here", self.extract_here)
    self.a_test = action("Probar", "test", self.test, "Ctrl+T")
    self.a_view = action("Ver", "view", self.view)
    self.a_delete = action("Eliminar", "delete", self.delete)
    self.a_info = action("Información", "info", self.info, "Ctrl+I")
    self.a_open = action("Abrir archivo comprimido…", None, self.ask_open_archive, QKeySequence.Open)
    self.a_folder = action("Ir a carpeta…", None, self.ask_open_folder, "Ctrl+Shift+O")
    self.a_close = action("Cerrar archivo comprimido", None, self.close_archive, "Ctrl+W")

  def build_toolbar(self):
    tb = QToolBar("Herramientas")
    tb.setMovable(False)
    tb.setIconSize(QSize(32, 32))
    tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
    for act in (self.a_add, self.a_extract_to, self.a_extract_here, self.a_test, self.a_view,
                self.a_delete, self.a_info):
      tb.addAction(act)
    self.addToolBar(tb)

  def build_menu(self):
    m = self.menuBar().addMenu("Archivo")
    for act in (self.a_open, self.a_folder, self.a_close):
      m.addAction(act)
    c = self.menuBar().addMenu("Órdenes")
    for act in (self.a_add, self.a_extract_to, self.a_extract_here, self.a_test, self.a_view, self.a_delete):
      c.addAction(act)
    c.addSeparator()
    c.addAction(self.a_info)

  def build_central(self):
    central = QWidget()
    layout = QVBoxLayout(central)
    layout.setContentsMargins(6, 6, 6, 0)
    row = QHBoxLayout()
    up = QToolButton()
    up.setIcon(self.icons["up"])
    up.setIconSize(QSize(18, 18))
    up.setToolTip("Subir un nivel")
    up.setAutoRaise(True)
    up.clicked.connect(self.go_up)
    row.addWidget(up)
    self.address = QLineEdit()
    self.address.returnPressed.connect(self.go_address)
    row.addWidget(self.address)
    layout.addLayout(row)

    self.tree = QTreeWidget()
    self.tree.setHeaderLabels(["Nombre", "Tamaño", "Comprimido", "Tipo", "Modificado"])
    self.tree.setRootIsDecorated(False)
    self.tree.setUniformRowHeights(True)
    self.tree.setAlternatingRowColors(True)
    self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
    self.tree.setIconSize(QSize(18, 18))
    header = self.tree.header()
    header.setSectionsClickable(True)
    header.setSortIndicatorShown(True)
    header.setSortIndicator(0, Qt.AscendingOrder)
    header.sectionClicked.connect(self.sort_by)
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.Stretch)
    for col, width in ((1, 100), (2, 100), (3, 160), (4, 140)):
      self.tree.setColumnWidth(col, width)
    self.tree.itemDoubleClicked.connect(lambda *_: self.activate())
    self.tree.itemSelectionChanged.connect(self.update_status)
    self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
    self.tree.customContextMenuRequested.connect(self.show_context)
    self.tree.installEventFilter(self)
    layout.addWidget(self.tree)
    self.setCentralWidget(central)

  def eventFilter(self, obj, event):
    if obj is self.tree and event.type() == QEvent.KeyPress:
      key = event.key()
      if key in (Qt.Key_Return, Qt.Key_Enter):
        self.activate()
        return True
      if key == Qt.Key_Backspace and not (event.modifiers() & Qt.ControlModifier):
        self.go_up()
        return True
      if key == Qt.Key_Delete or (key == Qt.Key_Backspace and event.modifiers() & Qt.ControlModifier):
        self.delete()
        return True
    return super().eventFilter(obj, event)

  # ---- arrastrar y soltar

  def dragEnterEvent(self, event):
    if event.mimeData().hasUrls():
      event.acceptProposedAction()

  def dropEvent(self, event):
    paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
    if not paths:
      return
    event.acceptProposedAction()
    # Soltar un archivo comprimido lo abre; soltar otros archivos o carpetas los comprime.
    if len(paths) == 1 and is_archive(paths[0]):
      self.open_archive(paths[0])
    elif self.mode == "archive":
      self.add_to_open_archive(paths)
    else:
      self.add(paths)

  # ---- navegación

  def open_path(self, path):
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isdir(path):
      self.show_fs(path)
    elif os.path.isfile(path) and is_archive(path):
      self.open_archive(path)
    elif os.path.exists(path):
      self.show_fs(os.path.dirname(path), select="f:" + path)
    else:
      QMessageBox.critical(self, "Ruta", f"No existe:\n{path}")

  def ask_open_archive(self):
    path, _ = QFileDialog.getOpenFileName(
        self, "Abrir archivo comprimido", self.current_fs_dir(),
        "Archivos comprimidos (*.rar *.7z *.zip *.tar *.gz *.tgz *.bz2 *.xz *.iso *.001);;Todos (*)")
    if path:
      self.open_archive(path)

  def ask_open_folder(self):
    path = QFileDialog.getExistingDirectory(self, "Ir a carpeta", self.current_fs_dir())
    if path:
      self.show_fs(path)

  def current_fs_dir(self):
    return os.path.dirname(self.arc["path"]) if self.mode == "archive" else self.fs_dir

  def show_fs(self, path, select=None):
    self.mode, self.arc, self.arc_dir = "fs", None, ""
    self.fs_dir = path
    self.setWindowTitle("MacRAR")
    self.refresh(select)

  def close_archive(self):
    if self.mode == "archive":
      self.show_fs(os.path.dirname(self.arc["path"]), select="f:" + self.arc["path"])

  def go_up(self):
    if self.mode == "archive":
      if self.arc_dir:
        previous = self.arc_dir
        self.arc_dir = previous.rpartition("/")[0]
        self.refresh(select="a:" + previous)
      else:
        self.close_archive()
    else:
      parent = os.path.dirname(self.fs_dir)
      if parent != self.fs_dir:
        previous = self.fs_dir
        self.show_fs(parent, select="f:" + previous)

  def go_address(self):
    text = self.address.text().strip()
    if self.mode == "archive" and text.startswith(self.arc["path"]):
      inner = text[len(self.arc["path"]):].strip("/")
      if inner in self.arc["nodes"] and self.arc["nodes"][inner].is_dir:
        self.arc_dir = inner
        self.refresh()
      return
    self.open_path(text)

  def current_id(self):
    item = self.tree.currentItem()
    if item is None or not item.isSelected():
      items = self.tree.selectedItems()
      item = items[0] if items else None
    return item.data(0, ROLE_ID) if item else None

  def activate(self):
    sel = self.current_id()
    if not sel:
      return
    if sel == "..":
      self.go_up()
      return
    if self.mode == "fs":
      path = sel[2:]
      if os.path.isdir(path):
        self.show_fs(path)
      elif is_archive(path):
        self.open_archive(path)
      else:
        open_in_finder(path)
    else:
      node = self.arc["nodes"][sel[2:]]
      if node.is_dir:
        self.arc_dir = node.path
        self.refresh()
      else:
        self.view()

  def show_context(self, pos):
    menu = QMenu(self)
    open_act = menu.addAction("Abrir")
    open_act.triggered.connect(self.activate)
    for act in (self.a_extract_to, self.a_extract_here, self.a_test):
      menu.addAction(act)
    menu.addSeparator()
    menu.addAction(self.a_add)
    menu.addAction(self.a_delete)
    menu.exec(self.tree.viewport().mapToGlobal(pos))

  # ---- listado

  def sort_by(self, col):
    self.sort_rev = not self.sort_rev if self.sort_col == col else False
    self.sort_col = col
    self.tree.header().setSortIndicator(col, Qt.DescendingOrder if self.sort_rev else Qt.AscendingOrder)
    self.refresh(select=self.current_id())

  def fs_rows(self):
    rows = []
    try:
      entries = list(os.scandir(self.fs_dir))
    except OSError as e:
      QMessageBox.critical(self, "Carpeta", f"No se puede abrir la carpeta:\n{e}")
      return rows
    provider = self.icons["provider"]
    for entry in entries:
      if entry.name.startswith("."):
        continue
      try:
        st = entry.stat()
        is_dir = entry.is_dir()
      except OSError:
        continue
      if is_dir:
        kind, icon = "Carpeta de archivos", self.icons["folder"]
      elif is_archive(entry.name):
        kind, icon = archive_kind(entry.name), archive_icon(self.icons, entry.name)
      else:
        ext = os.path.splitext(entry.name)[1][1:].upper()
        kind, icon = (f"Archivo {ext}" if ext else "Archivo"), provider.icon(QFileInfo(entry.path))
      rows.append({"id": "f:" + entry.path, "name": entry.name, "is_dir": is_dir,
                   "size": None if is_dir else st.st_size, "packed": None, "type": kind,
                   "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)), "icon": icon})
    return rows

  def arc_rows(self):
    rows = []
    show_packed = self.arc["engine"].kind != "tar"  # tar no guarda el tamaño comprimido por archivo
    for node in self.arc["nodes"][self.arc_dir].children.values():
      if node.is_dir:
        kind, icon = "Carpeta de archivos", self.icons["folder"]
      else:
        ext = os.path.splitext(node.name)[1][1:].upper()
        kind = f"Archivo {ext}" if ext else "Archivo"
        icon = archive_icon(self.icons, node.name) if is_archive(node.name) else self.icons["file"]
      rows.append({"id": "a:" + node.path, "name": node.name + (" *" if node.enc else ""), "is_dir": node.is_dir,
                   "size": node.size, "packed": node.packed if show_packed else None, "type": kind,
                   "mtime": node.mtime, "icon": icon})
    return rows

  def refresh(self, select=None):
    tree = self.tree
    tree.clear()
    rows = self.fs_rows() if self.mode == "fs" else self.arc_rows()
    key = ("name", "size", "packed", "type", "mtime")[self.sort_col]

    def sort_key(row):
      value = row[key]
      if key == "name":
        value = value.lower()
      return value if value is not None else -1

    rows.sort(key=sort_key, reverse=self.sort_rev)
    rows.sort(key=lambda r: not r["is_dir"])  # carpetas siempre primero
    items = {}
    if self.mode == "archive" or os.path.dirname(self.fs_dir) != self.fs_dir:
      up = QTreeWidgetItem(["..", "", "", "Carpeta superior", ""])
      up.setIcon(0, self.icons["up"])
      up.setData(0, ROLE_ID, "..")
      tree.addTopLevelItem(up)
      items[".."] = up
    for row in rows:
      packed = "" if row["packed"] is None or row["is_dir"] else human_size(row["packed"])
      item = QTreeWidgetItem([row["name"], human_size(row["size"]), packed, row["type"], row["mtime"]])
      item.setIcon(0, row["icon"])
      item.setData(0, ROLE_ID, row["id"])
      item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
      item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
      tree.addTopLevelItem(item)
      items[row["id"]] = item
    if self.mode == "archive":
      self.address.setText(self.arc["path"] + ("/" + self.arc_dir if self.arc_dir else ""))
    else:
      self.address.setText(self.fs_dir)
    target = items.get(select) or (tree.topLevelItem(0) if tree.topLevelItemCount() else None)
    if target:
      tree.setCurrentItem(target)
      tree.scrollToItem(target)
    tree.setFocus()
    self.update_status()

  def update_status(self):
    selected = self.selected_items()
    if self.mode == "archive":
      root = self.arc["nodes"][""]
      files = sum(1 for n in self.arc["nodes"].values() if not n.is_dir)
      text = f"{self.arc['engine'].label}  ·  Total: {files} archivos, {human_size(root.size)}"
      if selected:
        size = sum(self.arc["nodes"][p].size for p in selected)
        text = f"Seleccionado: {len(selected)} elemento(s), {human_size(size)}      " + text
    else:
      count = sum(1 for i in range(self.tree.topLevelItemCount())
                  if self.tree.topLevelItem(i).data(0, ROLE_ID) != "..")
      text = f"{count} elementos en {self.fs_dir}"
      if selected:
        text = f"Seleccionado: {len(selected)} elemento(s)      " + text
    self.statusBar().showMessage(text)

  def selected_items(self):
    """Rutas seleccionadas (rutas reales en modo carpeta, rutas internas en modo archivo)."""
    ids = [item.data(0, ROLE_ID) for item in self.tree.selectedItems()]
    return [i[2:] for i in ids if i and i != ".."]

  # ---- abrir un archivo comprimido

  def usable_engine(self, path):
    engine = engine_for(path)
    if engine is None:
      QMessageBox.information(self, "Formato", f"Formato no compatible:\n{os.path.basename(path)}")
      return None
    if not engine.program:
      QMessageBox.critical(self, "Falta un programa", missing_tool_message(engine))
      return None
    return engine

  def list_archive(self, engine, path):
    """Lista el archivo pidiendo la contraseña si los nombres están cifrados. Devuelve None si se cancela."""
    password = None
    asked = False
    while True:
      QApplication.setOverrideCursor(Qt.WaitCursor)
      try:
        entries, info, need_pw, rc, out = engine.list(path, password)
      finally:
        QApplication.restoreOverrideCursor()
      if need_pw:
        if password is None and not asked and path in self.passwords:
          password = self.passwords[path]
          continue
        message = ("Contraseña incorrecta. Inténtalo de nuevo:" if password
                   else "Los nombres de este archivo están cifrados.\nIntroduce la contraseña:")
        password = ask_password(self, path, message)
        asked = True
        if password is None:
          return None
        continue
      if not entries and rc not in (0, 1):
        show_result(self, "Error", f"No se pudo abrir {os.path.basename(path)}",
                    [l for l in out.splitlines() if any(k in l.lower() for k in ERROR_KEYS)], out)
        return None
      if password:
        self.passwords[path] = password
        info["hp"] = True  # hizo falta la contraseña solo para ver los nombres
      return entries, info

  def open_archive(self, path, keep_dir=""):
    path = first_volume(os.path.abspath(path))
    engine = self.usable_engine(path)
    if engine is None:
      return
    listed = self.list_archive(engine, path)
    if listed is None:
      return
    entries, info = listed
    _, nodes = build_tree(entries)
    self.arc = {"path": path, "engine": engine, "nodes": nodes, "info": info, "hp": info["hp"],
                "encrypted": info["hp"] or any(e["enc"] for e in entries.values()),
                "sizes": {p: e["size"] for p, e in entries.items()}}
    self.mode = "archive"
    self.arc_dir = keep_dir if keep_dir in nodes and nodes[keep_dir].is_dir else ""
    self.setWindowTitle(f"MacRAR - {os.path.basename(path)}")
    self.refresh()

  def open_archive_data(self, archive):
    """Datos del archivo si es el que está abierto (para no volver a listarlo)."""
    return self.arc if self.mode == "archive" and self.arc["path"] == archive else None

  def needs_password(self, engine, archive):
    data = self.open_archive_data(archive)
    if data:
      return data["encrypted"]
    return engine.passwords and engine.needs_password(archive)

  # ---- operaciones

  def run_with_password(self, title, engine, archive, build_args, cwd=None, sizes=None):
    """Ejecuta con barra de progreso; si la contraseña falla, la vuelve a pedir y reintenta.

    Devuelve el resultado, o None si se canceló.
    """
    password = self.passwords.get(archive)
    if password is None and self.needs_password(engine, archive):
      password = ask_password(self, archive)
      if password is None:
        return None
    while True:
      result = run_job(self, title, engine, build_args(password), cwd, sizes)
      if result["cancelled"]:
        self.statusBar().showMessage("Operación cancelada.")
        return None
      if not result["bad_password"] or not engine.passwords:
        if password:
          self.passwords[archive] = password
        return result
      self.passwords.pop(archive, None)
      password = ask_password(self, archive, "Contraseña incorrecta. Inténtalo de nuevo:" if password
                              else "Este archivo está protegido. Introduce la contraseña:")
      if password is None:
        return None

  def target_archives(self):
    """Archivos sobre los que actúa una orden (el abierto, o los seleccionados en la carpeta)."""
    if self.mode == "archive":
      return [self.arc["path"]]
    archives = []
    for path in self.selected_items():
      if os.path.isfile(path) and is_archive(path):
        first = first_volume(path)
        if first not in archives:
          archives.append(first)
    if not archives:
      QMessageBox.information(self, "Seleccionar", "Selecciona uno o más archivos comprimidos (o abre uno).")
    return archives

  def selected_masks(self):
    """Máscaras con lo seleccionado dentro del archivo (vacío = todo)."""
    if self.mode != "archive":
      return []
    engine, masks = self.arc["engine"], []
    for path in self.selected_items():
      masks += engine.masks(path, self.arc["nodes"][path].is_dir)
    return masks

  def extract_to(self):
    archives = self.target_archives()
    if not archives:
      return
    dlg = ExtractDialog(self, os.path.join(os.path.dirname(archives[0]), archive_basename(archives[0])))
    if dlg.exec() != QDialog.Accepted:
      return
    opts = dlg.options()
    self.extract(archives, lambda a: opts["dest"] if len(archives) == 1
                 else os.path.join(opts["dest"], archive_basename(a)), opts["overwrite"], opts["keep_paths"])

  def extract_here(self):
    archives = self.target_archives()
    if archives:
      self.extract(archives, os.path.dirname, "-o+", True)

  def extract(self, archives, dest_for, overwrite, keep_paths):
    masks = self.selected_masks()
    results = []
    for archive in archives:
      engine = self.usable_engine(archive)
      if engine is None:
        continue
      data = self.open_archive_data(archive)
      info = data["info"] if data else None
      # Si se extrae una selección desde una subcarpeta, las rutas son relativas a esa subcarpeta (RAR y tar).
      inner_dir = self.arc_dir if data else ""
      sizes = data["sizes"] if data and not masks else None
      dest = dest_for(archive)
      try:
        os.makedirs(dest, exist_ok=True)
      except OSError as e:
        QMessageBox.critical(self, "Destino", f"No se puede crear la carpeta:\n{e}")
        return
      result = self.run_with_password(
          f"Extrayendo {os.path.basename(archive)}", engine, archive,
          lambda pw: engine.extract(archive, dest, masks, pw, overwrite, keep_paths, inner_dir, info),
          cwd=os.path.dirname(archive), sizes=sizes)
      if result is None:
        break
      results.append((archive, dest, result))
    self.report_extract(results)

  def report_extract(self, results):
    if not results:
      return
    if self.mode == "fs":
      self.refresh(select=self.current_id())
    failed = [(a, r) for a, _, r in results if r["rc"] != 0 or r["errors"]]
    dest = results[0][1]
    if not failed:
      self.statusBar().showMessage("Extracción completada sin errores.")
      answer = QMessageBox.question(self, "Extracción completada",
                                    f"Todo correcto. Archivos extraídos en:\n{dest}\n\n¿Abrir la carpeta en Finder?")
      if answer == QMessageBox.Yes:
        open_in_finder(dest)
      return
    errors, log = [], ""
    for archive, result in failed:
      errors += result["errors"] or [f"El programa terminó con código {result['rc']}"]
      log += f"### {os.path.basename(archive)}\n{result['log']}\n"
    show_result(self, "Extracción con errores", "La extracción terminó con errores.", errors, log)

  def test(self):
    archives = self.target_archives()
    if not archives:
      return
    archive = archives[0]
    engine = self.usable_engine(archive)
    if engine is None:
      return
    data = self.open_archive_data(archive)
    result = self.run_with_password(f"Probando {os.path.basename(archive)}", engine, archive,
                                    lambda pw: engine.test(archive, pw), cwd=os.path.dirname(archive),
                                    sizes=data["sizes"] if data else None)
    if result is None:
      return
    if result["rc"] == 0 and not result["errors"]:
      QMessageBox.information(self, "Probar", f"No se encontraron errores en\n{os.path.basename(archive)}")
    else:
      show_result(self, "Probar", "Se encontraron errores en el archivo.", result["errors"], result["log"])

  def view(self):
    if self.mode == "fs":
      self.activate()
      return
    files = [p for p in self.selected_items() if not self.arc["nodes"][p].is_dir]
    if not files:
      self.activate()
      return
    archive, engine, info = self.arc["path"], self.arc["engine"], self.arc["info"]
    tmp = tempfile.mkdtemp(prefix="macrar_")
    result = self.run_with_password("Abriendo archivo", engine, archive,
                                    lambda pw: engine.extract(archive, tmp, files, pw, "-o+", False, "", info),
                                    cwd=tmp)
    if result is None:
      return
    opened = False
    for path in files:
      target = os.path.join(tmp, os.path.basename(path))
      if os.path.exists(target):
        open_in_finder(target)
        opened = True
    if not opened:
      show_result(self, "Ver", "No se pudo abrir el archivo.", result["errors"], result["log"])

  def require_editable(self):
    if self.mode != "archive":
      return False
    if not self.arc["engine"].can_modify(self.arc["info"]):
      QMessageBox.warning(self, "No disponible", "Este archivo no se puede modificar "
                          "(está dividido en volúmenes o su formato no lo permite).")
      return False
    return True

  def delete(self):
    if self.mode != "archive":
      QMessageBox.information(self, "Eliminar", "Abre un archivo comprimido para eliminar elementos de él.")
      return
    masks = self.selected_masks()
    if not masks or not self.require_editable():
      return
    names = "\n".join(self.selected_items()[:10])
    answer = QMessageBox.warning(self, "Eliminar", f"¿Eliminar del archivo comprimido?\n\n{names}\n\n"
                                 "Esta acción no se puede deshacer.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    if answer != QMessageBox.Yes:
      return
    archive, keep, engine = self.arc["path"], self.arc_dir, self.arc["engine"]
    result = self.run_with_password("Eliminando", engine, archive, lambda pw: engine.delete(archive, masks, pw),
                                    cwd=os.path.dirname(archive))
    if result and result["rc"] != 0:
      show_result(self, "Eliminar", "No se pudieron eliminar los elementos.", result["errors"], result["log"])
    self.open_archive(archive, keep)

  def add(self, sources=None):
    if self.mode == "archive":
      self.add_to_open_archive(sources)
      return
    if not CREATE_FORMATS:
      QMessageBox.critical(self, "Agregar", "No hay ningún programa para crear archivos (rar o 7zz).")
      return
    sources = sources or self.selected_items()
    if not sources:
      sources, _ = QFileDialog.getOpenFileNames(self, "Archivos a comprimir", self.fs_dir)
      if not sources:
        return
    first = sources[0].rstrip("/")
    base = os.path.basename(first) if len(sources) == 1 else os.path.basename(os.path.dirname(first))
    if not os.path.isdir(first) and len(sources) == 1:
      base = os.path.splitext(base)[0]
    dlg = AddDialog(self, os.path.join(os.path.dirname(first), base + "." + CREATE_FORMATS[0][1]), sources)
    if dlg.exec() != QDialog.Accepted:
      return
    opts = dlg.result_options
    engine = ENGINES["rar"] if opts["format"] == "rar" else ENGINES["7z"]
    result = run_job(self, f"Creando {os.path.basename(opts['name'])}", engine, engine.create(opts, sources),
                     os.path.dirname(opts["name"]))
    if self.mode == "fs" and os.path.dirname(opts["name"]) == self.fs_dir:
      candidates = [opts["name"], opts["name"][:-4] + ".part1.rar", opts["name"] + ".001"]
      created = next((c for c in candidates if os.path.exists(c)), opts["name"])
      self.refresh(select="f:" + created)
    if result["cancelled"]:
      self.statusBar().showMessage("Compresión cancelada.")
    elif result["rc"] == 0:
      self.statusBar().showMessage(f"Archivo creado: {opts['name']}")
      QMessageBox.information(self, "Agregar", f"Archivo creado correctamente:\n{opts['name']}")
    else:
      show_result(self, "Agregar", "La compresión terminó con errores.", result["errors"], result["log"])

  def add_to_open_archive(self, sources=None):
    if not self.require_editable():
      return
    if not sources:
      sources, _ = QFileDialog.getOpenFileNames(self, "Archivos a agregar", os.path.dirname(self.arc["path"]))
      if not sources:
        return
    archive, keep, engine, info = self.arc["path"], self.arc_dir, self.arc["engine"], self.arc["info"]
    password = self.passwords.get(archive)
    if self.arc["encrypted"] and not password:
      password = ask_password(self, archive, "Introduce la contraseña del archivo para cifrar lo que agregues:")
      if password is None:
        return
    result = run_job(self, f"Agregando a {os.path.basename(archive)}", engine,
                     engine.add(archive, sources, password, info, keep), os.path.dirname(archive))
    if result["rc"] != 0 and not result["cancelled"]:
      show_result(self, "Agregar", "No se pudieron agregar los archivos.", result["errors"], result["log"])
    elif keep and engine.kind != "rar":
      QMessageBox.information(self, "Agregar", "Con este formato los archivos se agregan en la raíz del archivo.")
    self.open_archive(archive, keep)

  def info(self):
    if self.mode != "archive":
      archives = [p for p in self.selected_items() if is_archive(p)]
      if not archives:
        QMessageBox.information(self, "Información", "Abre o selecciona un archivo comprimido para ver su información.")
        return
      self.open_archive(archives[0])
      if self.mode != "archive":
        return
    arc, nodes = self.arc, self.arc["nodes"]
    root = nodes[""]
    files = sum(1 for n in nodes.values() if not n.is_dir)
    folders = sum(1 for n in nodes.values() if n.is_dir) - 1
    encryption = "Sí, incluidos los nombres" if arc["hp"] else ("Sí" if arc["encrypted"] else "No")
    lines = [
        f"Archivo: {os.path.basename(arc['path'])}",
        f"Carpeta: {os.path.dirname(arc['path'])}",
        f"Formato: {arc['info']['details'] or arc['engine'].label}",
        f"Volúmenes: {arc['info']['volumes']}",
        f"Archivos: {files}    Carpetas: {folders}",
        f"Tamaño total: {human_size(root.size)}",
    ]
    if arc["engine"].kind == "tar":
      lines.append(f"Tamaño en disco: {human_size(os.path.getsize(arc['path']))}")
    else:
      ratio = f"{root.packed * 100 / root.size:.0f}%" if root.size else "-"
      lines.append(f"Comprimido: {human_size(root.packed)}  (relación {ratio})")
    lines.append(f"Cifrado: {encryption}")
    QMessageBox.information(self, "Información del archivo", "\n".join(lines))


class App(QApplication):
  """Recibe los archivos que macOS envía al abrir con la app (QFileOpenEvent)."""

  def __init__(self, argv):
    super().__init__(argv)
    self.window = None

  def event(self, event):
    if event.type() == QEvent.FileOpen and self.window:
      self.window.open_path(event.file())
      return True
    return super().event(event)


def main():
  app = App(sys.argv)
  app.setApplicationName("MacRAR")
  app.window = MainWindow(sys.argv[1] if len(sys.argv) > 1 else None)
  app.window.show()
  sys.exit(app.exec())


if __name__ == "__main__":
  main()
