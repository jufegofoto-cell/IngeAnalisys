# SPDX-License-Identifier: GPL-3.0-or-later
"""Lo que se dibuja sobre la vista 3D — ``app.add_overlay``.

Diagrama solar (banda anual, analemas, trayectorias del 21 de cada mes,
horas), el sol del momento con su rayo, la brújula graduada, los anillos
de distancia, las flechas de viento animadas, la rosa de vientos sobre el
suelo, las anotaciones y el recuadro de notas.

Todo está en metros del modelo y se proyecta en bloque con
``world_to_pixels`` (miles de puntos por cuadro); la geometría del cielo
se calcula una vez por sitio/radio/meses y se guarda. Lo que queda detrás
de la cámara se corta, nunca se dibuja deformado.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QPainter,
                           QPainterPath, QPen, QPolygonF, QRadialGradient)

from . import clima, solar

try:
    from .i18n import tr
except ImportError:
    from i18n import tr

MONTHS = clima.MONTHS
HOURLY_WIND = "#2f6fe0"          # la flecha del viento horario (EPW)


def qcolor(hexc: str, alpha: float = 1.0) -> QColor:
    c = QColor(hexc) if isinstance(hexc, str) else QColor(*hexc)
    if not c.isValid():
        c = QColor("#cccccc")
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _font(px: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setPixelSize(max(6, int(round(px))))
    f.setBold(bold)
    return f


class Projector:
    """Proyección de un cuadro: puntos del modelo → píxeles del widget."""

    def __init__(self, viewport) -> None:
        self.vp = viewport
        cam = viewport.camera
        self.eye = cam.eye()
        f = cam.forward()
        up = cam.up_vector() if hasattr(cam, "up_vector") else cam.up
        r = np.cross([f.x(), f.y(), f.z()], [up.x(), up.y(), up.z()])
        n = np.linalg.norm(r)
        self.right = r / n if n > 1e-12 else np.array([1.0, 0.0, 0.0])

    def __call__(self, pts):
        a = np.asarray(pts, dtype=float).reshape(-1, 3)
        if not len(a):
            return np.zeros((0, 2)), np.zeros(0, dtype=bool)
        px, py, ok = self.vp.world_to_pixels(a)
        xy = np.column_stack([np.asarray(px, float), np.asarray(py, float)])
        ok = (np.asarray(ok, bool) & np.isfinite(xy).all(axis=1)
              & (np.abs(xy) < 30000.0).all(axis=1))
        return xy, ok

    def one(self, p):
        xy, ok = self((p,))
        return QPointF(*xy[0]) if ok[0] else None

    def pixel_size(self, p, meters: float) -> float:
        """Cuántos píxeles mide ``meters`` en ``p`` (de frente a la cámara)."""
        p = np.asarray(p, float)
        xy, ok = self((p, p + self.right * meters))
        if not ok.all():
            return 0.0
        return float(np.hypot(*(xy[1] - xy[0])))


def _polyline(painter, xy, ok) -> None:
    """Dibuja tramos consecutivos de puntos válidos."""
    run = []
    for (x, y), good in zip(xy, ok):
        if good:
            run.append(QPointF(x, y))
        elif len(run) > 1:
            painter.drawPolyline(QPolygonF(run))
            run = []
        else:
            run = []
    if len(run) > 1:
        painter.drawPolyline(QPolygonF(run))


def _label(painter, pos: QPointF, text: str, px: float, color="#ffffff",
           bg=None, border=None, bold=True, align="center", pad=3.0) -> QRectF:
    font = _font(px, bold)
    painter.setFont(font)
    fm = QFontMetricsF(font)
    w, h = fm.horizontalAdvance(text), fm.height()
    if align == "center":
        rect = QRectF(pos.x() - w / 2 - pad, pos.y() - h / 2 - pad / 2,
                      w + 2 * pad, h + pad)
    else:                                            # a la derecha del punto
        rect = QRectF(pos.x() + 4, pos.y() - h / 2 - pad / 2, w + 2 * pad,
                      h + pad)
    if bg is not None:
        painter.setPen(QPen(qcolor(border), 1.2) if border else Qt.NoPen)
        painter.setBrush(qcolor(bg) if isinstance(bg, str) else bg)
        painter.drawRoundedRect(rect, 3, 3)
    painter.setPen(qcolor(color))
    painter.drawText(rect, Qt.AlignCenter, text)
    return rect


def _halo_text(painter, pos: QPointF, text: str, px: float, color: str,
               bold=True) -> None:
    """Texto con un contorno oscuro: se lee sobre mapa claro u oscuro."""
    font = _font(px, bold)
    fm = QFontMetricsF(font)
    w = fm.horizontalAdvance(text)
    path = QPainterPath()
    path.addText(QPointF(pos.x() - w / 2, pos.y() + fm.ascent() / 2 - 1),
                 font, text)
    painter.setPen(QPen(QColor(10, 14, 24, 200), 3.0, Qt.SolidLine,
                        Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)
    painter.setPen(Qt.NoPen)
    painter.setBrush(qcolor(color))
    painter.drawPath(path)


# ---- Geometría que no cambia cuadro a cuadro -----------------------------------

class SkyGeometry:
    """Banda, analemas, trayectorias mensuales, horas y brújula para un
    sitio, centro, radio y selección de meses."""

    def __init__(self, s: dict, view: dict) -> None:
        lat, lon, tz = s["lat"], s["lon"], s["tz"]
        sun = view["sun"]
        self.frame = fr = solar.Frame(view["center"], view["radius"], s["north"])
        R = fr.r
        self.band = (np.asarray(solar.band_quads(lat, fr), float)
                     if sun["band"] else None)
        self.analemmas = ([np.asarray(r) for r in solar.analemmas(lat, lon,
                                                                   tz, fr)]
                          if sun["analemma"] else [])
        self.months = []          # (mes, [tramos], discontinua)
        self.marks = []           # (hora, punto)
        self.month_labels = []    # (texto, punto)
        for m in sun["months"]:
            doy = solar.doy_of(int(m), 21)
            runs = [np.asarray(r) for r in solar.day_path(lat, lon, tz, doy, fr)]
            self.months.append((m, runs, m not in (6, 12)))
            marks = solar.hour_marks(lat, lon, tz, doy, fr)
            self.marks.extend((h, p) for h, p, _a in marks)
            if marks:
                h, p, a = max(marks, key=lambda t: t[2])
                self.month_labels.append(
                    (f"21 {MONTHS[int(m) - 1]}",
                     (p[0], p[1], p[2] + R * 0.07)))
        self.mark_pts = np.asarray([p for _h, p in self.marks] or
                                   np.zeros((0, 3)), float).reshape(-1, 3)
        self.mark_labels = np.asarray(
            [fr.sky(*_inv(fr, p), scale=1.045, lift=R * 0.018)
             for _h, p in self.marks] or np.zeros((0, 3)), float).reshape(-1, 3)
        # Brújula: dos círculos, marcas de grado, rótulos, flecha del norte.
        z = 0.25
        self.circle = np.asarray([fr.ground(a, R, z) for a in range(0, 361, 2)])
        self.circle2 = np.asarray([fr.ground(a, R * 1.06, z)
                                   for a in range(0, 361, 2)])
        ticks = []
        for a in range(360):
            k = 1.058 if a % 10 == 0 else 1.035 if a % 5 == 0 else 1.018
            ticks.append(fr.ground(a, R * 1.003, z))
            ticks.append(fr.ground(a, R * k, z))
        self.ticks = np.asarray(ticks)
        self.deg_labels = [(f"{a}°", fr.ground(a, R * 1.1, z))
                           for a in range(30, 360, 30) if a % 90]
        self.cardinals = [("N", fr.ground(0, R * 1.14, z), True),
                          ("E", fr.ground(90, R * 1.14, z), False),
                          ("S", fr.ground(180, R * 1.14, z), False),
                          ("O", fr.ground(270, R * 1.14, z), False)]
        tip = fr.ground(0, R * 1.26, z)
        self.north_arrow = np.asarray([
            tip, _offset(fr, R * 1.19, 1.6, z), fr.ground(0, R * 1.21, z),
            _offset(fr, R * 1.19, -1.6, z)])
        c = R * 0.03
        self.cross = np.asarray([fr.ground(90, c, z), fr.ground(270, c, z),
                                 fr.ground(0, c, z), fr.ground(180, c, z)])


def _offset(fr, r, deg, z):
    return fr.ground(deg, r, z)


def _inv(fr: solar.Frame, p):
    """Punto de la cúpula → (alt, az) (para rótulos un poco por fuera)."""
    dx, dy, dz = ((p[0] - fr.cx) / fr.r, (p[1] - fr.cy) / fr.r,
                  (p[2] - fr.cz) / fr.r)
    # deshacer el giro del norte: local → grid
    e = dx * fr._c - dy * fr._s
    n = dx * fr._s + dy * fr._c
    alt = math.degrees(math.asin(max(-1.0, min(1.0, dz))))
    az = math.degrees(math.atan2(e, n)) % 360.0
    return alt, az


class Overlay:
    """``draw(viewport, painter)`` — todo el análisis sobre la vista."""

    def __init__(self, sitio) -> None:
        self.sitio = sitio
        self._sky_cache = (None, None)
        self._day_cache = (None, None)

    # ---- Cachés ------------------------------------------------------------------
    def sky(self, s, view) -> SkyGeometry:
        sun = view["sun"]
        key = (round(s["lat"], 6), round(s["lon"], 6), s["tz"],
               round(s["north"], 4), tuple(view["center"]),
               float(view["radius"]), tuple(sun["months"]), sun["band"],
               sun["analemma"])
        if self._sky_cache[0] != key:
            self._sky_cache = (key, SkyGeometry(s, view))
        return self._sky_cache[1]

    def day(self, s, view, doy):
        key = (round(s["lat"], 6), round(s["lon"], 6), s["tz"],
               round(s["north"], 4), tuple(view["center"]),
               float(view["radius"]), doy)
        if self._day_cache[0] != key:
            fr = solar.Frame(view["center"], view["radius"], s["north"])
            runs = [np.asarray([(p[0], p[1], p[2] + 0.2) for p in r])
                    for r in solar.day_path(s["lat"], s["lon"], s["tz"], doy,
                                            fr)]
            self._day_cache = (key, runs)
        return self._day_cache[1]

    # ---- Dibujo ------------------------------------------------------------------
    def draw(self, viewport, painter: QPainter, export: bool = False) -> None:
        st = self.sitio
        view = st.store.view()
        if not view.get("on", True):
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        P = Projector(viewport)
        s = st.site()
        ls = float(view["view"].get("label_scale", 1.0))
        sun = view["sun"]
        sky = self.sky(s, view)
        if view["view"].get("compass", True):
            self._compass(painter, P, sky, sun["color"], ls)
        self._rings(painter, P, sky.frame, view["rings"], ls)
        stats = st.stats()
        if view["wind"]["rose"] and stats:
            self._rose(painter, P, sky.frame, view["wind"], stats, ls)
        if sun.get("diagram", True):
            self._sky(painter, P, sky, sun, ls)
        self._wind(painter, P, sky.frame, view["wind"],
                   st.store.content()["winds"], st.hour_record(), ls)
        doy, _hour = st.when()
        self._sun(painter, P, s, view, doy, ls)
        self._pins(painter, P, view, st.store.content()["pins"], ls)
        if st.edit_context and st.selected_building:
            self._building(painter, P, st)
        # Paneles flotantes, arriba a la derecha: el de información y
        # convenciones (casilla en la sección 12) y, debajo de él, el de la
        # escala de radiación / sombras (casilla en la sección 11). La imagen
        # exportada los lleva si están visibles.
        notes_box = None
        if getattr(self.sitio, "show_notes", False):
            notes_box = self._notes(painter, viewport, view, ls)
        self._rad_legend(painter, viewport, view, ls, notes_box)

    # Brújula y anillos ---------------------------------------------------------------
    def _compass(self, painter, P, sky, color, ls) -> None:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(qcolor(color, 0.95), 2.0))
        _polyline(painter, *P(sky.circle))
        painter.setPen(QPen(qcolor(color, 0.7), 1.0))
        _polyline(painter, *P(sky.circle2))
        xy, ok = P(sky.ticks)
        painter.setPen(QPen(qcolor(color, 0.8), 1.0))
        for i in range(0, len(xy) - 1, 2):
            if ok[i] and ok[i + 1]:
                painter.drawLine(QPointF(*xy[i]), QPointF(*xy[i + 1]))
        for text, p in sky.deg_labels:
            q = P.one(p)
            if q is not None:
                _halo_text(painter, q, text, 10 * ls, color, bold=False)
        for text, p, red in sky.cardinals:
            q = P.one(p)
            if q is not None:
                _halo_text(painter, q, text, 17 * ls,
                           "#ff5a4f" if red else color)
        xy, ok = P(sky.north_arrow)
        if ok.all():
            painter.setPen(Qt.NoPen)
            painter.setBrush(qcolor("#ff5a4f"))
            painter.drawPolygon(QPolygonF([QPointF(*q) for q in xy]))
        xy, ok = P(sky.cross)
        painter.setPen(QPen(qcolor(color, 0.9), 1.2))
        for i in (0, 2):
            if ok[i] and ok[i + 1]:
                painter.drawLine(QPointF(*xy[i]), QPointF(*xy[i + 1]))

    def _rings(self, painter, P, fr, rings, ls) -> None:
        n, step = int(rings.get("n", 0)), float(rings.get("step", 25.0))
        if n <= 0 or step <= 0:
            return
        col = rings.get("color", "#5ec8ff")
        pen = QPen(qcolor(col, 0.6), 1.2, Qt.DashLine)
        for i in range(1, n + 1):
            r = i * step
            pts = np.asarray([fr.ground(a, r, 0.3) for a in range(0, 361, 3)])
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            _polyline(painter, *P(pts))
            for az in (205.0, 25.0):
                q = P.one(fr.ground(az, r + step * 0.12, 0.35))
                if q is not None:
                    _halo_text(painter, q, f"{r:g} m", 10 * ls, col, bold=False)

    # Cielo -------------------------------------------------------------------------------
    def _sky(self, painter, P, sky, sun, ls) -> None:
        color = sun["color"]
        if sky.band is not None and len(sky.band):
            q = sky.band.reshape(-1, 3)
            xy, ok = P(q)
            xy = xy.reshape(-1, 4, 2)
            ok = ok.reshape(-1, 4).all(axis=1)
            path = QPainterPath()
            path.setFillRule(Qt.WindingFill)
            for quad, good in zip(xy, ok):
                if good:
                    path.addPolygon(QPolygonF([QPointF(*c) for c in quad]))
                    path.closeSubpath()
            painter.setPen(Qt.NoPen)
            painter.setBrush(qcolor(color, 0.16))
            painter.drawPath(path)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(qcolor(color, 0.38), 1.0))
        for run in sky.analemmas:
            _polyline(painter, *P(run))
        for _m, runs, dashed in sky.months:
            pen = QPen(qcolor(color, 0.9), 1.6 if dashed else 2.2,
                       Qt.DashLine if dashed else Qt.SolidLine)
            painter.setPen(pen)
            for run in runs:
                _polyline(painter, *P(run))
        xy, ok = P(sky.mark_pts)
        painter.setPen(Qt.NoPen)
        painter.setBrush(qcolor(color))
        for (x, y), good in zip(xy, ok):
            if good:
                painter.drawEllipse(QPointF(x, y), 2.6, 2.6)
        if sun.get("labels", True):
            xy, ok = P(sky.mark_labels)
            for (h, _p), (x, y), good in zip(sky.marks, xy, ok):
                if good:
                    _halo_text(painter, QPointF(x, y), str(h), 10 * ls,
                               "#ffffff")
            for text, p in sky.month_labels:
                q = P.one(p)
                if q is not None:
                    _label(painter, q, text, 11 * ls, color="#111111",
                           bg=color)

    def _sun(self, painter, P, s, view, doy, ls) -> None:
        sun = view["sun"]
        fr = solar.Frame(view["center"], view["radius"], s["north"])
        if sun.get("day", True):
            painter.setPen(QPen(qcolor(sun["ball"], 0.95), 1.8, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            for run in self.day(s, view, doy):
                _polyline(painter, *P(run))
        alt, az = self.sitio.sun_now()
        if alt <= -2:
            return
        p = fr.sky(alt, az, lift=0.6)
        q = P.one(p)
        if q is None:
            return
        rpx = max(5.0, min(40.0, P.pixel_size(p, fr.r * 0.022)))
        glow = QRadialGradient(q, rpx * 3.6)
        glow.setColorAt(0.0, qcolor(sun["ball"], 0.55))
        glow.setColorAt(1.0, qcolor(sun["ball"], 0.0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(q, rpx * 3.6, rpx * 3.6)
        painter.setBrush(qcolor(sun["ball"]))
        painter.drawEllipse(q, rpx, rpx)
        if sun.get("ray", True) and alt > 0:
            c = P.one((fr.cx, fr.cy, fr.cz + 0.6))
            g = P.one(fr.ground(az, fr.r, 0.5))
            painter.setBrush(Qt.NoBrush)
            if c is not None:
                painter.setPen(QPen(qcolor(sun["ball"], 0.8), 1.5, Qt.DashLine))
                painter.drawLine(q, c)
                if g is not None:
                    painter.setPen(QPen(qcolor(sun["ball"], 0.5), 1.3,
                                        Qt.DashLine))
                    painter.drawLine(c, g)

    # Viento -----------------------------------------------------------------------------
    def _wind(self, painter, P, fr, wv, winds, rec, ls) -> None:
        arrows = []
        if wv["show"]:
            for w in winds:
                if w.get("on", True):
                    try:
                        d, sp = float(w["dir"]), float(w["speed"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    freq = w.get("freq")
                    label = f"{w.get('label', '')} · {sp:.1f} m/s"
                    if freq:
                        label += f" · {freq}%"
                    arrows.append((d, sp, w.get("color", "#b5d16b"), label,
                                   False, 0.92))
        if wv.get("hourly") and rec and rec["ws"] >= 0.3:
            arrows.append((rec["wd"], rec["ws"], HOURLY_WIND,
                           f"Viento a esta hora · {rec['ws']:.1f} m/s",
                           True, 0.85))
        if not arrows:
            return
        t = self.sitio.phase() if wv.get("anim", True) else 0.0
        anim = 1.0 if wv.get("anim", True) else 0.0
        for d, sp, col, label, single, opa in arrows:
            self._arrow(painter, P, fr, wv, d, sp, col, label, single, opa,
                        t, anim, ls)

    def _arrow(self, painter, P, fr, wv, d, speed, col, label, single, opa,
               t, anim, ls) -> None:
        R = fr.r
        o = math.radians(d)
        cx, cy = fr.grid_to_local(math.sin(o), math.cos(o))      # hacia el origen del viento
        lx, ly = fr.grid_to_local(math.cos(o), -math.sin(o))     # transversal
        c, l = np.array([cx, cy]), np.array([lx, ly])
        width = (0.01 + min(speed, 10.0) * 0.004) * R * float(wv.get("width", 1))
        z = fr.cz + float(wv.get("height", 8.0))
        far, near = R * 1.22, R * (0.18 if single else 0.42)
        offsets = [0] if single else [-1, 0, 1]
        center = np.array([fr.cx, fr.cy])
        base = QColor(col)
        for g, p in enumerate(offsets):
            m = p * R * 0.1
            A = c * (far - abs(p) * R * 0.08) + l * (m * 1.5)
            Y = c * (near + abs(p) * R * 0.07) + l * (m * 0.75)
            bend = (0 if single else R * 0.06) * (0.6 if g == 1 else
                                                  1 if g == 0 else -1)
            B = A + (Y - A) * 0.33 + l * bend
            C = A + (Y - A) * 0.66 - l * (bend * 0.7)
            hw = width * (1.1 if p == 0 else 0.85)
            self._ribbon(painter, P, center, z, (A, B, C, Y), hw, base,
                         g * 0.37 + d * 0.01, opa, t, anim)
        if label:
            q = P.one((fr.cx + c[0] * (far + R * 0.06),
                       fr.cy + c[1] * (far + R * 0.06), z + R * 0.02))
            if q is not None:
                _label(painter, q, label, 11 * ls, color="#ffffff",
                       bg=QColor(12, 18, 32, 190), border=col, bold=True,
                       pad=4)

    @staticmethod
    def _ribbon(painter, P, center, z, ctrl, hw, base, phase, opa, t,
                anim) -> None:
        """La cinta del flujo: se ensancha, termina en punta de flecha y
        late a lo largo (como el sombreador de la app web)."""
        A, B, C, D = ctrl
        prof = [(k / 48 * 0.8, hw * (0.25 + 0.75 * min(1.0, k / 48 * 0.8 / 0.45)))
                for k in range(49)]
        prof += [(0.8, hw * 2.5), (1.0, 0.001)]

        def bez(u):
            a = (1 - u) ** 3
            b = 3 * (1 - u) ** 2 * u
            c = 3 * (1 - u) * u * u
            d = u ** 3
            return a * A + b * B + c * C + d * D

        def tan(u):
            u = min(u, 0.999)
            return (3 * (1 - u) ** 2 * (B - A) + 6 * (1 - u) * u * (C - B)
                    + 3 * u * u * (D - C))

        left, right, us = [], [], []
        for u, w in prof:
            pt = bez(u) + center
            tg = tan(u)
            n = np.hypot(*tg)
            perp = np.array([-tg[1], tg[0]]) / (n if n > 1e-12 else 1.0)
            left.append((*(pt + perp * w / 2), z))
            right.append((*(pt - perp * w / 2), z))
            us.append(u)
        xy, ok = P(left + right)
        k = len(prof)
        L, Rr, okl, okr = xy[:k], xy[k:], ok[:k], ok[k:]
        painter.setPen(Qt.NoPen)
        for i in range(1, k):
            if not (okl[i] and okl[i - 1] and okr[i] and okr[i - 1]):
                continue
            u = us[i]
            a = min(1.0, u / 0.35) * 0.95
            s = 0.55 + 0.45 * _smooth(0.2, 1.0, math.sin(
                (u * 4.0 - t * 0.9 + phase) * 2 * math.pi))
            kk = 1.0 + (s - 1.0) * anim
            col = QColor(base)
            col.setAlphaF(max(0.0, min(1.0, a * opa * kk)))
            painter.setBrush(col)
            painter.drawPolygon(QPolygonF([
                QPointF(*L[i - 1]), QPointF(*L[i]), QPointF(*Rr[i]),
                QPointF(*Rr[i - 1])]))

    def _rose(self, painter, P, fr, wv, stats, ls) -> None:
        cols = clima.rose_colors(wv.get("pal", "amber"), wv.get("c0"),
                                 wv.get("c1"))
        t = fr.r * float(wv.get("scale", 0.62))
        opa = float(wv.get("opa", 0.8))
        sums = [sum(r) for r in stats["rose"]]
        mx = max(max(sums), 1)
        z = 0.5
        painter.setPen(QPen(QColor(15, 20, 30, 120), 0.8))
        for d in range(16):
            acc = 0
            az = d * 22.5
            for b, n in enumerate(stats["rose"][d]):
                if not n:
                    continue
                r0 = t * 0.06 + acc / mx * t
                r1 = t * 0.06 + (acc + n) / mx * t - t * 0.004
                acc += n
                arc_o = [fr.ground(az - 8.5 + 17 * k / 8, r1, z)
                         for k in range(9)]
                arc_i = [fr.ground(az + 8.5 - 17 * k / 8, max(r0, 0.01), z)
                         for k in range(9)]
                xy, ok = P(arc_o + arc_i)
                if not ok.all():
                    continue
                painter.setBrush(qcolor(cols[b], opa))
                painter.drawPolygon(QPolygonF([QPointF(*q) for q in xy]))
        ring = [fr.ground(a, t * 1.06, z) for a in range(0, 361, 4)]
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 90), 1.0, Qt.DashLine))
        _polyline(painter, *P(ring))
        pct = mx / max(1, stats["total"]) * 100.0
        q = P.one(fr.ground(135.0, t * 1.06, 2.0))
        if q is not None:
            _label(painter, q, f"{pct:.1f}%", 10 * ls,
                   bg=QColor(12, 18, 32, 180))

    # Anotaciones ---------------------------------------------------------------------
    def _pins(self, painter, P, view, pins, ls) -> None:
        sel = self.sitio.selected_pin
        for pin in pins:
            try:
                x, y, z = (float(v) for v in pin["pos"])
            except (KeyError, TypeError, ValueError):
                continue
            st = pin_style(pin, view["annot"])
            top = (x, y, z + st["h"])
            q = P.one(top)
            if q is None:
                continue
            if st["line_on"] and st["h"] > 0:
                g = P.one((x, y, z))
                if g is not None:
                    painter.setPen(QPen(qcolor(st["line"], 0.9), 1.4))
                    painter.drawLine(g, q)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(qcolor(st["line"]))
                    painter.drawEllipse(g, 2.5, 2.5)
            r = max(7.0, min(26.0, P.pixel_size(top, st["size"] / 2.0)))
            icon = pin.get("icon") or ""
            if icon and not _has_glyph(icon, r * 1.15):
                icon = ""                       # sin fuente para ese icono
            if st["bg"]:
                painter.setPen(QPen(qcolor(st["color"]), 2.0))
                painter.setBrush(qcolor(st["bg_color"], 0.92))
                painter.drawEllipse(q, r, r)
            if icon:
                _halo_text(painter, QPointF(q.x(), q.y() + 1), icon, r * 1.15,
                           st["color"], bold=False)
            elif not st["bg"]:
                painter.setPen(Qt.NoPen)
                painter.setBrush(qcolor(st["color"]))
                painter.drawEllipse(q, r * 0.45, r * 0.45)
            else:
                painter.setPen(Qt.NoPen)
                painter.setBrush(qcolor(st["color"]))
                painter.drawEllipse(q, r * 0.38, r * 0.38)
            if pin.get("id") == sel:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(242, 115, 41), 3.0))
                painter.drawEllipse(q, r + 5, r + 5)
            if st["label_on"] and pin.get("label"):
                _label(painter, QPointF(q.x() + r + 2, q.y()), pin["label"],
                       12 * ls, color=st["text"],
                       bg=QColor(12, 18, 32, 170) if st["bg"] else None,
                       align="right")

    def _building(self, painter, P, st) -> None:
        ctx = st.store.context()
        view = st.store.view()
        for b in ctx.get("buildings", []):
            if b.get("id") != st.selected_building:
                continue
            h = float(b.get("h", 9.0)) * float(view["ctx"]["hscale"])
            z = float(b.get("z0", 0.0))
            pts = [(p[0], p[1], z + h + 0.05) for p in b["pts"]]
            pts.append(pts[0])
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(242, 115, 41), 3.0))
            _polyline(painter, *P(pts))
            base = [(p[0], p[1], z + 0.05) for p in b["pts"]]
            base.append(base[0])
            painter.setPen(QPen(QColor(242, 115, 41, 160), 1.5, Qt.DashLine))
            _polyline(painter, *P(base))
            return

    # Escala de la radiación ----------------------------------------------------
    def _rad_legend(self, painter, viewport, view, ls, notes_box=None) -> None:
        from . import radiacion as rad
        st = self.sitio
        if not view.get("rad", {}).get("legend", True):
            return
        g = rad.visible_map(st.scene, st.store.key)
        if g is None:
            return
        data = g.ext.get(st.store.key) or {}
        shadows = data.get("kind") == "shadows"
        pal = rad.scale(data)["palette"]
        pad, bw, bh = 12 * ls, 18 * ls, 230 * ls
        title_f, small_f = _font(13 * ls, True), _font(10.5 * ls)
        lines = [str(data.get("period", "")), str(data.get("source", ""))]
        title = (tr("Horas de sol (asoleamiento)") if data.get("show") == "sun"
                 else tr("Horas de sombra")) if shadows else tr("Radiación incidente")
        unit = ("h en el intervalo (Δt {} min)".format(
                    data.get("period", "").split("Δt ")[-1].split(" min")[0])
                if shadows else "kWh/m² en el periodo")
        if shadows:
            lines = lines[:1]
        fm = QFontMetricsF(small_f)
        width = max(150 * ls, max(fm.horizontalAdvance(t) for t in lines)
                    + 2 * pad)
        width = max(width, fm.horizontalAdvance(unit) + 2 * pad)
        height = pad + 18 * ls + 14 * ls + bh + 14 * ls + 2 * 14 * ls + pad
        W, H = viewport.width(), viewport.height()
        x, y = W - width - 14, 14
        if notes_box is not None and notes_box.right() > x and notes_box.top() < 40:
            if notes_box.bottom() + 10 + height <= H - 14:
                y = notes_box.bottom() + 10          # debajo del de información
                width = max(width, notes_box.width())
                x = notes_box.right() - width
            else:
                x = notes_box.left() - width - 10    # a su izquierda
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
        painter.setBrush(QColor(11, 18, 32, 215))
        painter.drawRoundedRect(QRectF(x, y, width, height), 8, 8)
        cy = y + pad
        painter.setFont(title_f)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(x + pad, cy, width - 2 * pad, 18 * ls),
                         Qt.AlignLeft | Qt.AlignVCenter, title)
        cy += 18 * ls
        painter.setFont(small_f)
        painter.setPen(QColor("#9fb0cc"))
        painter.drawText(QRectF(x + pad, cy, width - 2 * pad, 14 * ls),
                         Qt.AlignLeft | Qt.AlignVCenter, unit)
        cy += 14 * ls + 6 * ls
        bx = x + pad
        sc = rad.scale(data)
        if sc["stepped"]:
            # Una caja por clase, de la mayor (arriba) a la menor.
            n = sc["n"]
            for i in range(n + 1):
                if sc["shadows"]:
                    # c = clase de horas de sombra. Con «horas de sol» arriba
                    # va lo más soleado y el rótulo dice las horas de sol.
                    c = i if sc["sun"] else n - i
                    rgb = rad.color_at(c / n, pal)
                    label = (f"{max(0.0, sc['total'] - c):g} h" if sc["sun"]
                             else f"{c} h")
                else:
                    c = n - i
                    span = (sc["hi"] - sc["lo"]) / n
                    disp = sc["lo"] + span * (c + 0.5)
                    label = f"{sc['lo'] + span * c:,.0f}"
                    rgb = rad.color_at(float(rad.scale_t(sc, [disp])[0]), pal)
                yy = cy + bh * i / (n + 1)
                hh = bh / (n + 1)
                painter.setPen(QPen(QColor(255, 255, 255, 90), 0.8))
                painter.setBrush(QColor(*rgb))
                painter.drawRect(QRectF(bx, yy + 0.5, bw, hh - 1))
                painter.setPen(QColor("#dbe4f3"))
                painter.drawText(QRectF(bx + bw + 7, yy, width, hh),
                                 Qt.AlignLeft | Qt.AlignVCenter, label)
        else:
            m = 48
            for i in range(m):
                v = sc["hi"] - (sc["hi"] - sc["lo"]) * i / m
                rgb = rad.color_at(float(rad.scale_t(sc, [v])[0]), pal)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(*rgb))
                painter.drawRect(QRectF(bx, cy + bh * i / m, bw, bh / m + 0.6))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 120), 1.0))
            painter.drawRect(QRectF(bx, cy, bw, bh))
            painter.setPen(QColor("#dbe4f3"))
            for k in range(6):
                yy = cy + bh * k / 5
                val = sc["hi"] - (sc["hi"] - sc["lo"]) * k / 5
                painter.drawLine(QPointF(bx + bw, yy), QPointF(bx + bw + 4, yy))
                painter.drawText(QRectF(bx + bw + 7, yy - 8 * ls, width, 16 * ls),
                                 Qt.AlignLeft | Qt.AlignVCenter,
                                 f"{val:.1f} h" if shadows else f"{val:,.1f}")
        cy += bh + 10 * ls
        painter.setPen(QColor("#9fb0cc"))
        for t in lines:
            painter.drawText(QRectF(x + pad, cy, width - 2 * pad, 14 * ls),
                             Qt.AlignLeft | Qt.AlignVCenter, t)
            cy += 14 * ls

    # Notas -----------------------------------------------------------------------
    def _notes(self, painter, viewport, view, ls):
        rows = notes_rows(self.sitio)
        if not rows:
            return
        pad = 12 * ls
        width = 270 * ls
        fh = 13 * ls
        heights = {"title": fh * 1.35 + 6, "sub": fh + 4, "kv": fh + 5,
                   "sep": 10, "leg": fh + 5, "hint": None}
        fm = QFontMetricsF(_font(11 * ls))
        total = pad
        hint_h = {}
        for i, r in enumerate(rows):
            if r["t"] == "hint":
                br = fm.boundingRect(QRectF(0, 0, width - 2 * pad, 1000),
                                     Qt.TextWordWrap, r["s"])
                hint_h[i] = br.height() + 6
                total += hint_h[i]
            else:
                total += heights[r["t"]]
        total += pad - 4
        W, H = viewport.width(), viewport.height()
        corner = view["view"].get("notes_corner", "tr")
        x = W - width - 14 if corner.endswith("r") else 14
        y = 14 if corner.startswith("t") else H - total - 14
        box = QRectF(x, y, width, total)
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
        painter.setBrush(QColor(11, 18, 32, 215))
        painter.drawRoundedRect(box, 8, 8)
        cy = y + pad
        for i, r in enumerate(rows):
            t = r["t"]
            if t == "title":
                painter.setFont(_font(fh * 1.2, True))
                painter.setPen(QColor("#ffffff"))
                painter.drawText(QRectF(x + pad, cy, width - 2 * pad, fh * 1.4),
                                 Qt.AlignLeft | Qt.AlignVCenter, r["s"])
                cy += heights[t]
            elif t == "sub":
                painter.setFont(_font(fh * 0.95))
                painter.setPen(QColor("#9fb0cc"))
                painter.drawText(QRectF(x + pad, cy, width - 2 * pad, fh + 2),
                                 Qt.AlignLeft | Qt.AlignVCenter, r["s"])
                cy += heights[t]
            elif t == "kv":
                painter.setFont(_font(fh * 0.9))
                painter.setPen(QColor("#8696b4"))
                rect = QRectF(x + pad, cy, width - 2 * pad, fh + 3)
                painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, r["k"])
                painter.setPen(QColor("#dbe4f3"))
                painter.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, r["v"])
                cy += heights[t]
            elif t == "sep":
                painter.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
                painter.drawLine(QPointF(x + pad, cy + 4),
                                 QPointF(x + width - pad, cy + 4))
                cy += heights[t]
            elif t == "leg":
                sx, sy = x + pad, cy + (fh + 3) / 2
                legend_symbol(painter, r["sym"], r["c"], sx, sy, fh)
                painter.setFont(_font(fh * 0.9))
                painter.setPen(QColor("#dbe4f3"))
                painter.drawText(QRectF(sx + 26, cy, width - 2 * pad - 26,
                                        fh + 3),
                                 Qt.AlignLeft | Qt.AlignVCenter, r["s"])
                cy += heights[t]
            elif t == "hint":
                painter.setFont(_font(11 * ls))
                painter.setPen(QColor("#f5d08a"))
                painter.drawText(QRectF(x + pad, cy, width - 2 * pad,
                                        hint_h[i]), Qt.TextWordWrap, r["s"])
                cy += hint_h[i]
        return box


_GLYPHS: dict = {}


def _has_glyph(text: str, px: float) -> bool:
    """¿Hay una fuente que dibuje el icono? Si no, se dibuja un punto en
    vez de un círculo vacío (sistemas sin fuente de emoji)."""
    if text not in _GLYPHS:
        fm = QFontMetricsF(_font(px))
        _GLYPHS[text] = all(fm.inFontUcs4(ord(c)) for c in text
                            if ord(c) not in (0xFE0F, 0x200D))
    return _GLYPHS[text]


def _smooth(e0, e1, x) -> float:
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def legend_symbol(painter, sym, color, x, y, fh) -> None:
    c = qcolor(color)
    painter.setBrush(Qt.NoBrush)
    if sym == "dot":
        painter.setPen(Qt.NoPen)
        painter.setBrush(c)
        painter.drawEllipse(QPointF(x + 9, y), 4, 4)
    elif sym == "dash":
        painter.setPen(QPen(c, 2.0, Qt.DashLine))
        painter.drawLine(QPointF(x, y), QPointF(x + 20, y))
    elif sym == "band":
        painter.setPen(Qt.NoPen)
        painter.setBrush(qcolor(color, 0.35))
        painter.drawRect(QRectF(x, y - 5, 20, 10))
    elif sym == "n":
        painter.setPen(Qt.NoPen)
        painter.setBrush(c)
        painter.drawPolygon(QPolygonF([QPointF(x + 10, y - 7),
                                       QPointF(x + 15, y + 6),
                                       QPointF(x + 10, y + 3),
                                       QPointF(x + 5, y + 6)]))
    elif sym == "arrow":
        painter.setPen(QPen(c, 3.0))
        painter.drawLine(QPointF(x, y), QPointF(x + 14, y))
        painter.setPen(Qt.NoPen)
        painter.setBrush(c)
        painter.drawPolygon(QPolygonF([QPointF(x + 20, y), QPointF(x + 12, y - 5),
                                       QPointF(x + 12, y + 5)]))
    elif sym == "ring":
        painter.setPen(QPen(c, 1.5, Qt.DashLine))
        painter.drawEllipse(QPointF(x + 10, y), 7, 7)
    elif sym == "line":
        painter.setPen(QPen(qcolor(color, 0.6), 1.2))
        painter.drawLine(QPointF(x, y + 4), QPointF(x + 20, y - 4))
    elif sym == "pin":
        painter.setPen(QPen(c, 1.2))
        painter.drawLine(QPointF(x + 10, y + 7), QPointF(x + 10, y))
        painter.setPen(QPen(c, 2.0))
        painter.setBrush(QColor(14, 22, 38))
        painter.drawEllipse(QPointF(x + 10, y - 2), 5, 5)
    elif sym == "rose":
        painter.setPen(Qt.NoPen)
        painter.setBrush(qcolor(color, 0.85))
        painter.drawPie(QRectF(x + 2, y - 8, 16, 16), 60 * 16, 60 * 16)
        painter.drawPie(QRectF(x + 5, y - 5, 10, 10), 200 * 16, 50 * 16)
    elif sym == "box":
        painter.setPen(QPen(c.darker(150), 1.0))
        painter.setBrush(c)
        painter.drawRect(QRectF(x + 3, y - 6, 14, 12))


def pin_style(pin: dict, annot: dict) -> dict:
    """El estilo de una anotación: el suyo, o el de nuevas anotaciones."""
    out = dict(annot)
    for k in annot:
        if k in pin and pin[k] is not None:
            out[k] = pin[k]
    try:
        out["h"] = float(out["h"])
        out["size"] = float(out["size"])
    except (TypeError, ValueError):
        out["h"], out["size"] = 6.0, 16.0
    return out


def notes_rows(sitio) -> list[dict]:
    """Las filas del recuadro de notas (también las usa la exportación)."""
    view = sitio.store.view()
    s = sitio.site()
    doy, hour = sitio.when()
    m, d = solar.month_day(doy)
    alt, az = sitio.sun_now()
    rise, sset = sitio.sun_times()
    tz = s["tz"]
    rows = [{"t": "title", "s": view["view"].get("notes_title")
             or tr("ANÁLISIS DE SITIO")}]
    name = view.get("site_name") or ""
    if name:
        rows.append({"t": "sub", "s": name})
    rows += [
        {"t": "kv", "k": tr("Ubicación"),
         "v": f"{s['lat']:.4f}, {s['lon']:.4f}" + ("" if s["located"]
                                                   else f" ({tr('sin ubicar')})")},
        {"t": "kv", "k": tr("Fecha"), "v": f"{d} {clima.MONTHS_LONG[m - 1]}"},
        {"t": "kv", "k": tr("Hora"),
         "v": f"{solar.fmt_hour(hour)} (UTC{'+' if tz >= 0 else ''}{tz})"},
        {"t": "kv", "k": tr("Sol"), "v": f"alt {alt:.1f}° · az {az:.1f}°"},
        {"t": "kv", "k": tr("Amanecer / ocaso"),
         "v": f"{solar.fmt_hour(rise)} / {solar.fmt_hour(sset)}"},
    ]
    sh = sitio.shadows()
    if sh is not None and sh.enabled:
        rows.append({"t": "kv", "k": tr("Sombras"), "v": tr("proyectadas")})
    rec = sitio.hour_record()
    if rec:
        rows.append({"t": "kv", "k": tr("Clima a esta hora"),
                     "v": f"{rec['t']:.1f} °C · {rec['rh']:.0f} % · "
                          f"{rec['ws']:.1f} m/s"})
    rows.append({"t": "sep"})
    rows += [dict(r, t="leg") for r in legend_rows(sitio, counts=True)]
    stats = sitio.stats()
    if stats:
        rows.append({"t": "sep"})
        rows.append({"t": "kv", "k": tr("T media (periodo)"),
                     "v": f"{stats['avg_t']:.1f} °C"})
        rows.append({"t": "kv", "k": tr("HR media"), "v": f"{stats['avg_rh']:.0f} %"})
        rows.append({"t": "kv", "k": tr("Calmas"), "v": f"{stats['calm'] * 100:.1f} %"})
    return rows


def legend_rows(sitio, counts: bool = False) -> list[dict]:
    """Convenciones de lo que está visible: ``{"sym", "c", "s"}``. Sólo
    símbolo y nombre — los valores (velocidades, número de edificios,
    paso de los anillos) están en sus secciones del panel; ``counts`` los
    añade para el cuadro de la imagen exportada, que va sin panel."""
    view = sitio.store.view()
    if not view.get("on", True):
        return []
    sun = view["sun"]
    rows = []
    if sun.get("diagram", True):
        if sun.get("months"):
            rows.append({"sym": "dot", "c": sun["color"],
                         "s": tr("Trayectorias y horas de sol (día 21)")})
        if sun.get("band"):
            rows.append({"sym": "band", "c": sun["color"],
                         "s": tr("Banda solar anual")})
        if sun.get("analemma"):
            rows.append({"sym": "line", "c": sun["color"],
                         "s": tr("Analemas horarios")})
    rows.append({"sym": "dot", "c": sun["ball"], "s": tr("Sol en la fecha y hora")})
    if sun.get("day", True):
        rows.append({"sym": "dash", "c": sun["ball"], "s": tr("Trayectoria del día")})
    if view["view"].get("compass", True):
        rows.append({"sym": "n", "c": "#ff5a4f", "s": tr("Norte geográfico")})
    rings = view["rings"]
    if rings.get("n", 0):
        txt = tr("Anillos de distancia")
        if counts:
            txt += f" (cada {float(rings['step']):g} m)"
        rows.append({"sym": "ring", "c": rings["color"], "s": txt})
    wv = view["wind"]
    if wv["show"]:
        for w in sitio.store.content()["winds"]:
            if w.get("on", True):
                txt = str(w.get("label", "") or "Viento")
                if counts:
                    txt += f" ({float(w.get('speed', 0)):.1f} m/s"
                    txt += f", {w['freq']}%)" if w.get("freq") else ")"
                rows.append({"sym": "arrow", "c": w.get("color", "#fff"),
                             "s": txt})
    stats = sitio.stats()
    if wv.get("hourly") and stats:
        rows.append({"sym": "arrow", "c": HOURLY_WIND, "s": tr("Viento a esta hora")})
    if wv.get("rose") and stats:
        cols = clima.rose_colors(wv.get("pal", "amber"), wv.get("c0"),
                                 wv.get("c1"))
        rows.append({"sym": "rose", "c": cols[2], "s": tr("Rosa de vientos 3D")})
    ctx = sitio.store.context()
    if ctx.get("buildings"):
        txt = tr("Edificios de contexto (OSM)")
        if counts:
            txt += f" — {len(ctx['buildings'])}"
        rows.append({"sym": "box", "c": view["ctx"]["color"], "s": txt})
    if sitio.store.content()["pins"]:
        rows.append({"sym": "pin", "c": view["annot"]["color"],
                     "s": tr("Anotaciones")})
    return rows
