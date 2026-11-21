# SPDX-License-Identifier: GPL-3.0-or-later
"""Las operaciones del análisis de sitio, sin widgets.

Cada una que cambia el modelo es UN paso de deshacer. El panel, los
menús y el menú contextual llaman aquí; las pruebas también.
"""
from __future__ import annotations

import uuid

from . import clima
from .datos import WIND_COLORS


class SiteError(Exception):
    """Algo que el usuario debe saber (se muestra tal cual)."""


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8]


# ---- Viento ----------------------------------------------------------------------

def set_winds(sitio, winds: list) -> None:
    st = sitio.store
    c = st.content()
    c["winds"] = winds
    st.set_content(c)
    sitio.update_animation()
    sitio.changed.emit()


def dominant_winds(sitio) -> list:
    s = sitio.stats()
    if not s:
        raise SiteError("Carga primero un archivo EPW.")
    out = []
    for i, d in enumerate(s["top"]):
        out.append({"on": True, "label": "Viento-" + clima.DIR16_LONG[d],
                    "dir": d * 22.5, "speed": round(s["avg_sp"][d], 1),
                    "color": WIND_COLORS[i % len(WIND_COLORS)],
                    "freq": round(s["freq"][d] * 100, 1)})
    if not out:
        raise SiteError("El periodo elegido no tiene horas con viento.")
    return out


# ---- Clima -------------------------------------------------------------------------

def load_epw_text(sitio, text: str) -> dict:
    epw = clima.parse_epw(text)
    sitio.store.set_climate(clima.compact(epw))
    sitio.changed.emit()
    return epw["info"]


def clear_epw(sitio) -> None:
    sitio.store.set_climate(None)
    sitio.changed.emit()
