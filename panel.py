# SPDX-License-Identifier: GPL-3.0-or-later
"""La pestaña «Análisis de sitio» de la bandeja lateral.

Las mismas secciones que la app web — hora y sol, ubicación, clima EPW,
diagrama solar, viento, mapa y contexto, masas, anotaciones, vista y
exportar — plegables (``FoldSection``) y estrechas, como pide
docs/plugins.md para una pestaña que se queda abierta mientras se modela.
"""
from __future__ import annotations

import math

from PySide6.QtCore import (QEvent, QObject, QRectF, QSignalBlocker, Qt, QTimer,
                            Signal)
from PySide6.QtGui import QColor, QVector3D
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QCheckBox, QColorDialog, QComboBox,
    QDial, QDoubleSpinBox, QSizePolicy,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSlider, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from views.fold_section import FoldSection, narrow, wrapping_form

try:
    from .i18n import tr, definir_langue_active, obtenir_langue_courante, LANGUES_SUPPORTEES
except ImportError:
    from i18n import tr, definir_langue_active, obtenir_langue_courante, LANGUES_SUPPORTEES
from . import acciones as act
from . import clima, exportar, geometria as geo, herramientas, solar
from .dibujo import legend_rows
from .graficos import MonthChart, RoseChart

_SET = "analisis_sitio/"

ICONS = (
    ("★", "estrella destacado etoile favori star featured"),
    ("📍", "punto marcador point repere pin marker"),
    ("🌳", "árbol parque arbre park tree nature"),
    ("🏠", "vivienda casa maison logement home house"),
    ("🏫", "colegio escuela ecole lycee school education"),
    ("🎓", "universidad universite campus university"),
    ("🏥", "hospital salud hopital sante hospital health"),
    ("✚", "centro de salud dispensaire clinique medical clinic"),
    ("🏛", "institucional monument mairie public civic"),
    ("⛪", "iglesia templo eglise culte church temple"),
    ("🛒", "comercio mercado commerce marche market shop"),
    ("🍴", "restaurante restaurant dining food"),
    ("☕", "café coffee bistro"),
    ("🚌", "bus parada arret bus stop"),
    ("🚇", "metro subway tube"),
    ("🚉", "estación tren gare train station"),
    ("🚲", "bicicleta ciclovía velo piste cyclable bike"),
    ("🅿", "parqueadero parking stationnement car park"),
    ("⛽", "gasolina carburant essence gas station"),
    ("✈", "aeropuerto aeroport avion airport plane"),
    ("⚓", "puerto port marina harbour"),
    ("⚽", "deporte cancha terrain sport stadium"),
    ("💧", "agua eau source water"),
    ("🌊", "río quebrada fleuve riviere river sea"),
    ("⛰", "montaña cerro montagne relief mountain hill"),
    ("🌬", "viento vent air wind"),
    ("☀", "sol soleil solar sun"),
    ("☂", "lluvia pluie rain umbrella"),
    ("🔊", "ruido bruit nuisance sound noise"),
    ("👁", "visual remate vue perspective view landmark"),
    ("⚠", "riesgo amenaza risque danger hazard warning"),
    ("⚡", "energía energie electricite power energy"),
    ("🏭", "industria industrie usine factory industry"),
    ("🗑", "residuos dechets poubelle waste trash"),
    ("♿", "accesible prm accessibilite accessible prm"),
    ("⚑", "bandera hito drapeau repere flag milestone"),
    ("🛈", "información info accueil information"),
)


def _section(title: str, key: str, default_open: bool = False) -> FoldSection:
    return FoldSection(tr(title), _SET + "open_" + key, default_open=default_open)


def _hint(text: str) -> QLabel:
    lbl = QLabel(tr(text))
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: palette(mid); font-size: 11px;")
    return lbl


def _kv(rows) -> str:
    # Traduit automatiquement chaque libellé de ligne (clé k) dans la langue active
    return ("<table width='100%' cellspacing='0' cellpadding='1'>"
            + "".join(f"<tr><td style='color:gray'>{tr(str(k))}</td>"
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
        c = QColorDialog.getColor(QColor(self._color), self, tr("Color"))
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
            w = QLabel(tr(w))
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
                       tr("Nada visible en la vista."))
        for i, r in enumerate(self.rows):
            y = 2 + i * 21 + 10
            legend_symbol(p, r["sym"], r["c"], 4, y, 13)
            p.setPen(text)
            p.drawText(QRectF(34, y - 10, self.width() - 36, 20),
                       Qt.AlignLeft | Qt.AlignVCenter, tr(r["s"]))
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
        self._mass_rows = []
        self._geocoder = None
        self._osm = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        self._scroll = scroll

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(60)
        self._refresh_timer.timeout.connect(self.refresh)
        sitio.changed.connect(self.schedule_refresh)

        # Construction initiale (et reconstructible à chaud lors d'un changement de langue)
        self._build_all()

    def _build_all(self) -> None:
        """Construit ou reconstruit intégralement l'interface du panneau dans la langue active."""
        old_body = self._scroll.takeWidget()
        if old_body is not None:
            old_body.deleteLater()

        self._binds = []
        self._mass_rows = []

        body = QWidget()
        self._lay = QVBoxLayout(body)
        self._lay.setContentsMargins(6, 4, 6, 8)
        self._lay.setSpacing(4)
        self._scroll.setWidget(body)

        # Sélecteur de langue multilingue en haut du panneau
        self._build_lang()
        # Construction des 12 sections repliables dans l'ordre (1 → 12)
        self._build_epw()           # 1  Cargar archivo EPW
        self._build_location()      # 2  Ubicación
        self._build_north()         # 3  Norte del proyecto
        self._build_time()          # 4  Fecha, hora y sombras (+ diagrama solar)
        self._build_wind()          # 5  Vientos
        self._build_charts()        # 6  Rosa de los vientos, temperatura y HR
        self._build_map()           # 7  Mapa base y contexto
        self._build_masses()        # 8  Masas y lotes
        self._build_pins()          # 9  Anotaciones
        self._build_view()          # 10 Visualización (+ convenciones)
        self._build_solar()         # 11 Radiación solar y sombras
        self._build_export()        # 12 Exportar imagen (+ panel flotante)
        self._lay.addStretch(1)

        self._wheel_guard = _WheelGuard(self._scroll)
        for w in body.findChildren(QWidget):
            if isinstance(w, (QComboBox, QAbstractSpinBox, QSlider, QDial)):
                w.setFocusPolicy(Qt.StrongFocus)
                w.installEventFilter(self._wheel_guard)

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
        sec = _section("4 · ☀  Fecha, hora y sombras", "time", True)
        g = QGridLayout(sec.body)
        g.setContentsMargins(6, 6, 6, 6)
        g.addWidget(QLabel(tr("Fecha")), 1, 0)
        self.doy = QSlider(Qt.Horizontal)
        self.doy.setRange(1, 365)
        self.doy.valueChanged.connect(self._on_doy)
        g.addWidget(self.doy, 1, 1)
        self.doy_lbl = QLabel("")
        self.doy_lbl.setMinimumWidth(52)
        g.addWidget(self.doy_lbl, 1, 2)
        g.addWidget(QLabel(tr("Hora")), 2, 0)
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
            b = QPushButton(tr(txt))
            b.setStyleSheet("font-size: 11px;")
            b.clicked.connect(lambda _c=False, d=doy: self.sitio.set_when(doy=d))
            quick.addWidget(b, i // 2, i % 2)
        g.addLayout(quick, 3, 0, 1, 3)
        self.anim_btn = QPushButton(tr("▶  Animar recorrido solar"))
        self.anim_btn.setCheckable(True)
        self.anim_btn.clicked.connect(self._on_anim)
        g.addWidget(self.anim_btn, 4, 0, 1, 3)
        self.shadow_on = QCheckBox(tr("Sombras proyectadas (sol real)"))
        self.shadow_on.toggled.connect(
            lambda on: None if self._busy else self.sitio.set_shadows(on))
        g.addWidget(self.shadow_on, 5, 0, 1, 3)
        g.addWidget(QLabel(tr("Oscuridad")), 6, 0)
        self.shadow_dark = QSlider(Qt.Horizontal)
        self.shadow_dark.setRange(0, 80)
        self.shadow_dark.valueChanged.connect(self._on_dark)
        g.addWidget(self.shadow_dark, 6, 1, 1, 2)
        self.sun_kv = QLabel("")
        self.sun_kv.setTextFormat(Qt.RichText)
        g.addWidget(self.sun_kv, 7, 0, 1, 3)
        g.addWidget(self._build_sun(), 8, 0, 1, 3)
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

    # ---- Ubicación --------------------------------------------------------------
    def _build_location(self) -> None:
        sec = _section("2 · ◎  Ubicación", "loc", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        self.geo_q = QLineEdit()
        self.geo_q.setPlaceholderText(tr("Buscar dirección o pegar «lat, lon»"))
        self.geo_q.returnPressed.connect(self._on_search)
        b = QPushButton(tr("Buscar"))
        b.clicked.connect(self._on_search)
        lay.addLayout(_row(self.geo_q, b, stretch_last=False))
        self.geo_res = QListWidget()
        self.geo_res.setMaximumHeight(110)
        self.geo_res.hide()
        self.geo_res.itemActivated.connect(self._on_result)
        self.geo_res.itemClicked.connect(self._on_result)
        lay.addWidget(self.geo_res)
        pick = QPushButton(tr("Elegir en el mapa…"))
        pick.clicked.connect(self._on_pick_map)
        lay.addWidget(pick)
        form = wrapping_form(None)
        self.site_name = self._bind(QLineEdit(), ["site_name"])
        form.addRow(tr("Nombre"), self.site_name)
        self.lat = _dspin(-90, 90, 0.0001, 6)
        self.lon = _dspin(-180, 180, 0.0001, 6)
        form.addRow(tr("Latitud"), self.lat)
        form.addRow(tr("Longitud"), self.lon)
        apply = QPushButton(tr("Aplicar coordenadas"))
        apply.clicked.connect(self._on_apply_latlon)
        form.addRow("", apply)
        self.tz = QComboBox()
        self.tz.addItem(tr("Automática (por longitud)"), None)
        for off in range(-12, 15):
            self.tz.addItem(f"UTC{off:+03d}:00", off)
        self.tz.currentIndexChanged.connect(
            lambda _i: None if self._busy else self.sitio.set_tz(
                self.tz.currentData()))
        form.addRow(tr("Zona horaria"), self.tz)
        lay.addLayout(form)
        narrow(self.tz, self.lat, self.lon)
        self.loc_hint = _hint("")
        lay.addWidget(self.loc_hint)
        self._lay.addWidget(sec)

    def _on_search(self) -> None:
        q = self.geo_q.text().strip()
        if not q:
            return
        import re
        m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$", q)
        if m:
            self._set_site(float(m.group(1)), float(m.group(2)), tr("Coordenadas"))
            return
        if self._geocoder is None:
            from georef.geocode import Geocoder
            self._geocoder = Geocoder(self)
            self._geocoder.resultsReady.connect(self._on_places)
            self._geocoder.failed.connect(
                lambda msg: self._flash(tr("Búsqueda fallida: {msg}", msg=msg)))
        self.geo_res.clear()
        self.geo_res.addItem(tr("Buscando…"))
        self.geo_res.show()
        self._geocoder.search(q)

    def _on_places(self, places) -> None:
        self.geo_res.clear()
        if not places:
            self.geo_res.addItem(tr("Sin resultados"))
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
                    self, tr("Mover el origen del proyecto"),
                    tr("El proyecto ya tiene ubicación. El punto nuevo pasa a ser el origen (0,0): lo dibujado conserva sus coordenadas locales y aparece en el sitio nuevo (a {dist} m).\n\n¿Mover el origen?", dist=f"{dist:,.0f}"))
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

    # ---- 1 · Clima (EPW) ---------------------------------------------------------
    def _build_lang(self) -> None:
        """Ajoute un sélecteur de langue intuitif en haut du panneau avec mise à jour instantanée."""
        from PySide6.QtCore import QSettings
        h = QHBoxLayout()
        h.setContentsMargins(4, 2, 4, 4)
        lbl = QLabel(tr("🌐 Langue / Idioma:"))
        lbl.setStyleSheet("font-size: 11px; color: palette(mid); font-weight: bold;")
        combo = QComboBox()
        combo.addItem("Français", "fr")
        combo.addItem("Español", "es")
        combo.addItem("English", "en")
        combo.addItem("Português", "pt")

        actuelle = obtenir_langue_courante()
        idx = combo.findData(actuelle)
        with QSignalBlocker(combo):
            if idx >= 0:
                combo.setCurrentIndex(idx)

        def on_lang_changed(index):
            code = combo.itemData(index)
            if not code:
                return
            # Change la langue active en mémoire et sauvegarde dans QSettings
            definir_langue_active(code)
            try:
                QSettings().setValue("analisis_sitio/language", code)
            except Exception:
                pass
            # Reconstruit toute la barre latérale au prochain cycle d'événement Qt
            QTimer.singleShot(0, self._on_lang_rebuild)

        combo.currentIndexChanged.connect(on_lang_changed)
        h.addWidget(lbl)
        h.addWidget(combo)
        h.addStretch(1)
        self._lay.addLayout(h)

    def _on_lang_rebuild(self) -> None:
        """Reconstruit les widgets du panneau et met à jour la vue 3D après un changement de langue."""
        self._build_all()
        # Met à jour le titre de l'onglet docké s'il est accessible
        try:
            ext_dict = getattr(self.app.window, "_ext_instances", {}).get(self.sitio.store.key, {})
            dock = ext_dict.get("dock")
            if dock is not None and hasattr(dock, "setWindowTitle"):
                dock.setWindowTitle(tr("Análisis de sitio"))
        except Exception:
            pass
        if hasattr(self.app, "viewport") and self.app.viewport:
            self.app.viewport.update()

    def _build_epw(self) -> None:
        sec = _section("1 · ☁  Cargar archivo EPW", "epw", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        b = QPushButton(tr("Abrir archivo EPW…"))
        b.clicked.connect(self._on_open_epw)
        lay.addWidget(b)
        self.epw_use_loc = QCheckBox(tr("Usar la ubicación del EPW para el sitio"))
        self.epw_use_loc.setChecked(True)
        lay.addWidget(self.epw_use_loc)
        self.epw_none = _hint("EPW gratis en climate.onebuilding.org o "
                              "energyplus.net/weather. Se guarda en el .igz.")
        lay.addWidget(self.epw_none)
        self.epw_box = QWidget()
        el = QVBoxLayout(self.epw_box)
        el.setContentsMargins(0, 4, 0, 0)
        self.epw_kv = QLabel("")
        self.epw_kv.setTextFormat(Qt.RichText)
        el.addWidget(self.epw_kv)
        el.addWidget(QLabel(tr("<b>Periodo de análisis</b>")))
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
        per.addWidget(QLabel(tr("Meses")), 0, 0)
        per.addWidget(self.m0, 0, 1)
        per.addWidget(self.m1, 0, 2)
        per.addWidget(QLabel(tr("Horas")), 1, 0)
        per.addWidget(self.h0, 1, 1)
        per.addWidget(self.h1, 1, 2)
        narrow(self.m0, self.m1)
        el.addLayout(per)
        self.epw_stats = QLabel("")
        self.epw_stats.setTextFormat(Qt.RichText)
        self.epw_stats.setWordWrap(True)
        el.addWidget(self.epw_stats)
        b = QPushButton(tr("Quitar EPW"))
        b.clicked.connect(lambda: act.clear_epw(self.sitio))
        el.addWidget(b)
        lay.addWidget(self.epw_box)
        self._lay.addWidget(sec)

    # ---- 3 · Norte ------------------------------------------------------------------
    def _build_north(self) -> None:
        sec = _section("3 · ➚  Norte del proyecto", "north", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
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
        col.addWidget(QLabel(tr("Ángulo (horario desde +Y)")))
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
        b = QPushButton(tr("Desde la arista seleccionada (apunta al norte)"))
        b.setToolTip(tr("Selecciona una línea del modelo dibujada hacia el norte geográfico (de sur a norte) y pulsa."))
        b.clicked.connect(self._on_north_from_edge)
        lay.addWidget(b)
        self.north_hint = _hint("")
        lay.addWidget(self.north_hint)
        self._lay.addWidget(sec)

    def _on_north(self, deg) -> None:
        if self._busy:
            return
        if self.sitio.datum() is None:
            self._flash(tr("Ubica primero el proyecto (sección 2)."))
            self.refresh()
            return
        deg = ((float(deg) + 180.0) % 360.0) - 180.0
        self.sitio.set_north(deg)
        self.refresh()

    def _on_north_from_edge(self) -> None:
        from core.mesh import Edge
        edges = [e for e in self.sitio.scene.selection if isinstance(e, Edge)]
        if len(edges) != 1:
            self._flash(tr("Selecciona una sola arista que apunte al norte."))
            return
        e = edges[0]
        a, b = e.v0.position, e.v1.position
        dx, dy = b.x() - a.x(), b.y() - a.y()
        if math.hypot(dx, dy) < 1e-6:
            self._flash(tr("La arista es vertical: no indica una dirección en planta."))
            return
        if dy < 0 or (abs(dy) < 1e-9 and dx < 0):
            dx, dy = -dx, -dy           # se toma el sentido más cercano a +Y
        self._on_north(math.degrees(math.atan2(dx, dy)))

    # ---- 6 · Datos climáticos ---------------------------------------------------------
    def _build_charts(self) -> None:
        sec = _section("6 · ◔  Datos climáticos", "charts", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        self.charts_none = _hint("Carga un archivo EPW (sección 1) para ver la "
                                 "temperatura y la humedad relativa mensual.")
        lay.addWidget(self.charts_none)
        self.charts_box = QWidget()
        el = QVBoxLayout(self.charts_box)
        el.setContentsMargins(0, 0, 0, 0)
        el.addWidget(QLabel(tr("<b>Temperatura y humedad relativa mensual</b>")))
        self.months_chart = MonthChart()
        el.addWidget(self.months_chart)
        b = QPushButton(tr("Guardar gráficos (PNG)…"))
        b.setToolTip(tr("Guarda la rosa de los vientos y el gráfico mensual."))
        b.clicked.connect(self._on_save_charts)
        el.addWidget(b)
        lay.addWidget(self.charts_box)
        self._lay.addWidget(sec)

    def _on_open_epw(self) -> None:
        path, _f = QFileDialog.getOpenFileName(
            self, tr("Abrir archivo de clima"), "", tr("EnergyPlus Weather (*.epw)"))
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
            QMessageBox.warning(self, tr("Archivo EPW"), str(exc))
            return False
        if self.epw_use_loc.isChecked():
            tz = info.get("tz")
            tz = None if tz is None or math.isnan(tz) else tz
            if ask:
                self._set_site(info["lat"], info["lon"],
                               name=", ".join(x for x in (info["city"],
                                                          info["country"]) if x),
                               tz=tz)
            else:
                self.sitio.set_location(info["lat"], info["lon"], tz=tz)
        self._flash(tr("Clima de {city} cargado.", city=info["city"] or tr("la estación")))
        self.refresh()
        return True

    def _on_dominant(self) -> None:
        try:
            act.set_winds(self.sitio, act.dominant_winds(self.sitio))
        except act.SiteError as exc:
            self._flash(str(exc))

    def _on_save_charts(self) -> None:
        path, _f = QFileDialog.getSaveFileName(
            self, tr("Guardar gráficos"), "clima.png", tr("Imagen PNG (*.png)"))
        if path:
            try:
                out = exportar.export_charts(self.sitio, path)
                self._flash(tr("Guardados: {files}", files=", ".join(p.split("/")[-1] for p in out)))
            except act.SiteError as exc:
                self._flash(str(exc))

    def _refresh_climate(self) -> None:
        epw = self.sitio.epw()
        self.epw_box.setVisible(epw is not None)
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
    def _build_sun(self) -> FoldSection:
        """Subsección de 4: cómo se dibuja el sol en la vista."""
        sec = _section("Diagrama solar", "sun")
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self._bind(QCheckBox(tr("Mostrar diagrama solar")),
                                 ["sun", "diagram"]))
        form = wrapping_form(None)
        self.radius = self._bind(_dspin(5, 5000, 5, 0, " m"), ["radius"])
        form.addRow(tr("Radio del diagrama"), self.radius)
        self.cx, self.cy, self.cz = (_dspin(-1e6, 1e6, 1, 2) for _ in range(3))
        for i, w in enumerate((self.cx, self.cy, self.cz)):
            w.valueChanged.connect(lambda v, i=i: self._on_center(i, v))
        c = QHBoxLayout()
        for w in (self.cx, self.cy, self.cz):
            c.addWidget(w)
        narrow(self.cx, self.cy, self.cz)
        form.addRow(tr("Centro (X, Y, Z)"), c)
        lay.addLayout(form)
        row = QHBoxLayout()
        b = QPushButton(tr("Elegir centro…"))
        b.setToolTip(tr("Clic en el modelo (con inferencias) para centrar el diagrama."))
        b.clicked.connect(self._on_pick_center)
        row.addWidget(b)
        b = QPushButton(tr("Origen"))
        b.clicked.connect(lambda: self._set_center((0.0, 0.0, 0.0)))
        row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(QLabel(tr("Trayectorias (día 21 de cada mes)")))
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
                          ("Trayectoria del día seleccionado", "day"),
                          ("Etiquetas de horas y meses", "labels"),
                          ("Rayo solar al centro", "ray")):
            lay.addWidget(self._bind(QCheckBox(tr(text)), ["sun", key]))
        self.sun_color = self._bind(ColorButton(), ["sun", "color"])
        self.sun_ball = self._bind(ColorButton(), ["sun", "ball"])
        lay.addLayout(_row("Diagrama", self.sun_color, "Sol", self.sun_ball))
        return sec

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

    # ---- 5 · Vientos ---------------------------------------------------------------
    def _build_wind(self) -> None:
        sec = _section("5 · ≋  Vientos", "wind", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        # Rosa de los vientos (EPW) y su color, primero.
        self.rose_none = _hint("Carga un archivo EPW (sección 1) para ver la "
                               "rosa de los vientos.")
        lay.addWidget(self.rose_none)
        self.rose_box = QWidget()
        rl = QVBoxLayout(self.rose_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel(tr("<b>Rosa de los vientos</b> (periodo de la sección 1)")))
        self.rose = RoseChart()
        rl.addWidget(self.rose)
        form = wrapping_form(None)
        self.pal = QComboBox()
        for k, (name, _c) in clima.PALETTES.items():
            self.pal.addItem(tr(name), k)
        self._bind(self.pal, ["wind", "pal"], after=self._refresh_climate)
        form.addRow(tr("Color"), self.pal)
        c0 = self._bind(ColorButton(), ["wind", "c0"], after=self._refresh_climate)
        c1 = self._bind(ColorButton(), ["wind", "c1"], after=self._refresh_climate)
        self.pal_custom = _row(c0, QLabel("→"), c1)
        form.addRow(tr("Calma → fuerte"), self.pal_custom)
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
            lay.addWidget(self._bind(QCheckBox(tr(text)), ["wind", key]))
        form = wrapping_form(None)
        form.addRow(tr("Opacidad rosa 3D"), self._bind(_dspin(0.15, 1, 0.05, 2),
                                                ["wind", "opa"]))
        form.addRow(tr("Tamaño rosa 3D"), self._bind(_dspin(0.2, 1.3, 0.02, 2, " × R"),
                                              ["wind", "scale"]))
        form.addRow(tr("Altura flechas"), self._bind(_dspin(0, 200, 1, 1, " m"),
                                                 ["wind", "height"]))
        form.addRow(tr("Grosor"), self._bind(_dspin(0.3, 3, 0.1, 1),
                                         ["wind", "width"]))
        lay.addLayout(form)
        lay.addWidget(QLabel(tr("Direcciones (de dónde viene el viento)")))
        self.winds = QTableWidget(0, 5)
        self.winds.setHorizontalHeaderLabels(["", tr("Etiqueta"), tr("Dir °"), tr("m/s"), ""])
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
        b = QPushButton(tr("+ Añadir"))
        b.clicked.connect(self._on_wind_add)
        row.addWidget(b)
        b = QPushButton(tr("Quitar"))
        b.clicked.connect(self._on_wind_del)
        row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(_hint("Doble clic en el color para cambiarlo."))
        self.dominant_btn = QPushButton(tr("Aplicar vientos dominantes (EPW)"))
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
        winds.append({"on": True, "label": f"{tr('Viento')} {len(winds) + 1}",
                      "dir": 90.0, "speed": 2.0,
                      "color": WIND_COLORS[len(winds) % len(WIND_COLORS)]})
        act.set_winds(self.sitio, winds)

    def _on_wind_del(self) -> None:
        r = self.winds.currentRow()
        winds = self._winds_from_table()
        if 0 <= r < len(winds):
            del winds[r]
            act.set_winds(self.sitio, winds)

    # ---- Mapa y contexto ------------------------------------------------------------
    def _build_map(self) -> None:
        sec = _section("7 · ▦  Mapa base y contexto", "map")
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        from georef.tiles import PRESETS
        form = wrapping_form(None)
        self.prov = QComboBox()
        for k, src in PRESETS.items():
            self.prov.addItem(src.name, k)
        self.prov.addItem(tr("URL de teselas propia (XYZ)…"), "custom")
        self._bind(self.prov, ["map", "provider"], after=self._sync_custom)
        form.addRow(tr("Cartografía"), self.prov)
        self.custom = self._bind(QLineEdit(), ["map", "custom"])
        self.custom.setPlaceholderText("https://…/{z}/{x}/{y}.png")
        form.addRow(tr("URL"), self.custom)
        self.extent = self._bind(_dspin(100, 5000, 50, 0, " m"), ["map", "extent"])
        form.addRow(tr("Extensión"), self.extent)
        self.zoom = QComboBox()
        self.zoom.addItem(tr("Automático"), 0)
        for z in range(14, 20):
            self.zoom.addItem(str(z), z)
        self._bind(self.zoom, ["map", "zoom"])
        form.addRow(tr("Zoom"), self.zoom)
        narrow(self.prov, self.zoom, self.custom)
        lay.addLayout(form)
        b = QPushButton(tr("Descargar mapa base"))
        b.clicked.connect(self._on_basemap)
        lay.addWidget(b)
        self.map_show = QCheckBox(tr("Mostrar mapa"))
        self.map_show.toggled.connect(self._on_map_show)
        self.map_opa = QSlider(Qt.Horizontal)
        self.map_opa.setRange(10, 100)
        self.map_opa.valueChanged.connect(self._on_map_opa)
        lay.addLayout(_row(self.map_show, QLabel(tr("Opacidad")), self.map_opa,
                           stretch_last=True))
        lay.addWidget(_hint("El mapa es la capa de mapa base de IngeTrazo "
                            "(también en la pestaña Terreno, con el terreno 3D)."))
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)
        lay.addWidget(QLabel(tr("<b>Edificios de contexto (OpenStreetMap)</b>")))
        form = wrapping_form(None)
        form.addRow(tr("Radio"), self._bind(_dspin(50, 2000, 50, 0, " m"),
                                        ["ctx", "radius"]))
        form.addRow(tr("Altura por defecto"), self._bind(_dspin(2, 100, 1, 1, " m"),
                                                     ["ctx", "def_h"]))
        lay.addLayout(form)
        lay.addWidget(self._bind(QCheckBox(tr("Apoyar en el terreno 3D (si existe)")),
                                 ["ctx", "terrain"]))
        self.osm_btn = QPushButton(tr("Cargar edificios 3D"))
        self.osm_btn.clicked.connect(self._on_osm)
        lay.addWidget(self.osm_btn)
        self.ctx_count = _hint("")
        lay.addWidget(self.ctx_count)
        self.ctx_show = QCheckBox(tr("Mostrar edificios"))
        self.ctx_show.toggled.connect(self._on_ctx_show)
        lay.addWidget(self.ctx_show)
        form = wrapping_form(None)
        col = self._bind(ColorButton(), ["ctx", "color"],
                         after=lambda: act.restyle_context(self.sitio))
        form.addRow(tr("Color"), col)
        form.addRow(tr("Opacidad"), self._bind(
            _dspin(0.05, 1, 0.05, 2), ["ctx", "opacity"],
            after=lambda: act.restyle_context(self.sitio)))
        self.hscale = _dspin(0.2, 3, 0.1, 1, " ×")
        self._bind(self.hscale, ["ctx", "hscale"])
        apply_h = QPushButton(tr("Aplicar"))
        apply_h.clicked.connect(self._on_rebuild_ctx)
        form.addRow(tr("Escala de altura"), _row(self.hscale, apply_h))
        lay.addLayout(form)
        self.ctx_edit = QCheckBox(tr("Editar edificios: clic en la vista"))
        self.ctx_edit.toggled.connect(self._on_ctx_edit)
        lay.addWidget(self.ctx_edit)
        self.bld_box = QWidget()
        bl = wrapping_form(self.bld_box)
        self.bld_name = QLabel("")
        bl.addRow(tr("Edificio"), self.bld_name)
        self.bld_h = _dspin(1, 500, 0.5, 1, " m")
        self.bld_min = _dspin(0, 500, 0.5, 1, " m")
        bl.addRow(tr("Altura"), self.bld_h)
        bl.addRow(tr("Base"), self.bld_min)
        row = QHBoxLayout()
        b = QPushButton(tr("Aplicar"))
        b.clicked.connect(self._on_bld_apply)
        row.addWidget(b)
        b = QPushButton(tr("Ocultar"))
        b.clicked.connect(self._on_bld_hide)
        row.addWidget(b)
        bl.addRow("", row)
        self.bld_box.hide()
        lay.addWidget(self.bld_box)
        row = QHBoxLayout()
        b = QPushButton(tr("Mostrar ocultos"))
        b.clicked.connect(self._on_unhide_all)
        row.addWidget(b)
        b = QPushButton(tr("Quitar contexto"))
        b.clicked.connect(lambda: act.set_context(self.sitio, None,
                                                  tr("Contexto quitado.")))
        row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(_hint("El contexto es un grupo bloqueado en la capa "
                            "«Contexto OSM»: proyecta sombra y se exporta como "
                            "el resto. © colaboradores de OpenStreetMap."))
        self._lay.addWidget(sec)

    def _sync_custom(self) -> None:
        self.custom.setVisible(self.prov.currentData() == "custom")

    def _on_basemap(self) -> None:
        v = self.sitio.store.view()["map"]
        try:
            z = act.load_basemap(self.sitio, v["provider"], v["extent"],
                                 v.get("zoom", 0), v.get("custom", ""))
            self._flash(tr("Mapa base: {extent} m, zoom {z}.", extent=f"{v['extent']:.0f}", z=z))
            self.refresh()
        except act.SiteError as exc:
            self._flash(str(exc))

    def _on_map_show(self, on) -> None:
        if self._busy:
            return
        layer = getattr(self.sitio.scene, "tile_layer", None)
        if layer is not None:
            layer.visible = bool(on)
            self.sitio.store.touch()

    def _on_map_opa(self, v) -> None:
        if self._busy:
            return
        layer = getattr(self.sitio.scene, "tile_layer", None)
        if layer is not None:
            layer.opacity = v / 100.0
            self.sitio.store.touch()

    def _on_osm(self) -> None:
        d = self.sitio.datum()
        if d is None:
            self._flash(tr("Ubica el proyecto antes de cargar edificios."))
            return
        if self._osm is None:
            from .osm import OsmWorker
            self._osm = OsmWorker(self)
            self._osm.finished.connect(self._on_osm_done)
            self._osm.failed.connect(self._on_osm_failed)
        r = self.sitio.store.view()["ctx"]["radius"]
        if self._osm.start(d.lat, d.lon, r):
            self.osm_btn.setEnabled(False)
            self.osm_btn.setText(tr("Descargando de OpenStreetMap…"))

    def _on_osm_done(self, payload) -> None:
        self.osm_btn.setEnabled(True)
        self.osm_btn.setText(tr("Cargar edificios 3D"))
        try:
            act.context_from_osm(self.sitio, payload)
        except act.SiteError as exc:
            self._flash(str(exc))
        self.refresh()

    def _on_osm_failed(self, msg) -> None:
        self.osm_btn.setEnabled(True)
        self.osm_btn.setText(tr("Cargar edificios 3D"))
        QMessageBox.warning(self, "OpenStreetMap", msg)

    def _on_ctx_show(self, on) -> None:
        if self._busy:
            return
        g = act.context_group(self.sitio)
        if g is not None:
            g.hidden = not on
            self.sitio.store.touch()
            notify = getattr(self.app.viewport, "notify_scene_changed", None)
            if callable(notify):
                notify()

    def _on_rebuild_ctx(self) -> None:
        bl = self.sitio.store.context().get("buildings")
        if bl:
            import copy
            act.set_context(self.sitio, copy.deepcopy(bl), "Contexto actualizado.")

    def _on_ctx_edit(self, on) -> None:
        self.sitio.edit_context = bool(on)
        if not on:
            self.sitio.selected_building = None
            self.app.release_pick()
        self.refresh()
        self.app.viewport.update()

    def building_selected(self, bid) -> None:
        self.sitio.selected_building = bid
        self.refresh()

    def _on_bld_apply(self) -> None:
        bid = self.sitio.selected_building
        if bid:
            act.update_building(self.sitio, bid, h=self.bld_h.value(),
                                min=self.bld_min.value(), src="user")

    def _on_bld_hide(self) -> None:
        bid = self.sitio.selected_building
        if bid:
            act.update_building(self.sitio, bid, hidden=True)
            self.sitio.selected_building = None
            self.app.release_pick()

    def _on_unhide_all(self) -> None:
        import copy
        bl = copy.deepcopy(self.sitio.store.context().get("buildings") or [])
        if any(b.get("hidden") for b in bl):
            for b in bl:
                b["hidden"] = False
            act.set_context(self.sitio, bl)

    # ---- Masas ----------------------------------------------------------------------
    def _build_masses(self) -> None:
        sec = _section("8 · ⬛  Masas y lotes", "mass", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(_hint("Dibuja la planta con Rectángulo, Polígono o Línea, "
                            "selecciona la(s) cara(s) y conviértelas:"))
        row = QGridLayout()
        b = QPushButton(tr("⬛ Masa desde selección"))
        b.clicked.connect(lambda: self._make_masses("mass"))
        row.addWidget(b, 0, 0)
        b = QPushButton(tr("▭ Lote desde selección"))
        b.clicked.connect(lambda: self._make_masses("lot"))
        row.addWidget(b, 0, 1)
        lay.addLayout(row)
        form = wrapping_form(None)
        form.addRow(tr("Altura inicial"), self._bind(_dspin(0, 500, 0.5, 2, " m"),
                                                 ["model", "def_h"]))
        form.addRow(tr("Altura de piso"), self._bind(_dspin(2, 10, 0.1, 2, " m"),
                                                 ["model", "floor_h"]))
        lay.addLayout(form)
        self.mass_list = QListWidget()
        self.mass_list.setMaximumHeight(150)
        self.mass_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.mass_list.currentRowChanged.connect(self._on_mass_row)
        lay.addWidget(self.mass_list)
        self.mass_box = QWidget()
        mf = wrapping_form(self.mass_box)
        self.mp_name = QLineEdit()
        self.mp_name.editingFinished.connect(self._on_mass_name)
        mf.addRow(tr("Nombre"), self.mp_name)
        self.mp_kind = QComboBox()
        self.mp_kind.addItem(tr("Masa (volumen)"), "mass")
        self.mp_kind.addItem(tr("Lote / área"), "lot")
        self.mp_kind.currentIndexChanged.connect(
            lambda _i: self._mass_change(kind=self.mp_kind.currentData()))
        mf.addRow(tr("Tipo"), self.mp_kind)
        self.mp_h = _dspin(0, 1000, 0.5, 2, " m")
        self.mp_h.valueChanged.connect(lambda v: self._mass_change(h=v))
        mf.addRow(tr("Altura"), self.mp_h)
        self.mp_floors = _ispin(0, 300)
        self.mp_floors.valueChanged.connect(self._on_floors)
        mf.addRow(tr("Pisos"), self.mp_floors)
        self.mp_base = _dspin(-1000, 1000, 0.5, 2, " m")
        self.mp_base.valueChanged.connect(lambda v: self._mass_change(base=v))
        mf.addRow(tr("Cota base"), self.mp_base)
        self.mp_color = ColorButton()
        self.mp_color.colorChanged.connect(lambda c: self._mass_change(color=c))
        self.mp_opa = _dspin(0.1, 1, 0.05, 2)
        self.mp_opa.valueChanged.connect(lambda v: self._mass_change(opacity=v))
        mf.addRow(tr("Color"), _row(self.mp_color, QLabel(tr("Opacidad")), self.mp_opa))
        self.mp_kv = QLabel("")
        self.mp_kv.setTextFormat(Qt.RichText)
        mf.addRow(self.mp_kv)
        g = QGridLayout()
        for i, (txt, fn) in enumerate((
                ("⟲ 15°", lambda: self._mass_op("rotL")),
                ("⟳ 15°", lambda: self._mass_op("rotR")),
                ("Duplicar", lambda: self._mass_op("dup")),
                ("Seleccionar", lambda: self._mass_op("sel")),
                ("Eliminar", lambda: self._mass_op("del")))):
            b = QPushButton(tr(txt))
            b.clicked.connect(fn)
            g.addWidget(b, i // 3, i % 3)
        mf.addRow(g)
        self.mass_box.hide()
        narrow(self.mp_kind, self.mp_name)
        lay.addWidget(self.mass_box)
        self.mass_sum = QLabel("")
        self.mass_sum.setTextFormat(Qt.RichText)
        lay.addWidget(self.mass_sum)
        lay.addWidget(_hint("Las masas son grupos del modelo: se mueven, giran y "
                            "empujan con las herramientas de IngeTrazo, y el panel "
                            "lee su forma actual. Proyectan sombra."))
        self._lay.addWidget(sec)

    def _make_masses(self, kind) -> None:
        try:
            made = act.create_masses_from_faces(self.sitio, kind)
        except act.SiteError as exc:
            self._flash(str(exc))
            return
        self.refresh()
        if made:
            self.select_mass(made[-1])

    def _current_mass(self):
        r = self.mass_list.currentRow()
        return self._mass_rows[r] if 0 <= r < len(self._mass_rows) else None

    def select_mass(self, group) -> None:
        if group in self._mass_rows:
            self.mass_list.setCurrentRow(self._mass_rows.index(group))

    def _on_mass_row(self, _r) -> None:
        if not self._busy:
            self._fill_mass()

    def _fill_mass(self) -> None:
        g = self._current_mass()
        self.mass_box.setVisible(g is not None)
        if g is None:
            return
        p = act.sync_from_mesh(self.sitio, g)
        st = geo.mass_stats(p)
        self._busy = True
        try:
            self.mp_name.setText(g.name)
            self.mp_kind.setCurrentIndex(0 if p["kind"] == "mass" else 1)
            self.mp_h.setValue(p["h"])
            self.mp_h.setEnabled(p["kind"] == "mass")
            self.mp_floors.setEnabled(p["kind"] == "mass")
            self.mp_floors.setValue(st["floors"])
            self.mp_base.setValue(p["base"])
            self.mp_color.setColor(p["color"])
            self.mp_opa.setValue(p["opacity"])
        finally:
            self._busy = False
        rows = [("Área en planta", f"{st['area']:,.1f} m²"),
                ("Perímetro", f"{st['perimeter']:,.1f} m")]
        if p["kind"] == "mass":
            rows += [("Volumen", f"{st['volume']:,.0f} m³"),
                     ("Área construida", f"{st['gfa']:,.0f} m² "
                                         f"({st['floors']} {tr('pisos')})"),
                     ("Cota superior", f"{st['top']:.2f} m")]
        self.mp_kv.setText(_kv(rows))

    def _mass_change(self, **changes) -> None:
        if self._busy:
            return
        g = self._current_mass()
        if g is None:
            return
        new = act.update_mass(self.sitio, g, changes)
        if new is not None:
            self.refresh()
            self.select_mass(new)

    def _on_floors(self, n) -> None:
        if self._busy:
            return
        fh = float(self.sitio.store.view()["model"]["floor_h"])
        self._mass_change(h=n * fh, floor_h=fh)

    def _on_mass_name(self) -> None:
        g = self._current_mass()
        name = self.mp_name.text().strip()
        if g is not None and name and name != g.name:
            g.name = name
            self.sitio.store.touch()
            notify = getattr(self.app.viewport, "notify_scene_changed", None)
            if callable(notify):
                notify()
            self.refresh()

    def _mass_op(self, op) -> None:
        g = self._current_mass()
        if g is None:
            return
        if op in ("rotL", "rotR"):
            new = act.rotate_mass(self.sitio, g, 15.0 if op == "rotL" else -15.0)
        elif op == "dup":
            new = act.duplicate_mass(self.sitio, g)
        elif op == "del":
            act.delete_mass(self.sitio, g)
            new = None
        else:
            sc = self.sitio.scene
            sc.selection.clear()
            sc.selection.add(g)
            self.app.viewport.update()
            new = g
        self.refresh()
        if new is not None:
            self.select_mass(new)

    def _refresh_masses(self) -> None:
        cur = self._current_mass()
        rows = act.masses(self.sitio)
        self._mass_rows = rows
        with QSignalBlocker(self.mass_list):
            self.mass_list.clear()
            for g in rows:
                p = act.sync_from_mesh(self.sitio, g)
                st = geo.mass_stats(p)
                if p["kind"] == "lot":
                    txt = f"▭ {g.name} · {st['area']:,.0f} m²"
                else:
                    txt = (f"⬛ {g.name} · {p['h']:.1f} m · {st['floors']} p · "
                           f"{st['area']:,.0f} m²")
                it = QListWidgetItem(txt)
                it.setForeground(QColor(p["color"]).darker(105))
                self.mass_list.addItem(it)
            if cur in rows:
                self.mass_list.setCurrentRow(rows.index(cur))
        self._fill_mass()
        t = act.summary(self.sitio)
        rows = []
        if t["masses"]:
            rows += [("Masas", t["masses"]),
                     ("Huella total", f"{t['footprint']:,.0f} m²"),
                     ("Área construida", f"{t['gfa']:,.0f} m²"),
                     ("Volumen", f"{t['volume']:,.0f} m³")]
        if t["lots"]:
            rows.append(("Área de lotes", f"{t['lot_area']:,.0f} m²"))
        if "ios" in t:
            rows += [("Índice de ocupación", f"{t['ios']:.2f}"),
                     ("Índice de construcción", f"{t['ica']:.2f}")]
        self.mass_sum.setText(_kv(rows) if rows else "")

    # ---- Anotaciones --------------------------------------------------------------
    def _build_pins(self) -> None:
        sec = _section("9 · ⌖  Anotaciones", "pins")
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        self.icon_q = QLineEdit()
        self.icon_q.setPlaceholderText(tr("Buscar icono (árbol, bus, hospital…)"))
        self.icon_q.textChanged.connect(self._fill_icons)
        lay.addWidget(self.icon_q)
        self.icons = QListWidget()
        self.icons.setViewMode(QListWidget.IconMode)
        self.icons.setFlow(QListWidget.LeftToRight)
        self.icons.setWrapping(True)
        self.icons.setResizeMode(QListWidget.Adjust)
        self.icons.setMaximumHeight(96)
        self.icons.setSpacing(2)
        lay.addWidget(self.icons)
        self._fill_icons()
        row = QGridLayout()
        b = QPushButton(tr("⌖ Punto (clic en la vista)"))
        b.clicked.connect(lambda: self._place_pin(False))
        row.addWidget(b, 0, 0)
        b = QPushButton(tr("★ Con el icono elegido"))
        b.clicked.connect(lambda: self._place_pin(True))
        row.addWidget(b, 0, 1)
        lay.addLayout(row)
        self.pin_list = QListWidget()
        self.pin_list.setMaximumHeight(120)
        self.pin_list.currentRowChanged.connect(self._on_pin_row)
        lay.addWidget(self.pin_list)
        self.pin_box = QWidget()
        pf = wrapping_form(self.pin_box)
        self.pp_label = QLineEdit()
        self.pp_label.editingFinished.connect(self._on_pin_label)
        pf.addRow(tr("Texto"), self.pp_label)
        row = QHBoxLayout()
        b = QPushButton(tr("Usar icono elegido"))
        b.clicked.connect(self._on_pin_icon)
        row.addWidget(b)
        b = QPushButton(tr("Quitar icono"))
        b.clicked.connect(lambda: self._pin_set(icon=None))
        row.addWidget(b)
        b = QPushButton(tr("Eliminar"))
        b.clicked.connect(self._on_pin_del)
        row.addWidget(b)
        pf.addRow(row)
        self.pin_box.hide()
        lay.addWidget(self.pin_box)
        lay.addWidget(QLabel(tr("<b>Estilo de las anotaciones</b>")))
        form = wrapping_form(None)
        a = ["annot"]
        form.addRow(tr("Icono · Texto · Línea"), _row(
            self._bind(ColorButton(), a + ["color"]),
            self._bind(ColorButton(), a + ["text"]),
            self._bind(ColorButton(), a + ["line"])))
        form.addRow(self._bind(QCheckBox(tr("Fondo circular / etiqueta")), a + ["bg"]),
                    self._bind(ColorButton(), a + ["bg_color"]))
        form.addRow(tr("Tamaño"), self._bind(_dspin(1, 80, 0.5, 1, " m"), a + ["size"]))
        form.addRow(tr("Altura"), self._bind(_dspin(0, 200, 1, 1, " m"), a + ["h"]))
        lay.addLayout(form)
        lay.addWidget(self._bind(QCheckBox(tr("Línea guía al terreno")), a + ["line_on"]))
        lay.addWidget(self._bind(QCheckBox(tr("Mostrar texto")), a + ["label_on"]))
        b = QPushButton(tr("Aplicar estilo a todas"))
        b.clicked.connect(lambda: act.style_all_pins(self.sitio))
        lay.addWidget(b)
        lay.addWidget(_hint("Con la herramienta Seleccionar: clic en una anotación "
                            "para elegirla; Supr la borra."))
        self._lay.addWidget(sec)

    def _fill_icons(self) -> None:
        q = self.icon_q.text().strip().lower() if hasattr(self, "icon_q") else ""
        cur = self.icons.currentItem().data(Qt.UserRole) if (
            self.icons.currentItem()) else None
        self.icons.clear()
        for g, name in ICONS:
            if q and q not in name:
                continue
            it = QListWidgetItem(g)
            it.setData(Qt.UserRole, g)
            it.setToolTip(name)
            it.setTextAlignment(Qt.AlignCenter)
            f = it.font()
            f.setPointSize(15)
            it.setFont(f)
            self.icons.addItem(it)
            if g == cur:
                self.icons.setCurrentItem(it)

    def _chosen_icon(self):
        it = self.icons.currentItem()
        return it.data(Qt.UserRole) if it is not None else None

    def _place_pin(self, with_icon: bool) -> None:
        icon = self._chosen_icon() if with_icon else None
        if with_icon and not icon:
            self._flash(tr("Elige primero un icono de la lista."))
            return
        name = next((n.split()[0].capitalize() for g, n in ICONS if g == icon),
                    None)

        def done(p, _ctx):
            act.add_pin(self.sitio, p, label=name, icon=icon)

        tool = herramientas.make_pick_tool("Clic donde va la anotación", done,
                                           lambda: herramientas.end(self.app),
                                           repeat=True)
        herramientas.begin(self.app, tool,
                           "Clic donde va la anotación (varias seguidas)")

    def _current_pin(self):
        pid = self.sitio.selected_pin
        for p in self.sitio.store.content()["pins"]:
            if p.get("id") == pid:
                return p
        return None

    def _on_pin_row(self, r) -> None:
        if self._busy:
            return
        pins = self.sitio.store.content()["pins"]
        self.sitio.selected_pin = pins[r]["id"] if 0 <= r < len(pins) else None
        self._fill_pin()
        self.app.viewport.update()

    def _fill_pin(self) -> None:
        p = self._current_pin()
        self.pin_box.setVisible(p is not None)
        if p is not None:
            with QSignalBlocker(self.pp_label):
                self.pp_label.setText(p.get("label", ""))

    def _pin_set(self, **ch) -> None:
        pid = self.sitio.selected_pin
        if pid:
            act.update_pin(self.sitio, pid, **ch)

    def _on_pin_label(self) -> None:
        p = self._current_pin()
        if p is not None and self.pp_label.text() != p.get("label"):
            self._pin_set(label=self.pp_label.text())

    def _on_pin_icon(self) -> None:
        icon = self._chosen_icon()
        if icon:
            self._pin_set(icon=icon)

    def _on_pin_del(self) -> None:
        pid = self.sitio.selected_pin
        if pid:
            act.delete_pin(self.sitio, pid)
            self.app.release_pick()

    def pin_selected(self, pid) -> None:
        self.sitio.selected_pin = pid
        self.refresh()

    # ---- Vista ------------------------------------------------------------------
    def _build_view(self) -> None:
        sec = _section("10 · ◐  Visualización", "view", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        self.on = self._bind(QCheckBox(tr("Mostrar el análisis en la vista")), ["on"])
        lay.addWidget(self.on)
        lay.addWidget(self._bind(QCheckBox(tr("Brújula y grados")), ["view", "compass"]))
        form = wrapping_form(None)
        form.addRow(tr("Anillos"), _row(self._bind(_ispin(0, 20), ["rings", "n"]),
                                    QLabel(tr("cada")),
                                    self._bind(_dspin(1, 1000, 5, 0, " m"),
                                               ["rings", "step"]),
                                    self._bind(ColorButton(), ["rings", "color"])))
        form.addRow(tr("Escala de etiquetas"), self._bind(_dspin(0.4, 2.5, 0.1, 1),
                                                      ["view", "label_scale"]))
        lay.addLayout(form)
        lay.addWidget(QLabel(tr("<b>Convenciones</b>")))
        self.legend = LegendWidget()
        lay.addWidget(self.legend)
        lay.addWidget(QLabel(tr("<b>Vistas</b>")))
        g = QGridLayout()
        views = (("Planta", "top"), ("Iso SE", "iso_se"), ("Iso SO", "iso_sw"),
                 ("Iso NE", "iso_ne"), ("Iso NO", "iso_nw"),
                 ("Aérea", "persp"), ("Desde el sol", "sun"),
                 ("Encuadrar", "fit"), ("Persp./Paral.", "proj"))
        for i, (t, k) in enumerate(views):
            b = QPushButton(tr(t))
            b.clicked.connect(lambda _c=False, k=k: self.set_view(k))
            g.addWidget(b, i // 3, i % 3)
        lay.addLayout(g)
        self._lay.addWidget(sec)

    def set_view(self, key: str) -> None:
        vp = self.app.viewport
        cam = vp.camera
        v = self.sitio.store.view()
        cx, cy, cz = v["center"]
        R = float(v["radius"]) * 1.3
        if key == "proj":
            cam.perspective = not cam.perspective
            vp.update()
            return
        if key == "top":
            cam.set_view("top")
        elif key.startswith("iso_"):
            yaw = {"iso_se": -45.0, "iso_sw": -135.0, "iso_ne": 45.0,
                   "iso_nw": 135.0}[key]
            cam.yaw, cam.pitch = math.radians(yaw), math.radians(35.0)
        elif key == "persp":
            cam.perspective = True
            cam.yaw, cam.pitch = math.radians(-60.0), math.radians(22.0)
        elif key == "sun":
            alt, az = self.sitio.sun_now()
            if alt <= 0:
                self._flash(tr("El sol está bajo el horizonte a esta hora."))
                return
            dx, dy, _dz = self.sitio.frame().direction(alt, az)
            cam.yaw = math.atan2(dy, dx)
            cam.pitch = math.radians(max(alt, 3.0))
        fit = getattr(cam, "fit_to", None)
        if callable(fit):
            fit(QVector3D(cx - R, cy - R, cz), QVector3D(cx + R, cy + R, cz + R * 0.6))
        else:
            cam.target = QVector3D(cx, cy, cz)
            cam.distance = R * 2.2
        vp.update()

    # ---- 11 · Exportar ------------------------------------------------------------
    def _build_export(self) -> None:
        sec = _section("12 · ⇩  Exportar imagen", "export")
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        form = wrapping_form(None)
        self.res = QComboBox()
        for t, w in (("FHD (1920)", 1920), ("2K (2560)", 2560),
                     ("4K (3840)", 3840), ("5K (5120)", 5120)):
            self.res.addItem(t, w)
        form.addRow(tr("Resolución"), self.res)
        narrow(self.res)
        lay.addLayout(form)
        b = QPushButton(tr("Exportar imagen (PNG)…"))
        b.clicked.connect(self._on_png)
        lay.addWidget(b)
        lay.addWidget(_hint("La imagen es la vista actual con el análisis encima, "
                            "incluido el panel de información si está visible."))
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)
        form = wrapping_form(None)
        form.addRow(tr("Título del panel"), self._bind(QLineEdit(),
                                                   ["view", "notes_title"]))
        corner = QComboBox()
        for k, t in (("tr", "Arriba derecha"), ("tl", "Arriba izquierda"),
                     ("br", "Abajo derecha"), ("bl", "Abajo izquierda")):
            corner.addItem(tr(t), k)
        form.addRow(tr("Posición"), self._bind(corner, ["view", "notes_corner"]))
        narrow(corner)
        lay.addLayout(form)
        # Oculto al iniciar IngeTrazo: es un ajuste de la sesión, no del
        # documento (un .igz no lo vuelve a encender al abrirse).
        self.notes_on = QCheckBox(tr("Mostrar panel flotante de información y convenciones"))
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
        path = self._ask_save(tr("Exportar imagen"), self._stem() + "_analisis.png",
                              tr("Imagen PNG (*.png)"))
        if path:
            try:
                exportar.export_png(self.sitio, self.overlay, path,
                                    self.res.currentData())
                self._flash(tr("Imagen guardada: {path}", path=path))
            except act.SiteError as exc:
                self._flash(str(exc))

    # ---- 11 · Radiación solar y sombras ------------------------------------------------
    def _build_solar(self) -> None:
        from . import radiacion as rad
        sec = _section("11 · ☀  Radiación solar y sombras", "solar", True)
        lay = QVBoxLayout(sec.body)
        lay.setContentsMargins(6, 6, 6, 6)
        r, d = ["rad"], ["sd"]
        # Común: qué superficies y con qué detalle.
        form = wrapping_form(None)
        tgt = QComboBox()
        for k, t in (("all", "Todo el modelo (sin contexto OSM)"),
                     ("masses", "Masas y lotes"),
                     ("sel", "Selección (caras o grupos)"),
                     ("none", "Sólo el suelo")):
            tgt.addItem(tr(t), k)
        form.addRow(tr("Superficies"), self._bind(tgt, r + ["target"]))
        resn = QComboBox()
        for k, t in (("faces:1.0", "Por superficie (un color por cara)"),
                     ("cells:2.0", "Malla de 2 m"), ("cells:1.0", "Malla de 1 m"),
                     ("cells:0.5", "Malla de 0.5 m")):
            resn.addItem(tr(t), k)
        resn.currentIndexChanged.connect(self._on_rad_res)
        self.rad_res = resn
        form.addRow(tr("Resolución"), resn)
        self.ground_r = _dspin(0, 2000, 10, 0, " m")
        self.ground_r.setSpecialValueText(tr("automático"))
        self._bind(self.ground_r, r + ["ground_r"])
        form.addRow(self._bind(QCheckBox(tr("Suelo alrededor")), r + ["ground"]),
                    _row(QLabel(tr("margen")), self.ground_r))
        narrow(tgt, resn)
        lay.addLayout(form)
        # Radiación.
        lay.addWidget(QLabel(tr("<b>Radiación solar incidente</b>")))
        form = wrapping_form(None)
        per = QComboBox()
        for k, t in (("day", "Día de la sección 4"), ("month", "Un mes"),
                     ("period", "Periodo de análisis (sección 1)"),
                     ("year", "Año completo")):
            per.addItem(tr(t), k)
        self.rad_per = self._bind(per, r + ["period"], after=self._sync_rad)
        form.addRow(tr("Periodo"), per)
        mon = QComboBox()
        for i, m in enumerate(clima.MONTHS_LONG):
            mon.addItem(m.capitalize(), i + 1)
        self.rad_month = self._bind(mon, r + ["month"])
        self.rad_month_lbl = QLabel(tr("Mes"))
        form.addRow(self.rad_month_lbl, mon)
        form.addRow(tr("Albedo del suelo"), self._bind(_dspin(0, 0.9, 0.05, 2),
                                                   r + ["albedo"]))
        narrow(per, mon)
        lay.addLayout(form)
        self.rad_src = _hint("")
        lay.addWidget(self.rad_src)
        b = QPushButton(tr("☀  Calcular radiación"))
        b.clicked.connect(self._on_rad)
        lay.addWidget(b)
        # Sombras.
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)
        lay.addWidget(QLabel(tr("<b>Estudio de sombras</b>")))
        self.sd_date = _hint("")
        lay.addWidget(self.sd_date)
        form = wrapping_form(None)
        form.addRow(tr("Desde"), self._bind(_ispin(0, 23, ":00 h"), d + ["h0"]))
        form.addRow(tr("Hasta"), self._bind(_ispin(1, 24, ":00 h"), d + ["h1"]))
        step = QComboBox()
        for m in (15, 20, 30, 60):
            step.addItem(tr("cada {m} min", m=m), m)
        form.addRow(tr("Intervalo"), self._bind(step, d + ["step"]))
        narrow(step)
        lay.addLayout(form)
        b = QPushButton(tr("◑  Calcular estudio de sombras"))
        b.clicked.connect(self._on_sd)
        lay.addWidget(b)
        # Resultado en la vista.
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        lay.addWidget(sep)
        self.map_box = QWidget()
        ml = QVBoxLayout(self.map_box)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(QLabel(tr("<b>Resultado en la vista</b>")))
        self.map_pick = QComboBox()
        self.map_pick.currentIndexChanged.connect(self._on_map_pick)
        ml.addWidget(self.map_pick)
        self.legend_on = self._bind(
            QCheckBox(tr("Mostrar panel flotante de la escala (radiación / sombras)")),
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
            pal.addItem(tr(t), k)
        self._bind(pal, r + ["palette"], after=self._rad_recolor)
        rl.addRow(tr("Colores"), pal)
        rl.addRow(self._bind(QCheckBox(tr("Escala automática (mín–máx)")),
                             r + ["auto"], after=self._rad_recolor))
        self.rad_lo = self._bind(_dspin(0, 1e6, 10, 1, " kWh/m²"), r + ["vmin"],
                                 after=self._rad_recolor)
        self.rad_hi = self._bind(_dspin(0, 1e6, 10, 1, " kWh/m²"), r + ["vmax"],
                                 after=self._rad_recolor)
        rl.addRow(tr("Mínimo"), self.rad_lo)
        rl.addRow(tr("Máximo"), self.rad_hi)
        narrow(pal)
        ml.addWidget(self.rad_opts)
        self.sd_opts = QWidget()
        sl = wrapping_form(self.sd_opts)
        spal = QComboBox()
        for k, (t, _st) in rad.PALETTES.items():
            spal.addItem(tr(t), k)
        self._bind(spal, d + ["palette"], after=self._sd_recolor)
        sl.addRow(tr("Colores"), spal)
        show = QComboBox()
        show.addItem(tr("Horas de sombra"), "shade")
        show.addItem(tr("Horas de sol (asoleamiento)"), "sun")
        self._bind(show, d + ["show"], after=self._sd_recolor)
        sl.addRow(tr("Mostrar"), show)
        narrow(spal, show)
        ml.addWidget(self.sd_opts)
        b = QPushButton(tr("Quitar el resultado mostrado"))
        b.clicked.connect(self._on_map_clear)
        ml.addWidget(b)
        lay.addWidget(self.map_box)
        lay.addWidget(_hint(
            "Radiación: directa (con las sombras de todo el modelo y el contexto)"
            " + difusa con la vista real al cielo + reflejada por el suelo. "
            "Sombras: horas en sombra de cada punto, probando el sol en cada "
            "intervalo. Apaga las sombras proyectadas (sección 4) para leer "
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
        dlg = QProgressDialog(tr(title), tr("Cancelar"), 0, 1000, self.app.window)
        dlg.setWindowTitle(tr("Análisis de sitio"))
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
            self._flash(tr(cancelled))
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

        run_step(self.app.viewport, mutate, message=tr("Resultado quitado."))
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
            tr("Clima: EPW de {city} (DNI y DHI horarias).", city=epw["info"].get("city") or tr("la estación")) if epw else
            tr("Sin EPW: se usa un cielo despejado teórico (valores máximos posibles). Carga un EPW en la sección 1 para el clima real."))
        m, d = solar.month_day(st.when()[0])
        self.sd_date.setText(tr("Día: {d} de {month} (la fecha de la sección 4).", d=d, month=clima.MONTHS_LONG[m - 1]))
        groups = {k: rad.find_group(st.scene, key, k) for k in rad.KINDS}
        shown = rad.visible_map(st.scene, key)
        self.map_box.setVisible(any(groups.values()))
        with QSignalBlocker(self.map_pick):
            self.map_pick.clear()
            self.map_pick.addItem(tr("Ninguno"), None)
            for k, title in (("radiation", "Mapa de radiación solar"),
                             ("shadows", "Estudio de sombras (horas de sombra)")):
                if groups[k] is not None:
                    self.map_pick.addItem(tr(title), k)
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
                         ("Puntos de cálculo", f"{sm['cells']:,} · {tr('malla')} "
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
            self.anim_btn.setText(tr("■  Detener") if st.animating_sun
                                  else tr("▶  Animar recorrido solar"))
            self.lat.setValue(s["lat"])
            self.lon.setValue(s["lon"])
            self.north.setValue(s["north"])
            self.north_dial.setValue(int(round(s["north"] + 180.0)) % 360)
            for w in (self.north, self.north_dial):
                w.setEnabled(s["located"])
            self.north_hint.setText(
                tr("Dónde queda el norte geográfico en el modelo. Gira el sol, el diagrama, el mapa base y el terreno; la geometría no se mueve.") if s["located"] else
                tr("Ubica primero el proyecto (sección 2) para fijar su norte."))
            self.legend.set_rows(legend_rows(st))
            self.loc_hint.setText(
                tr("Ubicación del proyecto (datum de IngeTrazo): la usan el sol, las sombras, el mapa y el terreno.") if s["located"] else
                tr("⚠ El proyecto no tiene ubicación: se usa un sitio por defecto. Busca la dirección, elige en el mapa o abre un EPW."))
            months = set(int(x) for x in v["sun"]["months"])
            for i, cb in enumerate(self.month_checks):
                cb.setChecked(i + 1 in months)
            cx, cy, cz = v["center"]
            self.cx.setValue(cx)
            self.cy.setValue(cy)
            self.cz.setValue(cz)
            self._fill_winds()
            self._sync_custom()
            layer = getattr(st.scene, "tile_layer", None)
            self.map_show.setEnabled(layer is not None)
            self.map_opa.setEnabled(layer is not None)
            if layer is not None:
                self.map_show.setChecked(bool(layer.visible))
                self.map_opa.setValue(int(round(getattr(layer, "opacity", 1) * 100)))
            ctx = st.store.context()
            bl = ctx.get("buildings") or []
            g = act.context_group(st)
            if bl:
                tagged = sum(1 for b in bl if b.get("src") == "tag")
                hidden = sum(1 for b in bl if b.get("hidden"))
                if hidden:
                    self.ctx_count.setText(tr("{n} edificios ({tagged} con altura en OSM, {hidden} ocultos).", n=len(bl), tagged=tagged, hidden=hidden))
                else:
                    self.ctx_count.setText(tr("{n} edificios ({tagged} con altura en OSM).", n=len(bl), tagged=tagged))
            else:
                self.ctx_count.setText(tr("Sin edificios de contexto."))
            self.ctx_show.setEnabled(g is not None)
            self.ctx_show.setChecked(g is not None and not g.hidden)
            self.ctx_edit.setChecked(st.edit_context)
            b = next((b for b in bl if b.get("id") == st.selected_building), None)
            self.bld_box.setVisible(st.edit_context and b is not None)
            if b is not None:
                self.bld_name.setText(b.get("name") or b["id"])
                self.bld_h.setValue(float(b.get("h", 9)))
                self.bld_min.setValue(float(b.get("min", 0)))
            pins = st.store.content()["pins"]
            self.pin_list.clear()
            sel_row = -1
            for i, p in enumerate(pins):
                self.pin_list.addItem(f"{p.get('icon') or '⌖'}  {p.get('label', '')}")
                if p.get("id") == st.selected_pin:
                    sel_row = i
            self.pin_list.setCurrentRow(sel_row)
        finally:
            self._busy = False
        self._fill_pin()
        self._refresh_climate()
        self._refresh_masses()
        self._refresh_rad()
