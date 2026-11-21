# SPDX-License-Identifier: GPL-3.0-or-later
"""Salidas: imagen de la vista con el análisis y gráficos del clima.

El OBJ, el SKP, el DXF y el IFC de las masas ya los da IngeTrazo
(Archivo ▸ Exportar): las masas son geometría normal del modelo.
"""
from __future__ import annotations

from PySide6.QtGui import QPainter

from . import clima
from .acciones import SiteError


def render_png(sitio, overlay, width: int):
    """La vista actual a ``width`` px de ancho, con el análisis encima (y
    el panel de información si está visible en la vista)."""
    vp = sitio.app.viewport
    img = vp.render_image(int(width), sharp_shadows=True)
    if img is None:
        raise SiteError("La vista no está lista para exportar.")
    p = QPainter(img)
    p.scale(img.width() / max(1, vp.width()),
            img.height() / max(1, vp.height()))
    overlay.draw(vp, p, export=True)
    p.end()
    return img


def export_png(sitio, overlay, path: str, width: int = 1920) -> str:
    img = render_png(sitio, overlay, width)
    if not img.save(path):
        raise SiteError(f"No se pudo guardar {path}.")
    return path


def export_charts(sitio, base_path: str) -> list[str]:
    from .graficos import chart_image
    stats = sitio.stats()
    if not stats:
        raise SiteError("Carga primero un archivo EPW.")
    w = sitio.store.view()["wind"]
    cols = clima.rose_colors(w.get("pal", "amber"), w.get("c0"), w.get("c1"))
    stem = base_path[:-4] if base_path.lower().endswith(".png") else base_path
    out = []
    for kind, suffix in (("rose", "_rosa.png"), ("months", "_mensual.png")):
        path = stem + suffix
        if not chart_image(kind, stats, cols).save(path):
            raise SiteError(f"No se pudo guardar {path}.")
        out.append(path)
    return out
