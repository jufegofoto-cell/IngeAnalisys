# SPDX-License-Identifier: GPL-3.0-or-later
"""Herramientas de un clic (con inferencias): el centro del
diagrama solar… Se crean al pedirlas y no a nivel de módulo:
una clase ``Tool`` del módulo tendría su propia entrada en el menú
Extensiones (core/extensions.py), y la entrada es el panel."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QVector3D


def make_pick_tool(prompt: str, done, cancelled, repeat: bool = False):
    """``done(point, ctx)`` con el punto INFERIDO (extremo, cara, eje…);
    ``cancelled()`` con Esc. Con ``repeat`` sigue activa tras cada clic
    (varios puntos seguidos) hasta Esc."""
    from tools.base import Tool

    class _PickPoint(Tool):
        name = prompt
        shortcut = None
        uses_snap = True

        def __init__(self) -> None:
            self.hover_point = None

        def on_activate(self, viewport) -> None:
            self.hover_point = None

        def on_deactivate(self, viewport) -> None:
            self.hover_point = None

        def on_hover(self, ctx) -> None:
            self.hover_point = ctx.world
            ctx.viewport.update()

        def on_click(self, ctx) -> None:
            done(QVector3D(ctx.world), ctx)
            if not repeat:
                cancelled()

        def on_key(self, viewport, key, modifiers) -> bool:
            if key == Qt.Key_Escape:
                cancelled()
                return True
            return False

        def rubber_band_lines(self):
            return []

    return _PickPoint()


def begin(app, tool, prompt: str) -> None:
    vp = app.viewport
    vp.set_active_tool(tool)
    try:
        app.window.statusBar().showMessage(prompt + " — Esc cancela")
    except Exception:                               # noqa: BLE001
        pass
    vp.setFocus()


def end(app) -> None:
    win = app.window
    try:
        win.statusBar().clearMessage()
    except Exception:                               # noqa: BLE001
        pass
    activate = getattr(win, "_activate_tool", None)
    if callable(activate):
        activate("select")
