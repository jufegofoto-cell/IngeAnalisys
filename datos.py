# SPDX-License-Identifier: GPL-3.0-or-later
"""Lo que el análisis de sitio guarda EN EL DOCUMENTO (.igz).

Tres valores en ``scene.plugin_data``, cada uno con su ritmo:

- ``<key>`` (``app.document_data``): el contenido — los vientos.
  Cada cambio es un paso de deshacer (``app.set_document_data``).
- ``<key>.vista``: cómo se ve — colores, radios, casillas. Se escribe
  directo y marca el documento como modificado, sin llenar el historial
  (como el panel de Sombras de IngeTrazo al arrastrar la hora).
- ``<key>.clima``: el EPW compacto (≈ 400 kB). Aparte, para que deshacer
  un viento no copie el año entero de datos horarios.

La hora y la fecha del estudio NO están aquí: son las de ``scene.shadows``
(una sola verdad con las sombras de IngeTrazo). La ubicación es
``scene.georef``.
"""
from __future__ import annotations

import copy
import json

from core.history import Command

# Fuentes libres de los textos del diagrama (fuentes.py las carga); la
# primera es la predeterminada. Las mismas que la extensión de SketchUp.
FONTS = ("Atkinson Hyperlegible", "Noto Sans", "Inter", "Source Sans 3")

PALETTE = ("#e5e7eb", "#f4c27a", "#9ec5fe", "#b9e3a5", "#f3a6a6", "#d5b8f5",
           "#a7e3df")
WIND_COLORS = ("#b5d16b", "#d79bd4", "#e8c95a", "#7dd3fc", "#fca5a5")


def default_view() -> dict:
    return {
        "on": True,
        "site_name": "",
        "radius": 100.0,
        "center": [0.0, 0.0, 0.0],
        "sun": {"months": [6, 3, 12], "band": True, "analemma": True,
                "day": False, "labels": True, "ray": False,
                "color": "#b09c45", "ball": "#ff3b30", "diagram": False,
                "font": "Atkinson Hyperlegible", "font_size": 10.0, "font_bold": False},
        "wind": {"show": False, "anim": False, "rose": False, "hourly": False,
                 "height": 8.0, "width": 1.0, "pal": "blue",
                 "c0": "#bfdbfe", "c1": "#1e3a8a", "opa": 0.8, "scale": 0.62},
        "rings": {"n": 4, "step": 25.0, "color": "#5ec8ff"},
        "view": {"compass": True, "notes": False,
                 "notes_title": "ANÁLISIS DE SITIO", "label_scale": 1.0,
                 "notes_corner": "tr"},
        "period": {"m0": 1, "m1": 12, "h0": 0, "h1": 24},
        "rad": {"period": "day", "month": 6, "target": "all", "grid": 1.0,
                "ground": True, "ground_r": 0.0,
                "mode": "cells", "albedo": 0.2, "palette": "ecotect",
                "auto": True, "vmin": 0.0, "vmax": 1000.0, "legend": True},
        "sd": {"h0": 7, "h1": 18, "step": 30, "palette": "grises",
               "show": "shade"},
    }


def default_content() -> dict:
    return {"winds": [
        {"on": True, "label": "Viento-Sur", "dir": 180.0, "speed": 2.5,
         "color": "#b5d16b"},
        {"on": True, "label": "Viento-Oeste", "dir": 270.0, "speed": 2.0,
         "color": "#d79bd4"},
        {"on": True, "label": "Viento-Noroeste", "dir": 315.0, "speed": 1.6,
         "color": "#e8c95a"},
    ]}


def _merge(base: dict, raw) -> dict:
    """``raw`` sobre los valores por defecto, un nivel de anidación."""
    out = copy.deepcopy(base)
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if k not in out:
            continue
        if isinstance(out[k], dict) and isinstance(v, dict):
            for kk, vv in v.items():
                if kk in out[k]:
                    out[k][kk] = vv
        else:
            out[k] = v
    return out


class Store:
    """Acceso a los cuatro valores del documento para una extensión."""

    def __init__(self, app) -> None:
        self.app = app
        self.key = app.key

    @property
    def scene(self):
        return self.app.scene

    def _pd(self) -> dict:
        pd = getattr(self.scene, "plugin_data", None)
        if pd is None:
            self.scene.plugin_data = pd = {}
        return pd

    # ---- Vista (directo, sin deshacer) --------------------------------------
    def view(self) -> dict:
        pd = self._pd()
        key = self.key + ".vista"
        cur = pd.get(key)
        merged = _merge(default_view(), cur)
        sun = merged["sun"]
        if sun.get("font") not in FONTS:     # p. ej. «Arial» de la v1.10
            sun["font"] = FONTS[0]
        if cur != merged:
            pd[key] = merged
        return pd[key]

    def touch(self) -> None:
        """La vista cambió: documento modificado y repintar."""
        self.scene.version += 1
        vp = self.app.viewport
        vp.update()

    # ---- Contenido (deshacer) -----------------------------------------------
    def content(self) -> dict:
        raw = self.app.document_data(None)
        if not isinstance(raw, dict):
            return default_content()
        out = default_content()
        if isinstance(raw.get("winds"), list):
            out["winds"] = [w for w in raw["winds"] if isinstance(w, dict)]
        return out

    def set_content(self, value: dict) -> None:
        self.app.set_document_data(value)

    # ---- Clima ----------------------------------------------------------------
    def climate(self):
        return self._pd().get(self.key + ".clima")

    def set_climate(self, value) -> None:
        from core.history import SetPluginDataCommand
        vp = self.app.viewport
        vp.history.execute(SetPluginDataCommand(self.key + ".clima", value))
        notify = getattr(vp, "notify_scene_changed", None)
        if callable(notify):
            notify()
        vp.update()

class Paso(Command):
    """UN paso de deshacer para lo que toca grupos, capas y datos de la
    extensión a la vez (los mapas de radiación y sombras).
    Las reconstrucciones sustituyen el objeto ``Group`` entero, así que
    basta recordar las listas: los objetos viejos quedan intactos."""

    def __init__(self, mutate, keys=()) -> None:
        self.mutate = mutate
        self.keys = tuple(keys)
        self.before = self.after = None

    def _capture(self, scene) -> dict:
        pd = scene.plugin_data
        return {
            "mesh": scene.mesh.capture_state(),
            "groups": list(scene.groups),
            "layers": list(scene.layers),
            "hidden": [(g, g.hidden) for g in scene.groups],
            "data": {k: (json.loads(json.dumps(pd[k])) if k in pd else None)
                     for k in self.keys},
        }

    def _restore(self, scene, s) -> None:
        scene.mesh.restore_state(s["mesh"])
        scene.groups[:] = s["groups"]
        scene.layers[:] = s["layers"]
        for g, h in s["hidden"]:
            g.hidden = h
        for k, v in s["data"].items():
            if v is None:
                scene.plugin_data.pop(k, None)
            else:
                scene.plugin_data[k] = json.loads(json.dumps(v))
        live = set(scene.mesh.faces) | set(scene.mesh.edges) | set(scene.groups)
        scene.selection.intersection_update(live)

    def do(self, scene) -> None:
        if self.after is None:
            self.before = self._capture(scene)
            try:
                self.mutate(scene)
            except BaseException:
                self._restore(scene, self.before)
                raise
            self.after = self._capture(scene)
        else:
            self._restore(scene, self.after)
        scene.version += 1

    def undo(self, scene) -> None:
        self._restore(scene, self.before)
        scene.version += 1


def run_step(viewport, mutate, keys=(), message: str | None = None) -> bool:
    """Ejecuta ``mutate(scene)`` como un paso; False (y aviso) si falló."""
    hist = viewport.history
    hist.execute(Paso(mutate, keys))
    err = getattr(hist, "last_error", None)
    if err:
        viewport.flash_status(str(err).split(": ", 1)[-1], 5000)
        return False
    notify = getattr(viewport, "notify_scene_changed", None)
    if callable(notify):
        notify()
    viewport.update()
    if message:
        viewport.flash_status(message, 3500)
    return True
