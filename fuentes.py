"""Fuentes libres de los textos del diagrama.

Las cuatro fuentes (licencia SIL OFL 1.1, ver ``fuentes/OFL.txt``) viajan
dentro del módulo y se registran en Qt al primer uso, de modo que se ven
igual en cualquier equipo sin instalarlas. Son las mismas que ofrece la
extensión de SketchUp.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication

from .datos import FONTS

log = logging.getLogger(__name__)

DEFAULT = FONTS[0]

_DIR = Path(__file__).resolve().parent / "fuentes"
_FILES = (
    "AtkinsonHyperlegible-Regular.otf", "AtkinsonHyperlegible-Bold.otf",
    "NotoSans-Regular.ttf", "NotoSans-Bold.ttf",
    "Inter-Regular.otf", "Inter-Bold.otf",
    "SourceSans3-Regular.ttf", "SourceSans3-Bold.ttf",
)
_loaded: set[str] | None = None


def register() -> set[str]:
    """Registra las fuentes incluidas (una sola vez). Devuelve las familias
    que quedaron disponibles."""
    global _loaded
    if _loaded is not None:
        return _loaded
    if QGuiApplication.instance() is None:      # aún sin aplicación Qt
        return set()
    _loaded = set()
    for name in _FILES:
        path = _DIR / name
        if not path.is_file():
            log.warning("Falta la fuente %s", path)
            continue
        fid = QFontDatabase.addApplicationFont(str(path))
        if fid < 0:
            log.warning("No se pudo cargar la fuente %s", path)
            continue
        _loaded.update(QFontDatabase.applicationFontFamilies(fid))
    return _loaded


def family(name) -> str:
    """La familia pedida si es una de las ofrecidas; si no (p. ej. «Arial»
    de una versión anterior), la predeterminada."""
    return name if name in FONTS else DEFAULT


def qfont(name=None, px: float = 12.0, bold: bool = False) -> QFont:
    """QFont de la familia ``name`` (o la predeterminada). Si por algún motivo
    la fuente no se pudo cargar, Qt usa una sin serifa del sistema."""
    register()
    f = QFont()
    f.setFamilies([family(name), "sans-serif"])
    f.setStyleHint(QFont.SansSerif)
    f.setPixelSize(max(6, int(round(px))))
    f.setBold(bool(bold))
    return f
