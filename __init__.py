# SPDX-License-Identifier: GPL-3.0-or-later
"""Análisis de Sitio — una EXTENSIÓN de IngeTrazo.

Sol · Viento · Clima, sobre el propio modelo: el diagrama solar 3D del
sitio (trayectorias del 21 de cada mes, banda anual, analemas, horas), el
sol del momento moviendo las sombras reales de IngeTrazo, el clima de un
archivo EPW (rosa de vientos, temperatura y humedad mensual), flechas de
viento animadas y rosa 3D sobre el suelo, radiación solar incidente y
estudio de sombras, notas en pantalla y exportación (imagen). El mapa base,
el contexto y el modelado de volúmenes son los de IngeTrazo.

Es el módulo de la app web «Análisis de Sitio 3D» llevado a IngeTrazo como
se hace una extensión de SketchUp: no toca el núcleo, todo pasa por
``setup(app)`` (views/extension_api.py, API 2):

- una pestaña en la bandeja lateral (``add_panel``) y un submenú propio en
  Extensiones (``add_menu``);
- el dibujo sobre la vista (``add_overlay``);
- los datos en el documento (.igz), con deshacer;
- la ubicación, la hora del sol, el mapa base y el terreno son los de
  IngeTrazo — una sola verdad, no una copia.

Instalación: copiar la carpeta ``analisis_sitio`` en la carpeta de
extensiones (Extensiones ▸ Abrir carpeta de extensiones) y reiniciar.
"""
from __future__ import annotations

import logging

__version__ = "1.12.0"
log = logging.getLogger("ingetrazo.plugins.analisis_sitio")


def setup(app) -> None:
    if getattr(app, "api_version", 1) not in (1, 2):
        log.warning("Análisis de Sitio espera la API de extensiones 2")
        return
    from PySide6.QtCore import QTimer

    from . import fuentes
    from .dibujo import Overlay
    from .nucleo import Sitio
    from .panel import SitePanel

    fuentes.register()      # fuentes libres incluidas (fuentes/OFL.txt)
    sitio = Sitio(app)
    overlay = Overlay(sitio)
    panel = SitePanel(sitio, overlay)
    dock = app.add_panel("Análisis de sitio", panel)

    app.add_overlay(lambda vp, painter: overlay.draw(vp, painter))

    # ---- El documento cambió (edición, deshacer, abrir, otra pestaña) ---------
    def changed():
        sitio.update_animation()
        panel.schedule_refresh()

    app.on_document_changed(changed)
    if hasattr(app, "on_document_closed"):
        app.on_document_closed(lambda _scene: None)

    # ---- Menú Extensiones ▸ Análisis de sitio ------------------------------------
    def show():
        app.show_panel(dock)

    def later(fn):
        return lambda: QTimer.singleShot(0, fn)

    sub = app.add_menu("Análisis de sitio")
    if sub is not None:
        entries = (
            ("Mostrar panel", show),
            (None, None),
            ("Abrir clima (EPW)…", lambda: (show(), panel._on_open_epw())),
            ("Elegir ubicación en el mapa…", lambda: (show(), panel._on_pick_map())),
            (None, None),
            ("Animar recorrido solar", panel._on_anim),
            ("Mostrar / ocultar el análisis", _toggle(sitio, panel)),
            (None, None),
            ("Exportar imagen (PNG)…", panel._on_png),
        )
        for text, fn in entries:
            if text is None:
                sub.addSeparator()
            else:
                a = sub.addAction(text)
                a.triggered.connect(lambda _c=False, f=fn: f())
    else:                                            # ventana sin ese menú
        app.add_menu_action("Análisis de sitio…", show)

    sitio.update_animation()
    # Para pruebas y la consola de Python.
    app.window.__dict__.setdefault("_ext_instances", {})[app.key] = {
        "sitio": sitio, "panel": panel, "overlay": overlay, "dock": dock}


def _toggle(sitio, panel):
    def fn():
        v = sitio.store.view()
        v["on"] = not v.get("on", True)
        sitio.store.touch()
        sitio.update_animation()
        panel.refresh()
    return fn
