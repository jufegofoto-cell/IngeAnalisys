# SPDX-License-Identifier: GPL-3.0-or-later
"""Gráficos del clima: rosa de vientos y clima mensual (QPainter).

Las funciones ``paint_rose`` / ``paint_months`` dibujan en cualquier
rectángulo (el widget del panel, o una imagen al exportar); los widgets
sólo les dan su tamaño y los colores del tema.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import clima
try:
    from .i18n import tr
except ImportError:
    from i18n import tr


def _font(px, bold=False):
    f = QFont()
    f.setPixelSize(max(6, int(round(px))))
    f.setBold(bold)
    return f


def _theme(widget=None):
    pal = widget.palette() if widget is not None else None
    if pal is None:
        return {"bg": QColor("#111a2b"), "text": QColor("#dbe4f3"),
                "mute": QColor("#8696b4"), "line": QColor("#24314d")}
    bg = pal.color(pal.ColorRole.Base)
    text = pal.color(pal.ColorRole.Text)
    mute = QColor(text)
    mute.setAlphaF(0.6)
    line = QColor(text)
    line.setAlphaF(0.18)
    return {"bg": bg, "text": text, "mute": mute, "line": line}


def paint_rose(p: QPainter, rect: QRectF, stats: dict, colors, theme) -> None:
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.fillRect(rect, theme["bg"])
    w, h = rect.width(), rect.height()
    k = min(w, h) / 600.0
    cx, cy = rect.x() + w / 2, rect.y() + h / 2 - 18 * k
    R = min(w, h) * 0.36
    total = max(1, stats["total"])
    sums = [sum(r) for r in stats["rose"]]
    gmax = max(max(sums), 1) / total * 100.0
    step = 10 if gmax > 20 else 5 if gmax > 8 else 2 if gmax > 4 else 1
    p.setPen(QPen(theme["line"], 1.5 * k))
    p.setFont(_font(20 * k))
    v = step
    while v <= gmax + 1e-6:
        r = v / gmax * R
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setPen(theme["mute"])
        p.drawText(QPointF(cx + r * 0.71 + 4, cy - r * 0.71), f"{v}%")
        p.setPen(QPen(theme["line"], 1.5 * k))
        v += step
    for d in range(16):
        a = math.radians(d * 22.5 - 90)
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(a) * R,
                                            cy + math.sin(a) * R))
    for d in range(16):
        acc = 0
        a0 = d * 22.5 - 90 - 9
        for b, n in enumerate(stats["rose"][d]):
            if not n:
                continue
            r0 = acc / total * 100.0 / gmax * R
            acc += n
            r1 = acc / total * 100.0 / gmax * R
            path = QPainterPath()
            outer = QRectF(cx - r1, cy - r1, 2 * r1, 2 * r1)
            inner = QRectF(cx - r0, cy - r0, 2 * r0, 2 * r0)
            # Qt: ángulos en grados, antihorario desde las 3; aquí horario.
            path.arcMoveTo(outer, -a0)
            path.arcTo(outer, -a0, -18)
            if r0 > 0.1:
                path.arcTo(inner, -(a0 + 18), 18)
            else:
                path.lineTo(cx, cy)
            path.closeSubpath()
            p.setPen(QPen(theme["bg"], 2 * k))
            p.setBrush(QColor(colors[b]))
            p.drawPath(path)
    p.setPen(theme["text"])
    p.setFont(_font(24 * k, True))
    for i, t in enumerate(("N", "E", "S", tr("O"))):
        a = math.radians(i * 90 - 90)
        q = QPointF(cx + math.cos(a) * (R + 26 * k), cy + math.sin(a) * (R + 26 * k))
        p.drawText(QRectF(q.x() - 20 * k, q.y() - 15 * k, 40 * k, 30 * k),
                   Qt.AlignCenter, t)
    p.setFont(_font(19 * k))
    x = rect.x() + 20 * k
    y = rect.bottom() - 26 * k
    p.setPen(theme["mute"])
    p.drawText(QRectF(x, y - 12 * k, 44 * k, 24 * k), Qt.AlignVCenter, "m/s")
    x += 44 * k
    fm = p.fontMetrics()
    for i, lab in enumerate(clima.SPEED_LABELS):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(colors[i]))
        p.drawRoundedRect(QRectF(x, y - 9 * k, 18 * k, 18 * k), 4 * k, 4 * k)
        p.setPen(theme["text"])
        p.drawText(QRectF(x + 24 * k, y - 12 * k, 80 * k, 24 * k),
                   Qt.AlignVCenter, lab)
        x += 24 * k + fm.horizontalAdvance(lab) + 16 * k
    p.setPen(theme["mute"])
    p.drawText(QRectF(rect.x(), rect.y() + 8 * k, w - 16 * k, 30 * k),
               Qt.AlignRight | Qt.AlignVCenter,
               f"{tr('Calmas')} {stats['calm'] * 100:.1f}%")
    p.restore()


def paint_months(p: QPainter, rect: QRectF, stats: dict, theme) -> None:
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.fillRect(rect, theme["bg"])
    k = rect.width() / 600.0
    ox, oy = rect.x(), rect.y()
    mon = stats["monthly"]
    left, right, top, bot1, top2, bot2 = 56, 16, 34, 190, 222, 270
    W = 600
    lo = math.floor(min(m["tmin"] for m in mon) / 5) * 5
    hi = math.ceil(max(m["tmax"] for m in mon) / 5) * 5
    if hi <= lo:
        hi = lo + 5

    def X(i):
        return ox + (left + (i + 0.5) * (W - left - right) / 12) * k

    def Y(t):
        return oy + (bot1 - (t - lo) / max(1, hi - lo) * (bot1 - top)) * k

    p.setFont(_font(18 * k))
    for t in range(int(lo), int(hi) + 1, 5):
        p.setPen(QPen(theme["line"], 1))
        p.drawLine(QPointF(ox + left * k, Y(t)), QPointF(ox + (W - right) * k, Y(t)))
        p.setPen(theme["mute"])
        p.drawText(QRectF(ox, Y(t) - 10 * k, (left - 8) * k, 20 * k),
                   Qt.AlignRight | Qt.AlignVCenter, f"{t}°")
    p.setPen(theme["text"])
    p.setFont(_font(19 * k, True))
    p.drawText(QPointF(ox + left * k, oy + 20 * k),
               tr("Temperatura (°C): rango mín–máx y media"))
    bw = (W - left - right) / 12 * 0.42 * k
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(245, 158, 11, 90))
    for i, m in enumerate(mon):
        y0, y1 = Y(m["tmax"]), Y(m["tmin"])
        p.drawRoundedRect(QRectF(X(i) - bw / 2, y0, bw, max(2.0, y1 - y0)),
                          4 * k, 4 * k)
    p.setPen(QPen(QColor("#f59e0b"), 3 * k))
    p.setBrush(Qt.NoBrush)
    pts = [QPointF(X(i), Y(m["t"])) for i, m in enumerate(mon)]
    for a, b in zip(pts, pts[1:]):
        p.drawLine(a, b)
    for q in pts:
        p.setPen(QPen(theme["bg"], 2 * k))
        p.setBrush(QColor("#f59e0b"))
        p.drawEllipse(q, 5 * k, 5 * k)
    p.setPen(theme["text"])
    p.setFont(_font(19 * k, True))
    p.drawText(QPointF(ox + left * k, oy + (top2 - 12) * k),
               tr("Humedad relativa media (%)"))
    span = bot2 - top2
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#60a5fa"))
    for i, m in enumerate(mon):
        hh = m["rh"] / 100.0 * span * k
        p.drawRoundedRect(QRectF(X(i) - bw / 2, oy + bot2 * k - hh, bw, hh),
                          4 * k, 4 * k)
    p.setPen(theme["mute"])
    p.setFont(_font(16 * k))
    for i in range(12):
        p.drawText(QRectF(X(i) - 20 * k, oy + (bot2 + 6) * k, 40 * k, 20 * k),
                   Qt.AlignCenter, clima.MONTHS[i])
    p.restore()


class _Chart(QWidget):
    ratio = 1.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.stats = None
        self.colors = clima.rose_colors("amber")
        sp = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setMinimumWidth(160)

    def hasHeightForWidth(self) -> bool:      # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return int(w * self.ratio)

    def sizeHint(self) -> QSize:              # noqa: N802
        return QSize(280, int(280 * self.ratio))

    def set_data(self, stats, colors=None) -> None:
        self.stats = stats
        if colors:
            self.colors = colors
        self.update()


class RoseChart(_Chart):
    ratio = 1.0

    def paintEvent(self, _ev) -> None:        # noqa: N802
        if not self.stats:
            return
        p = QPainter(self)
        paint_rose(p, QRectF(self.rect()), self.stats, self.colors,
                   _theme(self))
        p.end()


class MonthChart(_Chart):
    ratio = 0.5

    def paintEvent(self, _ev) -> None:        # noqa: N802
        if not self.stats:
            return
        p = QPainter(self)
        paint_months(p, QRectF(self.rect()), self.stats, _theme(self))
        p.end()


def chart_image(kind: str, stats: dict, colors, size: int = 1200) -> QImage:
    """Los gráficos como imagen (para guardarlos o pegarlos en láminas)."""
    h = size if kind == "rose" else size // 2
    img = QImage(size, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("#ffffff"))
    p = QPainter(img)
    theme = {"bg": QColor("#ffffff"), "text": QColor("#1b2639"),
             "mute": QColor("#5f6e88"), "line": QColor("#d6dde9")}
    if kind == "rose":
        paint_rose(p, QRectF(0, 0, size, h), stats, colors, theme)
    else:
        paint_months(p, QRectF(0, 0, size, h), stats, theme)
    p.end()
    return img
