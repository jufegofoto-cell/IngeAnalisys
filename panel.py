# SPDX-License-Identifier: GPL-3.0-or-later
"""La pestaña «Análisis de sitio» de la bandeja lateral.

Siete secciones, las mismas y en el mismo orden que la extensión de
SketchUp — 1 Ubicación (clima EPW, sitio, norte y periodo de análisis),
2 Diagrama solar, 3 Rosa de los vientos, 4 Datos climáticos,
5 Visualización, 6 Radiación solar y sombras y 7 Exportar imagen — plegables (``FoldSection``) y estrechas, como pide
docs/plugins.md para una pestaña que se queda abierta mientras se modela.
"""
from __future__ import annotations

import math

from PySide6.QtCore import (QEvent, QObject, QRectF, QSignalBlocker, Qt, QTimer,
                            Signal)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QColorDialog, QComboBox,
    QDial, QDoubleSpinBox, QSizePolicy,
    QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSlider, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from views.fold_section import FoldSection, narrow, wrapping_form

from . import acciones as act
from . import clima, exportar, fuentes, herramientas, solar
from .fuentes import FONTS
from .dibujo import legend_rows
from .graficos import MonthChart, RoseChart

_SET = "analisis_sitio/"

def _section(title: str, key: str, default_open: bool = False) -> FoldSection:
    return FoldSection(title, _SET + "open_" + key, default_open=default_open)


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: palette(mid); font-size: 11px;")
    return lbl


def _kv(rows) -> str:
    return ("<table width='100%' cellspacing='0' cellpadding='1'>"
            + "".join(f"<tr><td style='color:gray'>{k}</td>"
                      f"<td align='right'>{v}</td></tr>" for k, v in rows)
            + "</table>")


class ColorButton(QPushButton):
    colorChanged = Signal(str)

    def __init__(self, color: str = "#ffffff", parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(34, 22)
        self._color = color
        self._paint()
        self.clicked.connect(self._pick)

    def color(self) -> str:
        return self._color

    def setColor(self, c: str) -> None:              # noqa: N802
        self._color = c or "#ffffff"
        self._paint()

    def _paint(self) -> None:
        self.setStyleSheet(f"QPushButton {{ background: {self._color}; "
                           "border: 1px solid #7f8a99; border-radius: 3px; }")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self._color), self, "Color")
        if c.isValid():
            self.setColor(c.name())
            self.colorChanged.emit(self._color)


def _dspin(lo, hi, step=1.0, dec=1, suffix="") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(dec)
    if suffix:
        s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


def _ispin(lo, hi, suffix="") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    if suffix:
        s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


def _row(*widgets, stretch_last=False) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    for i, w in enumerate(widgets):
        if isinstance(w, str):
            w = QLabel(w)
        h.addWidget(w, 1 if (stretch_last and i == len(widgets) - 1) else 0)
    if not stretch_last:
        h.addStretch(1)
    return h


class _WheelGuard(QObject):
    """La rueda del ratón desplaza la pestaña, no cambia valores: un
    desplegable o una casilla numérica sólo responden a la rueda después de
    hacer clic en ellos (evita cambiar la zona horaria, el norte o el centro
    del diagrama sin querer al recorrer el panel)."""

    def __init__(self, scroll) -> None:
        super().__init__(scroll)
        self.scroll = scroll

    def eventFilter(self, obj, ev) -> bool:          # noqa: N802
        if ev.type() == QEvent.Wheel and not obj.hasFocus():
            sb = self.scroll.verticalScrollBar()
            sb.setValue(sb.value() - int(ev.angleDelta().y() / 120.0
                                         * sb.singleStep() * 3))
            return True
        return False


class LegendWidget(QWidget):
    """Las convenciones de lo que se ve en la vista (antes, el recuadro
    flotante). Sólo símbolos y nombres: los valores están en sus secciones."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows = []
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def set_rows(self, rows) -> None:
        self.rows = rows
        self.setFixedHeight(max(20, 21 * len(rows) + 4))
        self.update()

    def paintEvent(self, _ev) -> None:              # noqa: N802
        from PySide6.QtGui import QPainter
        from .dibujo import legend_symbol
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        text = self.palette().color(self.palette().ColorRole.Text)
        if not self.rows:
            p.setPen(text)
            p.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter,
                       "Nada visible en la vista.")
        for i, r in enumerate(self.rows):
            y = 2 + i * 21 + 10
            legend_symbol(p, r["sym"], r["c"], 4, y, 13)
            p.setPen(text)
            p.drawText(QRectF(34, y - 10, self.width() - 36, 20),
                       Qt.AlignLeft | Qt.AlignVCenter, r["s"])
        p.end()


class SitePanel(QWidget):
    """El panel. ``refresh()`` lee el documento; cada control escribe."""

    def __init__(self, sitio, overlay) -> None:
        super().__init__()
        self.sitio = sitio
        self.app = sitio.app
        self.overlay = overlay
        self._busy = False
        self._binds = []
        self._geocoder = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        self._lay = QVBoxLayout(body)
        self._lay.setContentsMargins(6, 4, 6, 8)
        self._lay.setSpacing(4)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self._scroll = scroll

        # El orden de la pestaña (1 → 7), igual que en la extensión de SketchUp.
        # El mapa base, el contexto y los volúmenes son los de IngeTrazo.
        self._build_site()          # 1  Ubicación (clima EPW, sitio, norte, periodo)
        self._build_time()          # 2  Diagrama solar (+ fecha, hora y sombras)
        self._build_wind()          # 3  Rosa de los vientos
        self._build_charts()        # 4  Datos climáticos (temperatura y HR)
        self._build_view()          # 5  Visualización (+ convenciones)
        self._build_solar()         # 6  Radiación solar y sombras
        self._build_export()        # 7  Exportar imagen (+ panel flotante)
        self._lay.addStretch(1)
        self._wheel_guard = _WheelGuard(scroll)
        for w in body.findChildren(QWidget):
            if isinstance(w, (QComboBox, QAbstractSpinBox, QSlider, QDial)):
                w.setFocusPolicy(Qt.StrongFocus)
                w.installEventFilter(self._wheel_guard)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(60)
        self._refresh_timer.timeout.connect(self.refresh)
        sitio.changed.connect(self.schedule_refresh)
        self.refresh()

    # ---- Enlaces control ↔ vista ------------------------------------------------
    def _bind(self, w, path, kind=None, after=None, scale=1.0):
        kind = kind or ("check" if isinstance(w, QCheckBox) else
                        "color" if isinstance(w, ColorButton) else
                        "text" if isinstance(w, QLineEdit) else
                        "combo" if isinstance(w, QComboBox) else
                        "slider" if isinstance(w, QSlider) else "spin")
        self._binds.append((w, tuple(path), kind, scale))

        def write(*_a):
            if self._busy:
                return
            v = self.sitio.store.view()
            node = v
            for k in path[:-1]:
                node = node[k]
            if kind == "check":
                val = w.isChecked()
            elif kind == "color":
                val = w.color()
            elif kind == "text":
                val = w.text()
            elif kind == "combo":
                val = w.currentData()
            elif kind == "slider":
                val = w.value() / scale
            else:
                val = w.value()
            node[path[-1]] = val
            self.sitio.store.touch()
            if after:
                after()
            self.sitio.update_animation()

        sig = {"check": w.toggled if kind == "check" else None,
               "color": getattr(w, "colorChanged", None),
               "text": getattr(w, "editingFinished", None),
               "combo": getattr(w, "currentIndexChanged", None),
               "slider": getattr(w, "valueChanged", None),
               "spin": getattr(w, "valueChanged", None)}[kind]
        sig.connect(write)
        return w

    def _read_binds(self) -> None:
        v = self.sitio.store.view()
        for w, path, kind, scale in self._binds:
            node = v
            try:
                for k in path:
                    node = node[k]
            except (KeyError, TypeError):
                continue
            with QSignalBlocker(w):
                if kind == "check":
                    w.setChecked(bool(node))
                elif kind == "color":
                    w.setColor(str(node))
                elif kind == "text":
                    w.setText(str(node))
                elif kind == "combo":
                    i = w.findData(node)
                    w.setCurrentIndex(max(0, i))
                elif kind == "slider":
                    w.setValue(int(round(float(node) * scale)))
                else:
                    w.setValue(type(w.value())(node))

    # ---- Hora y sol ---------------------------------------------------------------
    def _build_time(self) -> None:
        sec = _section("2 · ☀  Diagrama solar", "time", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        # Cabecera: los controles del diagrama solar.
        self._sun_controls(lay)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)
        lay.addWidget(QLabel("<b>Fecha, hora y sombras</b>"))
        g = QGridLayout()
        g.setContentsMargins(0, 0, 0, 0)
        g.addWidget(QLabel("Fecha"), 1, 0)
        self.doy = QSlider(Qt.Horizontal)
        self.doy.setRange(1, 365)
        self.doy.valueChanged.connect(self._on_doy)
        g.addWidget(self.doy, 1, 1)
        self.doy_lbl = QLabel("")
        self.doy_lbl.setMinimumWidth(52)
        g.addWidget(self.doy_lbl, 1, 2)
        g.addWidget(QLabel("Hora"), 2, 0)
        self.hour = QSlider(Qt.Horizontal)
        self.hour.setRange(0, 24 * 4 - 1)
        self.hour.valueChanged.connect(self._on_hour)
        g.addWidget(self.hour, 2, 1)
        self.hour_lbl = QLabel("")
        g.addWidget(self.hour_lbl, 2, 2)
        quick = QGridLayout()
        for i, (txt, doy) in enumerate((("Equinoccio mar", 80),
                                        ("Solsticio jun", 172),
                                        ("Equinoccio sep", 266),
                                        ("Solsticio dic", 355))):
            b = QPushButton(txt)
            b.setStyleSheet("font-size: 11px;")
            b.clicked.connect(lambda _c=False, d=doy: self.sitio.set_when(doy=d))
            quick.addWidget(b, i // 2, i % 2)
        g.addLayout(quick, 3, 0, 1, 3)
        self.anim_btn = QPushButton("▶  Animar recorrido solar")
        self.anim_btn.setCheckable(True)
        self.anim_btn.clicked.connect(self._on_anim)
        g.addWidget(self.anim_btn, 4, 0, 1, 3)
        self.shadow_on = QCheckBox("Sombras proyectadas (sol real)")
        self.shadow_on.toggled.connect(
            lambda on: None if self._busy else self.sitio.set_shadows(on))
        g.addWidget(self.shadow_on, 5, 0, 1, 3)
        g.addWidget(QLabel("Oscuridad"), 6, 0)
        self.shadow_dark = QSlider(Qt.Horizontal)
        self.shadow_dark.setRange(0, 80)
        self.shadow_dark.valueChanged.connect(self._on_dark)
        g.addWidget(self.shadow_dark, 6, 1, 1, 2)
        self.sun_kv = QLabel("")
        self.sun_kv.setTextFormat(Qt.RichText)
        g.addWidget(self.sun_kv, 7, 0, 1, 3)
        lay.addLayout(g)
        self._lay.addWidget(sec)

    def _on_doy(self, v: int) -> None:
        if not self._busy:
            self.sitio.set_when(doy=v)

    def _on_hour(self, v: int) -> None:
        if not self._busy:
            self.sitio.set_when(hour=v / 4.0)

    def _on_dark(self, v: int) -> None:
        if not self._busy:
            sh = self.sitio.shadows()
            self.sitio.set_shadows(sh.enabled if sh else False,
                                   darkness=1.0 - v / 100.0)

    def _on_anim(self) -> None:
        on = self.sitio.toggle_sun_animation()
        self.anim_btn.setChecked(on)

    # ---- 1 · Ubicación (clima EPW, sitio y norte) -------------------------------
    def _build_site(self) -> None:
        sec = _section("1 · ◎  Ubicación", "loc", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        self._epw_block(lay)
        for block in (self._loc_block, self._north_block, self._period_block):
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            lay.addWidget(sep)
            block(lay)
        self._lay.addWidget(sec)

    def _loc_block(self, lay) -> None:
        lay.addWidget(QLabel("<b>Sitio</b>"))
        self.geo_q = QLineEdit()
        self.geo_q.setPlaceholderText("Buscar dirección o pegar «lat, lon»")
        self.geo_q.returnPressed.connect(self._on_search)
        self.geo_btn = QPushButton("Buscar")
        self.geo_btn.clicked.connect(self._on_search)
        lay.addLayout(_row(self.geo_q, self.geo_btn, stretch_last=False))
        self.geo_res = QListWidget()
        self.geo_res.setMaximumHeight(110)
        self.geo_res.hide()
        self.geo_res.itemActivated.connect(self._on_result)
        self.geo_res.itemClicked.connect(self._on_result)
        lay.addWidget(self.geo_res)
        self.pick_btn = QPushButton("Elegir en el mapa…")
        self.pick_btn.clicked.connect(self._on_pick_map)
        lay.addWidget(self.pick_btn)
        form = wrapping_form(None)
        self.site_name = self._bind(QLineEdit(), ["site_name"])
        form.addRow("Nombre", self.site_name)
        self.lat = _dspin(-90, 90, 0.0001, 6)
        self.lon = _dspin(-180, 180, 0.0001, 6)
        form.addRow("Latitud", self.lat)
        form.addRow("Longitud", self.lon)
        self.apply_btn = QPushButton("Aplicar coordenadas")
        self.apply_btn.clicked.connect(self._on_apply_latlon)
        form.addRow("", self.apply_btn)
        self.tz = QComboBox()
        self.tz.addItem("Automática (por longitud)", None)
        for off in range(-12, 15):
            self.tz.addItem(f"UTC{off:+03d}:00", off)
        self.tz.currentIndexChanged.connect(
            lambda _i: None if self._busy else self.sitio.set_tz(
                self.tz.currentData()))
        form.addRow("Zona horaria", self.tz)
        lay.addLayout(form)
        narrow(self.tz, self.lat, self.lon)
        self.loc_hint = _hint("")
        lay.addWidget(self.loc_hint)

    def _on_search(self) -> None:
        q = self.geo_q.text().strip()
        if not q:
            return
        import re
        m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$", q)
        if m:
            self._set_site(float(m.group(1)), float(m.group(2)), "Coordenadas")
            return
        if self._geocoder is None:
            from georef.geocode import Geocoder
            self._geocoder = Geocoder(self)
            self._geocoder.resultsReady.connect(self._on_places)
            self._geocoder.failed.connect(
                lambda msg: self._flash(f"Búsqueda fallida: {msg}"))
        self.geo_res.clear()
        self.geo_res.addItem("Buscando…")
        self.geo_res.show()
        self._geocoder.search(q)

    def _on_places(self, places) -> None:
        self.geo_res.clear()
        if not places:
            self.geo_res.addItem("Sin resultados")
        for p in places:
            it = QListWidgetItem(p.name)
            it.setData(Qt.UserRole, (p.lat, p.lon, p.name))
            it.setToolTip(f"{p.lat:.5f}, {p.lon:.5f}")
            self.geo_res.addItem(it)
        self.geo_res.show()

    def _on_result(self, it) -> None:
        data = it.data(Qt.UserRole)
        if data:
            lat, lon, name = data
            self._set_site(lat, lon, ", ".join(name.split(",")[:2]).strip())
            self.geo_res.hide()

    def _set_site(self, lat, lon, name=None, tz=None, alt=None) -> bool:
        d = self.sitio.datum()
        if d is not None:
            dist = math.hypot((lat - d.lat) * 111320.0,
                              (lon - d.lon) * 111320.0 * math.cos(math.radians(lat)))
            if dist > 1.0:
                ans = QMessageBox.question(
                    self, "Mover el origen del proyecto",
                    "El proyecto ya tiene ubicación. El punto nuevo pasa a ser "
                    "el origen (0,0): lo dibujado conserva sus coordenadas "
                    f"locales y aparece en el sitio nuevo (a {dist:,.0f} m)."
                    "\n\n¿Mover el origen?")
                if ans != QMessageBox.Yes:
                    return False
        self.sitio.set_location(lat, lon, alt=alt, tz=tz)
        if name and not self.sitio.store.view().get("site_name"):
            self.sitio.store.view()["site_name"] = name
        self.refresh()
        return True

    def _on_pick_map(self) -> None:
        from georef.tiles import DEFAULT_SOURCE_ID, PRESETS
        from views.location_dialog import pick_location
        s = self.sitio.site()
        res = pick_location(PRESETS[DEFAULT_SOURCE_ID], s["lat"], s["lon"],
                            self.app.window)
        if res is not None:
            self._set_site(res[0], res[1])

    def _on_apply_latlon(self) -> None:
        self._set_site(self.lat.value(), self.lon.value())

    # ---- Clima (EPW), en la sección 1 ----------------------------------------------
    def _epw_block(self, lay) -> None:
        lay.addWidget(QLabel("<b>Clima (archivo EPW)</b>"))
        b = QPushButton("Abrir archivo EPW…")
        b.clicked.connect(self._on_open_epw)
        lay.addWidget(b)
        self.epw_none = _hint("Con un EPW, la ubicación del sitio es la de su "
                              "estación. Sin EPW, escríbela abajo. EPW gratis en "
                              "climate.onebuilding.org o energyplus.net/weather; "
                              "se guarda en el .igz.")
        lay.addWidget(self.epw_none)
        self.epw_box = QWidget()
        el = QVBoxLayout(self.epw_box)
        el.setContentsMargins(0, 4, 0, 0)
        self.epw_kv = QLabel("")
        self.epw_kv.setTextFormat(Qt.RichText)
        el.addWidget(self.epw_kv)
        b = QPushButton("Quitar EPW")
        b.clicked.connect(lambda: act.clear_epw(self.sitio))
        el.addWidget(b)
        lay.addWidget(self.epw_box)

    # ---- Periodo de análisis, al final de la sección 1 ---------------------------------
    def _period_block(self, lay) -> None:
        lay.addWidget(QLabel("<b>Periodo de análisis</b>"))
        per = QGridLayout()
        self.m0, self.m1 = QComboBox(), QComboBox()
        for i, m in enumerate(clima.MONTHS):
            self.m0.addItem(m, i + 1)
            self.m1.addItem(m, i + 1)
        self.h0, self.h1 = _ispin(0, 23), _ispin(1, 24)
        self._bind(self.m0, ["period", "m0"], after=self._refresh_climate)
        self._bind(self.m1, ["period", "m1"], after=self._refresh_climate)
        self._bind(self.h0, ["period", "h0"], after=self._refresh_climate)
        self._bind(self.h1, ["period", "h1"], after=self._refresh_climate)
        per.addWidget(QLabel("Meses"), 0, 0)
        per.addWidget(self.m0, 0, 1)
        per.addWidget(self.m1, 0, 2)
        per.addWidget(QLabel("Horas"), 1, 0)
        per.addWidget(self.h0, 1, 1)
        per.addWidget(self.h1, 1, 2)
        narrow(self.m0, self.m1)
        lay.addLayout(per)
        lay.addWidget(_hint("Las horas del EPW que cuentan para las estadísticas, "
                            "la rosa de los vientos, los vientos dominantes y la "
                            "radiación del «Periodo de análisis»."))
        self.epw_stats = QLabel("")
        self.epw_stats.setTextFormat(Qt.RichText)
        self.epw_stats.setWordWrap(True)
        lay.addWidget(self.epw_stats)

    # ---- Norte, en la sección 1 ------------------------------------------------------
    def _north_block(self, lay) -> None:
        lay.addWidget(QLabel("<b>Norte del proyecto</b>"))
        top = QHBoxLayout()
        self.north_dial = QDial()
        self.north_dial.setRange(0, 359)
        self.north_dial.setWrapping(True)
        self.north_dial.setNotchesVisible(True)
        self.north_dial.setNotchTarget(15)
        self.north_dial.setFixedSize(84, 84)
        # QDial con vuelta: 0 abajo y crece en sentido horario; aquí la
        # perilla apunta adonde queda el norte (0° = arriba = +Y).
        self.north_dial.valueChanged.connect(
            lambda v: self._on_north((v + 180) % 360))
        top.addWidget(self.north_dial)
        col = QVBoxLayout()
        self.north = _dspin(-180, 360, 0.5, 1, "°")
        self.north.setWrapping(True)
        self.north.valueChanged.connect(self._on_north)
        col.addWidget(QLabel("Ángulo (horario desde +Y)"))
        col.addWidget(self.north)
        row = QHBoxLayout()
        for t, d in (("0°", 0.0), ("−15°", -15.0), ("+15°", 15.0)):
            b = QPushButton(t)
            b.setStyleSheet("font-size: 11px;")
            if d == 0.0:
                b.clicked.connect(lambda: self._on_north(0.0))
            else:
                b.clicked.connect(lambda _c=False, d=d: self._on_north(
                    self.sitio.site()["north"] + d))
            row.addWidget(b)
        col.addLayout(row)
        top.addLayout(col, 1)
        lay.addLayout(top)
        b = QPushButton("Desde la arista seleccionada (apunta al norte)")
        b.setToolTip("Selecciona una línea del modelo dibujada hacia el norte "
                     "geográfico (de sur a norte) y pulsa.")
        b.clicked.connect(self._on_north_from_edge)
        lay.addWidget(b)
        self.north_hint = _hint("")
        lay.addWidget(self.north_hint)

    def _on_north(self, deg) -> None:
        if self._busy:
            return
        if self.sitio.datum() is None:
            self._flash("Ubica primero el proyecto (sección 1).")
            self.refresh()
            return
        deg = ((float(deg) + 180.0) % 360.0) - 180.0
        self.sitio.set_north(deg)
        self.refresh()

    def _on_north_from_edge(self) -> None:
        from core.mesh import Edge
        edges = [e for e in self.sitio.scene.selection if isinstance(e, Edge)]
        if len(edges) != 1:
            self._flash("Selecciona una sola arista que apunte al norte.")
            return
        e = edges[0]
        a, b = e.v0.position, e.v1.position
        dx, dy = b.x() - a.x(), b.y() - a.y()
        if math.hypot(dx, dy) < 1e-6:
            self._flash("La arista es vertical: no indica una dirección en planta.")
            return
        if dy < 0 or (abs(dy) < 1e-9 and dx < 0):
            dx, dy = -dx, -dy           # se toma el sentido más cercano a +Y
        self._on_north(math.degrees(math.atan2(dx, dy)))

    # ---- 4 · Datos climáticos ---------------------------------------------------------
    def _build_charts(self) -> None:
        sec = _section("4 · ◔  Datos climáticos", "charts", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        self.charts_none = _hint("Carga un archivo EPW (sección 1) para ver la "
                                 "temperatura y la humedad relativa mensual.")
        lay.addWidget(self.charts_none)
        self.charts_box = QWidget()
        el = QVBoxLayout(self.charts_box)
        el.setContentsMargins(0, 0, 0, 0)
        el.addWidget(QLabel("<b>Temperatura y humedad relativa mensual</b>"))
        self.months_chart = MonthChart()
        el.addWidget(self.months_chart)
        b = QPushButton("Guardar gráficos (PNG)…")
        b.setToolTip("Guarda la rosa de los vientos y el gráfico mensual.")
        b.clicked.connect(self._on_save_charts)
        el.addWidget(b)
        lay.addWidget(self.charts_box)
        self._lay.addWidget(sec)

    def _on_open_epw(self) -> None:
        path, _f = QFileDialog.getOpenFileName(
            self, "Abrir archivo de clima", "", "EnergyPlus Weather (*.epw)")
        if path:
            self.open_epw(path)

    def open_epw(self, path: str, ask: bool = True) -> bool:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            info = act.load_epw_text(self.sitio, text)
        except (OSError, clima.EpwError) as exc:
            QMessageBox.warning(self, "Archivo EPW", str(exc))
            return False
        # La ubicación del sitio es la de la estación del EPW.
        tz = info.get("tz")
        tz = None if tz is None or math.isnan(tz) else tz
        if ask:
            self._set_site(info["lat"], info["lon"],
                           name=", ".join(x for x in (info["city"],
                                                      info["country"]) if x),
                           tz=tz)
        else:
            self.sitio.set_location(info["lat"], info["lon"], tz=tz)
        self._flash(f"Clima de {info['city'] or 'la estación'} cargado.")
        self.refresh()
        return True

    def _on_dominant(self) -> None:
        try:
            act.set_winds(self.sitio, act.dominant_winds(self.sitio))
        except act.SiteError as exc:
            self._flash(str(exc))

    def _on_save_charts(self) -> None:
        path, _f = QFileDialog.getSaveFileName(
            self, "Guardar gráficos", "clima.png", "Imagen PNG (*.png)")
        if path:
            try:
                out = exportar.export_charts(self.sitio, path)
                self._flash("Guardados: " + ", ".join(p.split("/")[-1] for p in out))
            except act.SiteError as exc:
                self._flash(str(exc))

    def _refresh_climate(self) -> None:
        epw = self.sitio.epw()
        # Sin EPW, la ubicación se escribe a mano; con EPW es la de su estación.
        manual = epw is None
        for w in (self.geo_q, self.geo_btn, self.pick_btn, self.lat, self.lon,
                  self.apply_btn, self.tz):
            w.setEnabled(manual)
        if not manual:
            self.geo_res.hide()
            self.loc_hint.setText(
                "Ubicación y zona horaria tomadas del archivo EPW (estación "
                f"{epw['info'].get('city') or '—'}). Quita el EPW para "
                "escribirlas a mano.")
        self.epw_box.setVisible(epw is not None)
        self.epw_stats.setVisible(epw is not None)
        self.epw_none.setVisible(epw is None)
        self.charts_box.setVisible(epw is not None)
        self.charts_none.setVisible(epw is None)
        self.rose_box.setVisible(epw is not None)
        self.rose_none.setVisible(epw is None)
        self.dominant_btn.setEnabled(epw is not None)
        for i in range(self.pal_custom.count()):
            w = self.pal_custom.itemAt(i).widget()
            if w is not None:
                w.setEnabled(self.pal.currentData() == "custom")
        if epw is None:
            return
        info = epw["info"]
        self.epw_kv.setText(_kv([
            ("Estación", f"{info.get('city', '')} {info.get('country', '')}"),
            ("WMO", info.get("wmo", "") or "—"),
            ("Lat / Lon", f"{info['lat']:.3f}, {info['lon']:.3f}"),
            ("Huso", f"UTC{info['tz']:+g}" if not math.isnan(info.get("tz", math.nan))
             else "—"),
            ("Elevación", f"{info.get('elev', 0):.0f} m")]))
        s = self.sitio.stats()
        if s is None:
            return
        dom = "<br>".join(
            f"{clima.DIR16_LONG[d]} {s['freq'][d] * 100:.1f}% · "
            f"{s['avg_sp'][d]:.1f} m/s" for d in s["top"]) or "—"
        self.epw_stats.setText(_kv([
            ("Horas", s["total"]),
            ("T media", f"{s['avg_t']:.1f} °C ({s['t_min']:.1f} a {s['t_max']:.1f})"),
            ("HR media", f"{s['avg_rh']:.0f} %"),
            ("Viento medio", f"{s['avg_ws']:.1f} m/s"),
            ("Calmas", f"{s['calm'] * 100:.1f} %"),
            ("Dominantes", dom)]))
        w = self.sitio.store.view()["wind"]
        cols = clima.rose_colors(w.get("pal"), w.get("c0"), w.get("c1"))
        self.rose.set_data(s, cols)
        self.months_chart.set_data(s)

    # ---- Diagrama solar -----------------------------------------------------------
    def _sun_controls(self, lay) -> None:
        """Cabecera de la sección 2: cómo se dibuja el diagrama en la vista."""
        self.diagram_chk = self._bind(QCheckBox("Mostrar diagrama solar"),
                                      ["sun", "diagram"], after=self._on_diagram)
        self.diagram_chk.setToolTip(
            "Al activarlo muestra la banda anual, los analemas, las etiquetas, "
            "la trayectoria del día y el rayo solar. Sin él no se dibujan la "
            "brújula (puntos cardinales y grados) ni los anillos.")
        lay.addWidget(self.diagram_chk)
        form = wrapping_form(None)
        self.radius = self._bind(_dspin(5, 5000, 5, 0, " m"), ["radius"])
        form.addRow("Radio del diagrama", self.radius)
        self.cx, self.cy, self.cz = (_dspin(-1e6, 1e6, 1, 2) for _ in range(3))
        for i, w in enumerate((self.cx, self.cy, self.cz)):
            w.valueChanged.connect(lambda v, i=i: self._on_center(i, v))
        c = QHBoxLayout()
        for w in (self.cx, self.cy, self.cz):
            c.addWidget(w)
        narrow(self.cx, self.cy, self.cz)
        form.addRow("Centro (X, Y, Z)", c)
        lay.addLayout(form)
        row = QHBoxLayout()
        b = QPushButton("Elegir centro…")
        b.setToolTip("Clic en el modelo (con inferencias) para centrar el diagrama.")
        b.clicked.connect(self._on_pick_center)
        row.addWidget(b)
        b = QPushButton("Origen")
        b.clicked.connect(lambda: self._set_center((0.0, 0.0, 0.0)))
        row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(QLabel("Trayectorias (día 21 de cada mes)"))
        grid = QGridLayout()
        self.month_checks = []
        for i, m in enumerate(clima.MONTHS):
            cb = QCheckBox(m)
            cb.toggled.connect(self._on_months)
            grid.addWidget(cb, i // 4, i % 4)
            self.month_checks.append(cb)
        lay.addLayout(grid)
        for text, key in (("Banda anual (solsticio a solsticio)", "band"),
                          ("Analemas horarios", "analemma"),
                          ("Etiquetas de horas y meses", "labels")):
            lay.addWidget(self._bind(QCheckBox(text), ["sun", key]))
        # «Trayectoria del día» gobierna el sol del momento y su rayo: al
        # desmarcarla se ocultan los tres y el rayo queda desmarcado.
        self.day_chk = self._bind(QCheckBox("Trayectoria del día seleccionado"),
                                  ["sun", "day"], after=self._on_day)
        self.day_chk.setToolTip("Incluye el sol de la fecha y hora; al "
                                "desmarcarla se ocultan el sol y su rayo.")
        lay.addWidget(self.day_chk)
        self.ray_chk = self._bind(QCheckBox("Rayo solar al centro"), ["sun", "ray"])
        lay.addWidget(self.ray_chk)
        # Colores: líneas del diagrama, relleno de la banda y sol.
        self.sun_color = self._bind(ColorButton(), ["sun", "color"])
        self.sun_color.setToolTip("Líneas del diagrama: trayectorias, analemas, "
                                  "puntos de las horas y brújula.")
        self.sun_fill = self._bind(ColorButton(), ["sun", "fill"])
        self.sun_fill.setToolTip("Relleno de la banda solar anual, que destaca "
                                 "la carta solar.")
        self.sun_ball = self._bind(ColorButton(), ["sun", "ball"])
        self.sun_ball.setToolTip("Color del sol, su rayo y la trayectoria del día.")
        lay.addLayout(_row("Líneas", self.sun_color, "Relleno", self.sun_fill,
                           "Sol", self.sun_ball))
        # Base del diagrama: brújula y anillos (antes en Visualización).
        self.compass_chk = self._bind(QCheckBox("Brújula y grados"),
                                      ["view", "compass"])
        lay.addWidget(self.compass_chk)
        rf = wrapping_form(None)
        self.ring_n = self._bind(_ispin(0, 20), ["rings", "n"])
        self.ring_step = self._bind(_dspin(1, 1000, 5, 0, " m"), ["rings", "step"])
        self.ring_color = self._bind(ColorButton(), ["rings", "color"])
        self.ring_color.setToolTip("Color de los anillos concéntricos.")
        rf.addRow("Anillos", _row(self.ring_n, QLabel("cada"), self.ring_step,
                                  self.ring_color))
        lay.addLayout(rf)
        # Textos del gráfico: horas, meses, grados, puntos cardinales, anillos.
        box = QGroupBox("Textos del diagrama")
        bl = wrapping_form(box)
        self.font_cb = QComboBox()
        for i, fam in enumerate(FONTS):
            self.font_cb.addItem(fam, fam)
            # Cada opción se muestra en su propia fuente.
            self.font_cb.setItemData(i, fuentes.qfont(fam, 13), Qt.FontRole)
        self.font_cb.setToolTip(
            "Fuentes libres (licencia OFL) incluidas en el módulo.")
        bl.addRow("Fuente", self._bind(self.font_cb, ["sun", "font"]))
        self.font_size = self._bind(_dspin(4, 40, 1, 0), ["sun", "font_size"])
        self.font_size.setToolTip("10 = tamaño normal.")
        bl.addRow("Tamaño", _row(self.font_size,
                                 self._bind(QCheckBox("Negrita"),
                                            ["sun", "font_bold"])))
        self.text_color = self._bind(ColorButton(), ["sun", "text"])
        self.text_color.setToolTip("Color de las horas, los meses, los grados, "
                                   "los puntos cardinales y las distancias de los "
                                   "anillos (la N va en rojo).")
        bl.addRow("Color", self.text_color)
        narrow(self.font_cb)
        lay.addWidget(box)

    def _on_diagram(self) -> None:
        """Activar el diagrama enciende todas sus partes (luego se pueden
        apagar una a una)."""
        sun = self.sitio.store.view()["sun"]
        if sun.get("diagram"):
            for k in ("band", "analemma", "labels", "day", "ray"):
                sun[k] = True
            self.sitio.store.touch()
        self.refresh()

    def _on_day(self) -> None:
        v = self.sitio.store.view()
        if not v["sun"].get("day", True) and v["sun"].get("ray", True):
            v["sun"]["ray"] = False
            self.sitio.store.touch()
        self.refresh()

    def _on_months(self) -> None:
        if self._busy:
            return
        v = self.sitio.store.view()
        v["sun"]["months"] = [i + 1 for i, cb in enumerate(self.month_checks)
                              if cb.isChecked()]
        self.sitio.store.touch()

    def _on_center(self, i, val) -> None:
        if self._busy:
            return
        v = self.sitio.store.view()
        c = list(v["center"])
        c[i] = float(val)
        v["center"] = c
        self.sitio.store.touch()

    def _set_center(self, p) -> None:
        self.sitio.store.view()["center"] = [float(p[0]), float(p[1]), float(p[2])]
        self.sitio.store.touch()
        self.refresh()

    def _on_pick_center(self) -> None:
        tool = herramientas.make_pick_tool(
            "Clic donde va el centro del diagrama",
            lambda p, _c: self._set_center((p.x(), p.y(), p.z())),
            lambda: herramientas.end(self.app))
        herramientas.begin(self.app, tool, "Clic donde va el centro del diagrama")

    # ---- 3 · Rosa de los vientos ---------------------------------------------------------------
    def _build_wind(self) -> None:
        sec = _section("3 · ≋  Rosa de los vientos", "wind", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        # Rosa de los vientos (EPW) y su color, primero.
        self.rose_none = _hint("Carga un archivo EPW (sección 1) para ver la "
                               "rosa de los vientos.")
        lay.addWidget(self.rose_none)
        self.rose_box = QWidget()
        rl = QVBoxLayout(self.rose_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("<b>Rosa de los vientos</b> (periodo de la sección 1)"))
        self.rose = RoseChart()
        rl.addWidget(self.rose)
        form = wrapping_form(None)
        self.pal = QComboBox()
        for k, (name, _c) in clima.PALETTES.items():
            self.pal.addItem(name, k)
        self._bind(self.pal, ["wind", "pal"], after=self._refresh_climate)
        form.addRow("Color", self.pal)
        c0 = self._bind(ColorButton(), ["wind", "c0"], after=self._refresh_climate)
        c1 = self._bind(ColorButton(), ["wind", "c1"], after=self._refresh_climate)
        self.pal_custom = _row(c0, QLabel("→"), c1)
        form.addRow("Calma → fuerte", self.pal_custom)
        narrow(self.pal)
        rl.addLayout(form)
        lay.addWidget(self.rose_box)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)
        # En la vista.
        for text, key in (("Rosa de vientos 3D sobre el suelo (requiere EPW)", "rose"),
                          ("Flecha del viento horario (EPW, en azul)", "hourly"),
                          ("Flechas de viento", "show"), ("Animar flujo", "anim")):
            lay.addWidget(self._bind(QCheckBox(text), ["wind", key]))
        form = wrapping_form(None)
        form.addRow("Opacidad rosa 3D", self._bind(_dspin(0.15, 1, 0.05, 2),
                                                ["wind", "opa"]))
        form.addRow("Tamaño rosa 3D", self._bind(_dspin(0.2, 1.3, 0.02, 2, " × R"),
                                              ["wind", "scale"]))
        form.addRow("Altura flechas", self._bind(_dspin(0, 200, 1, 1, " m"),
                                                 ["wind", "height"]))
        form.addRow("Grosor", self._bind(_dspin(0.3, 3, 0.1, 1),
                                         ["wind", "width"]))
        lay.addLayout(form)
        lay.addWidget(QLabel("Direcciones (de dónde viene el viento)"))
        self.winds = QTableWidget(0, 5)
        self.winds.setHorizontalHeaderLabels(["", "Etiqueta", "Dir °", "m/s", ""])
        hh = self.winds.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (0, 2, 3, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.winds.verticalHeader().hide()
        self.winds.setMaximumHeight(150)
        self.winds.itemChanged.connect(self._on_wind_item)
        self.winds.cellDoubleClicked.connect(self._on_wind_dbl)
        lay.addWidget(self.winds)
        row = QHBoxLayout()
        b = QPushButton("+ Añadir")
        b.clicked.connect(self._on_wind_add)
        row.addWidget(b)
        b = QPushButton("Quitar")
        b.clicked.connect(self._on_wind_del)
        row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(_hint("Doble clic en el color para cambiarlo."))
        self.dominant_btn = QPushButton("Aplicar vientos dominantes (EPW)")
        self.dominant_btn.clicked.connect(self._on_dominant)
        lay.addWidget(self.dominant_btn)
        self._lay.addWidget(sec)

    def _fill_winds(self) -> None:
        winds = self.sitio.store.content()["winds"]
        with QSignalBlocker(self.winds):
            self.winds.setRowCount(len(winds))
            for r, w in enumerate(winds):
                on = QTableWidgetItem()
                on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                on.setCheckState(Qt.Checked if w.get("on", True) else Qt.Unchecked)
                self.winds.setItem(r, 0, on)
                self.winds.setItem(r, 1, QTableWidgetItem(str(w.get("label", ""))))
                self.winds.setItem(r, 2, QTableWidgetItem(f"{float(w.get('dir', 0)):g}"))
                self.winds.setItem(r, 3, QTableWidgetItem(f"{float(w.get('speed', 0)):g}"))
                col = QTableWidgetItem("   ")
                col.setFlags(Qt.ItemIsEnabled)
                col.setBackground(QColor(w.get("color", "#ffffff")))
                self.winds.setItem(r, 4, col)

    def _winds_from_table(self) -> list:
        old = self.sitio.store.content()["winds"]
        out = []
        for r in range(self.winds.rowCount()):
            base = dict(old[r]) if r < len(old) else {}
            try:
                d = float(self.winds.item(r, 2).text().replace(",", "."))
                sp = float(self.winds.item(r, 3).text().replace(",", "."))
            except (AttributeError, ValueError):
                d, sp = float(base.get("dir", 0)), float(base.get("speed", 0))
            base.update({"on": self.winds.item(r, 0).checkState() == Qt.Checked,
                         "label": self.winds.item(r, 1).text(),
                         "dir": d % 360.0, "speed": max(0.0, sp)})
            out.append(base)
        return out

    def _on_wind_item(self, _it) -> None:
        if not self._busy:
            act.set_winds(self.sitio, self._winds_from_table())

    def _on_wind_dbl(self, r, c) -> None:
        if c != 4:
            return
        winds = self._winds_from_table()
        col = QColorDialog.getColor(QColor(winds[r].get("color", "#ffffff")), self)
        if col.isValid():
            winds[r]["color"] = col.name()
            act.set_winds(self.sitio, winds)

    def _on_wind_add(self) -> None:
        from .datos import WIND_COLORS
        winds = self._winds_from_table()
        winds.append({"on": True, "label": f"Viento {len(winds) + 1}",
                      "dir": 90.0, "speed": 2.0,
                      "color": WIND_COLORS[len(winds) % len(WIND_COLORS)]})
        act.set_winds(self.sitio, winds)

    def _on_wind_del(self) -> None:
        r = self.winds.currentRow()
        winds = self._winds_from_table()
        if 0 <= r < len(winds):
            del winds[r]
            act.set_winds(self.sitio, winds)

    # ---- Vista ------------------------------------------------------------------
    def _build_view(self) -> None:
        sec = _section("5 · ◐  Visualización", "view", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        self.on = self._bind(QCheckBox("Mostrar el análisis en la vista"), ["on"])
        lay.addWidget(self.on)
        form = wrapping_form(None)
        form.addRow("Escala de etiquetas (vientos y paneles)",
                    self._bind(_dspin(0.4, 2.5, 0.1, 1), ["view", "label_scale"]))
        lay.addLayout(form)
        lay.addWidget(QLabel("<b>Convenciones</b>"))
        self.legend = LegendWidget()
        lay.addWidget(self.legend)
        self._lay.addWidget(sec)

    # ---- 7 · Exportar ------------------------------------------------------------
    def _build_export(self) -> None:
        sec = _section("7 · ⇩  Exportar imagen", "export")
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        form = wrapping_form(None)
        self.res = QComboBox()
        for t, w in (("FHD (1920)", 1920), ("2K (2560)", 2560),
                     ("4K (3840)", 3840), ("5K (5120)", 5120)):
            self.res.addItem(t, w)
        form.addRow("Resolución", self.res)
        narrow(self.res)
        lay.addLayout(form)
        b = QPushButton("Exportar imagen (PNG)…")
        b.clicked.connect(self._on_png)
        lay.addWidget(b)
        lay.addWidget(_hint("La imagen es la vista actual con el análisis encima, "
                            "incluido el panel de información si está visible."))
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)
        form = wrapping_form(None)
        form.addRow("Título del panel", self._bind(QLineEdit(),
                                                   ["view", "notes_title"]))
        corner = QComboBox()
        for k, t in (("tr", "Arriba derecha"), ("tl", "Arriba izquierda"),
                     ("br", "Abajo derecha"), ("bl", "Abajo izquierda")):
            corner.addItem(t, k)
        form.addRow("Posición", self._bind(corner, ["view", "notes_corner"]))
        narrow(corner)
        lay.addLayout(form)
        # Oculto al iniciar IngeTrazo: es un ajuste de la sesión, no del
        # documento (un .igz no lo vuelve a encender al abrirse).
        self.notes_on = QCheckBox("Mostrar panel flotante de información y "
                                  "convenciones")
        self.notes_on.toggled.connect(self._on_notes)
        lay.addWidget(self.notes_on)
        self._lay.addWidget(sec)

    def _on_notes(self, on) -> None:
        if self._busy:
            return
        self.sitio.show_notes = bool(on)
        self.app.viewport.update()

    def _ask_save(self, title, name, filt):
        path, _f = QFileDialog.getSaveFileName(self, title, name, filt)
        return path

    def _stem(self) -> str:
        n = self.sitio.store.view().get("site_name") or "sitio"
        return "".join(c if c.isalnum() else "_" for c in n).strip("_") or "sitio"

    def _on_png(self) -> None:
        path = self._ask_save("Exportar imagen", self._stem() + "_analisis.png",
                              "Imagen PNG (*.png)")
        if path:
            try:
                exportar.export_png(self.sitio, self.overlay, path,
                                    self.res.currentData())
                self._flash(f"Imagen guardada: {path}")
            except act.SiteError as exc:
                self._flash(str(exc))

    # ---- 6 · Radiación solar y sombras ------------------------------------------------
    def _build_solar(self) -> None:
        from . import radiacion as rad
        sec = _section("6 · ☀  Radiación solar y sombras", "solar", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        r, d = ["rad"], ["sd"]
        # Común: qué superficies y con qué detalle.
        form = wrapping_form(None)
        tgt = QComboBox()
        for k, t in (("all", "Todo el modelo"),
                     ("sel", "Selección (caras o grupos)"),
                     ("none", "Sólo el suelo")):
            tgt.addItem(t, k)
        form.addRow("Superficies", self._bind(tgt, r + ["target"]))
        resn = QComboBox()
        for k, t in (("faces:1.0", "Por superficie (un color por cara)"),
                     ("cells:2.0", "Malla de 2 m"), ("cells:1.0", "Malla de 1 m"),
                     ("cells:0.5", "Malla de 0.5 m")):
            resn.addItem(t, k)
        resn.currentIndexChanged.connect(self._on_rad_res)
        self.rad_res = resn
        form.addRow("Resolución", resn)
        self.ground_r = _dspin(0, 2000, 10, 0, " m")
        self.ground_r.setSpecialValueText("automático")
        self._bind(self.ground_r, r + ["ground_r"])
        form.addRow(self._bind(QCheckBox("Suelo alrededor"), r + ["ground"]),
                    _row(QLabel("margen"), self.ground_r))
        narrow(tgt, resn)
        lay.addLayout(form)
        # Radiación.
        lay.addWidget(QLabel("<b>Radiación solar incidente</b>"))
        form = wrapping_form(None)
        per = QComboBox()
        for k, t in (("day", "Día de la sección 2"), ("month", "Un mes"),
                     ("period", "Periodo de análisis (sección 1)"),
                     ("year", "Año completo")):
            per.addItem(t, k)
        self.rad_per = self._bind(per, r + ["period"], after=self._sync_rad)
        form.addRow("Periodo", per)
        mon = QComboBox()
        for i, m in enumerate(clima.MONTHS_LONG):
            mon.addItem(m.capitalize(), i + 1)
        self.rad_month = self._bind(mon, r + ["month"])
        self.rad_month_lbl = QLabel("Mes")
        form.addRow(self.rad_month_lbl, mon)
        form.addRow("Albedo del suelo", self._bind(_dspin(0, 0.9, 0.05, 2),
                                                   r + ["albedo"]))
        narrow(per, mon)
        lay.addLayout(form)
        self.rad_src = _hint("")
        lay.addWidget(self.rad_src)
        b = QPushButton("☀  Calcular radiación")
        b.clicked.connect(self._on_rad)
        lay.addWidget(b)
        # Sombras.
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)
        lay.addWidget(QLabel("<b>Estudio de sombras</b>"))
        self.sd_date = _hint("")
        lay.addWidget(self.sd_date)
        form = wrapping_form(None)
        form.addRow("Desde", self._bind(_ispin(0, 23, ":00 h"), d + ["h0"]))
        form.addRow("Hasta", self._bind(_ispin(1, 24, ":00 h"), d + ["h1"]))
        step = QComboBox()
        for m in (15, 20, 30, 60):
            step.addItem(f"cada {m} min", m)
        form.addRow("Intervalo", self._bind(step, d + ["step"]))
        narrow(step)
        lay.addLayout(form)
        b = QPushButton("◑  Calcular estudio de sombras")
        b.clicked.connect(self._on_sd)
        lay.addWidget(b)
        # Resultado en la vista.
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)
        self.map_box = QWidget()
        ml = QVBoxLayout(self.map_box)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(QLabel("<b>Resultado en la vista</b>"))
        self.map_pick = QComboBox()
        self.map_pick.currentIndexChanged.connect(self._on_map_pick)
        ml.addWidget(self.map_pick)
        self.legend_on = self._bind(
            QCheckBox("Mostrar panel flotante de la escala (radiación / sombras)"),
            r + ["legend"])
        ml.addWidget(self.legend_on)
        self.map_kv = QLabel("")
        self.map_kv.setTextFormat(Qt.RichText)
        self.map_kv.setWordWrap(True)
        ml.addWidget(self.map_kv)
        self.rad_opts = QWidget()
        rl = wrapping_form(self.rad_opts)
        pal = QComboBox()
        for k, (t, _st) in rad.PALETTES.items():
            pal.addItem(t, k)
        self._bind(pal, r + ["palette"], after=self._rad_recolor)
        rl.addRow("Colores", pal)
        rl.addRow(self._bind(QCheckBox("Escala automática (mín–máx)"),
                             r + ["auto"], after=self._rad_recolor))
        self.rad_lo = self._bind(_dspin(0, 1e6, 10, 1, " kWh/m²"), r + ["vmin"],
                                 after=self._rad_recolor)
        self.rad_hi = self._bind(_dspin(0, 1e6, 10, 1, " kWh/m²"), r + ["vmax"],
                                 after=self._rad_recolor)
        rl.addRow("Mínimo", self.rad_lo)
        rl.addRow("Máximo", self.rad_hi)
        narrow(pal)
        ml.addWidget(self.rad_opts)
        self.sd_opts = QWidget()
        sl = wrapping_form(self.sd_opts)
        spal = QComboBox()
        for k, (t, _st) in rad.PALETTES.items():
            spal.addItem(t, k)
        self._bind(spal, d + ["palette"], after=self._sd_recolor)
        sl.addRow("Colores", spal)
        show = QComboBox()
        show.addItem("Horas de sombra", "shade")
        show.addItem("Horas de sol (asoleamiento)", "sun")
        self._bind(show, d + ["show"], after=self._sd_recolor)
        sl.addRow("Mostrar", show)
        narrow(spal, show)
        ml.addWidget(self.sd_opts)
        b = QPushButton("Quitar el resultado mostrado")
        b.clicked.connect(self._on_map_clear)
        ml.addWidget(b)
        lay.addWidget(self.map_box)
        lay.addWidget(_hint(
            "Radiación: directa (con las sombras de todo el modelo y el contexto)"
            " + difusa con la vista real al cielo + reflejada por el suelo. "
            "Sombras: horas en sombra de cada punto, probando el sol en cada "
            "intervalo. Apaga las sombras proyectadas (sección 2) para leer "
            "bien los colores."))
        self._lay.addWidget(sec)

    def _sync_rad(self) -> None:
        on = self.rad_per.currentData() == "month"
        self.rad_month.setVisible(on)
        self.rad_month_lbl.setVisible(on)

    def _on_rad_res(self, _i) -> None:
        if self._busy:
            return
        mode, grid = self.rad_res.currentData().split(":")
        v = self.sitio.store.view()["rad"]
        v["mode"], v["grid"] = mode, float(grid)
        self.sitio.store.touch()

    def _progress(self, title: str):
        from PySide6.QtWidgets import QApplication, QProgressDialog
        dlg = QProgressDialog(title, "Cancelar", 0, 1000, self.app.window)
        dlg.setWindowTitle("Análisis de sitio")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        def cb(frac, text=None):
            dlg.setValue(int(max(0.0, min(1.0, frac)) * 1000))
            if text:
                dlg.setLabelText(f"{title}\n{text}")
            QApplication.processEvents()
            return dlg.wasCanceled()
        return dlg, cb

    def _settings(self, extra: dict) -> dict:
        s = dict(self.sitio.store.view()["rad"])
        if s.get("target") == "none":
            s["target"], s["ground"] = "ground", True
        s.update(extra)
        return s

    def _run_map(self, fn, settings, title, cancelled) -> None:
        dlg, cb = self._progress(title)
        try:
            res = fn(self.sitio, settings, cb)
        except act.SiteError as exc:
            dlg.close()
            self._flash(str(exc))
            return
        dlg.close()
        if res is None:
            self._flash(cancelled)
        self.refresh()

    def _on_rad(self) -> None:
        from . import radiacion as rad
        self._run_map(rad.analyse, self._settings({}),
                      "Calculando radiación incidente…",
                      "Cálculo de radiación cancelado.")

    def _on_sd(self) -> None:
        from . import radiacion as rad
        sd = self.sitio.store.view()["sd"]
        self._run_map(rad.shadow_study,
                      self._settings({"h0": sd["h0"], "h1": sd["h1"],
                                      "step": sd["step"],
                                      "palette": sd.get("palette", "grises"),
                                      "show": sd.get("show", "shade")}),
                      "Estudio de sombras…", "Estudio de sombras cancelado.")

    def _rad_recolor(self) -> None:
        from . import radiacion as rad
        v = self.sitio.store.view()["rad"]
        rad.recolor(self.sitio, "radiation", palette=v["palette"],
                    auto=v["auto"], vmin=v["vmin"], vmax=v["vmax"])
        self.schedule_refresh()

    def _sd_recolor(self) -> None:
        from . import radiacion as rad
        sd = self.sitio.store.view()["sd"]
        rad.recolor(self.sitio, "shadows", palette=sd.get("palette", "grises"),
                    show=sd.get("show", "shade"))
        self.schedule_refresh()

    def _on_map_pick(self, _i) -> None:
        from . import radiacion as rad
        if self._busy:
            return
        rad.show_map(self.sitio, self.map_pick.currentData())
        self.refresh()

    def _on_map_clear(self) -> None:
        from . import radiacion as rad
        from .datos import run_step
        g = rad.visible_map(self.sitio.scene, self.sitio.store.key)
        if g is None:
            return

        def mutate(sc):
            if g in sc.groups:
                sc.groups.remove(g)
            sc.selection.discard(g)

        run_step(self.app.viewport, mutate, message="Resultado quitado.")
        self.refresh()

    def _refresh_rad(self) -> None:
        from . import radiacion as rad
        st = self.sitio
        key = st.store.key
        v = st.store.view()["rad"]
        i = self.rad_res.findData(f"{v['mode']}:{float(v['grid']):.1f}")
        with QSignalBlocker(self.rad_res):
            self.rad_res.setCurrentIndex(max(0, i))
        self._sync_rad()
        self.ground_r.setEnabled(bool(v.get("ground", True)))
        epw = st.epw()
        self.rad_src.setText(
            f"Clima: EPW de {epw['info'].get('city') or 'la estación'} "
            "(DNI y DHI horarias)." if epw else
            "Sin EPW: se usa un cielo despejado teórico (valores máximos "
            "posibles). Carga un EPW en la sección 1 para el clima real.")
        m, d = solar.month_day(st.when()[0])
        self.sd_date.setText(f"Día: {d} de {clima.MONTHS_LONG[m - 1]} (la fecha "
                             "de la sección 2).")
        groups = {k: rad.find_group(st.scene, key, k) for k in rad.KINDS}
        shown = rad.visible_map(st.scene, key)
        self.map_box.setVisible(any(groups.values()))
        with QSignalBlocker(self.map_pick):
            self.map_pick.clear()
            self.map_pick.addItem("Ninguno", None)
            for k, title in (("radiation", "Mapa de radiación solar"),
                             ("shadows", "Estudio de sombras (horas de sombra)")):
                if groups[k] is not None:
                    self.map_pick.addItem(title, k)
            kind = (shown.ext[key]["kind"] if shown is not None else None)
            self.map_pick.setCurrentIndex(max(0, self.map_pick.findData(kind)))
        self.rad_opts.setVisible(kind == "radiation")
        self.sd_opts.setVisible(kind == "shadows")
        self.rad_lo.setEnabled(not v["auto"])
        self.rad_hi.setEnabled(not v["auto"])
        if shown is None:
            self.map_kv.setText("")
            return
        data = shown.ext.get(key) or {}
        sm = data.get("summary") or {}
        if kind == "radiation":
            rows = [("Periodo", data.get("period", "—")),
                    ("Clima", data.get("source", "—"))]
            if sm:
                rows += [("Superficies", f"{sm['faces']} ({sm['area']:,.0f} m²)"),
                         ("Puntos de cálculo", f"{sm['cells']:,} · malla "
                                               f"{sm['grid']:.2g} m"),
                         ("Mínimo / máximo", f"{sm['min']:,.1f} / "
                                             f"{sm['max']:,.1f} kWh/m²"),
                         ("Media (por área)", f"{sm['mean']:,.1f} kWh/m²"),
                         ("Energía incidente", f"{sm['energy'] / 1000:,.1f} MWh"),
                         ("Posiciones del sol", f"{sm['n_sun']} · "
                                                f"{sm['seconds']:.1f} s")]
        else:
            rows = [("Día y pasos", data.get("period", "—")),
                    ("Sol en el intervalo", f"{data.get('hours', 0):.1f} h")]
            if sm:
                a = max(sm["area"], 1e-9)
                rows += [("Superficie analizada", f"{sm['area']:,.0f} m²"),
                         ("Sombra media", f"{sm['mean']:.1f} h"),
                         ("Siempre al sol", f"{sm['full_sun']:,.0f} m² "
                                            f"({sm['full_sun'] / a * 100:.0f} %)"),
                         ("Siempre en sombra", f"{sm['full_shade']:,.0f} m² "
                                               f"({sm['full_shade'] / a * 100:.0f} %)"),
                         ("Puntos de cálculo", f"{sm['cells']:,} · "
                                               f"{sm['seconds']:.1f} s")]
        self.map_kv.setText(_kv(rows))

    # ---- Refresco ------------------------------------------------------------------
    def _flash(self, text: str) -> None:
        self.app.viewport.flash_status(text, 5000)

    def schedule_refresh(self) -> None:
        self._refresh_timer.start()

    def refresh(self) -> None:
        self._busy = True
        try:
            self._read_binds()
            self.notes_on.setChecked(bool(getattr(self.sitio, "show_notes", False)))
            st = self.sitio
            v = st.store.view()
            doy, hour = st.when()
            m, d = solar.month_day(doy)
            self.doy.setValue(doy)
            self.doy_lbl.setText(f"{d} {clima.MONTHS[m - 1]}")
            self.hour.setValue(int(round(hour * 4)) % 96)
            s = st.site()
            self.hour_lbl.setText(f"{solar.fmt_hour(hour)}")
            sh = st.shadows()
            if sh is not None:
                self.shadow_on.setChecked(sh.enabled)
                self.shadow_dark.setValue(int(round((1.0 - sh.darkness) * 100)))
                i = self.tz.findData(sh.utc_offset)
                self.tz.setCurrentIndex(i if i >= 0 else 0)
            alt, az = st.sun_now()
            rise, sset = st.sun_times()
            span = (f"{sset - rise:.1f} h" if rise is not None and sset is not None
                    else "—")
            rows = [("Sol", f"alt {alt:.1f}° · az {az:.1f}°"),
                    ("Amanecer / ocaso",
                     f"{solar.fmt_hour(rise)} / {solar.fmt_hour(sset)}"),
                    ("Horas de sol", span)]
            rec = st.hour_record()
            if rec:
                rows.append(("Clima a esta hora",
                             f"{rec['t']:.1f} °C · {rec['rh']:.0f} % · "
                             f"{rec['ws']:.1f} m/s"))
            self.sun_kv.setText(_kv(rows))
            self.anim_btn.setChecked(st.animating_sun)
            self.anim_btn.setText("■  Detener" if st.animating_sun
                                  else "▶  Animar recorrido solar")
            self.lat.setValue(s["lat"])
            self.lon.setValue(s["lon"])
            self.north.setValue(s["north"])
            self.north_dial.setValue(int(round(s["north"] + 180.0)) % 360)
            for w in (self.north, self.north_dial):
                w.setEnabled(s["located"])
            self.north_hint.setText(
                "Dónde queda el norte geográfico en el modelo. Gira el sol, "
                "el diagrama, el mapa base y el terreno; la geometría no se "
                "mueve." if s["located"] else
                "Ubica primero el proyecto para fijar su norte.")
            self.legend.set_rows(legend_rows(st))
            self.loc_hint.setText(
                "Ubicación del proyecto (datum de IngeTrazo): la usan el sol, "
                "las sombras, el mapa y el terreno." if s["located"] else
                "⚠ El proyecto no tiene ubicación: se usa un sitio por defecto. "
                "Busca la dirección, elige en el mapa o abre un EPW.")
            months = set(int(x) for x in v["sun"]["months"])
            for i, cb in enumerate(self.month_checks):
                cb.setChecked(i + 1 in months)
            cx, cy, cz = v["center"]
            self.cx.setValue(cx)
            self.cy.setValue(cy)
            self.cz.setValue(cz)
            self._fill_winds()
            self.ray_chk.setEnabled(bool(v["sun"].get("day", True)))
        finally:
            self._busy = False
        self._refresh_climate()
        self._refresh_rad()
