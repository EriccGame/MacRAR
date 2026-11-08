#!/usr/bin/env python3
"""Genera el iconset de MacRAR (la caja de cartón dibujada en macrar.py) para convertirlo con iconutil.

Uso:  python3 app/make_icon.py build/MacRAR.iconset
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

import macrar


def main():
  out = sys.argv[1]
  os.makedirs(out, exist_ok=True)
  app = QApplication([])  # noqa: F841  (QPixmap necesita una aplicación Qt)
  for base in (16, 32, 128, 256, 512):
    for scale in (1, 2):
      size = base * scale
      pix = QPixmap(size, size)
      pix.fill(Qt.transparent)
      p = QPainter(pix)
      p.scale(size / 16, size / 16)
      macrar._archive(lambda c, x0, y0, x1, y1: p.fillRect(x0, y0, x1 - x0, y1 - y0, QColor(c)))
      p.end()
      pix.save(os.path.join(out, f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"))


if __name__ == "__main__":
  main()
