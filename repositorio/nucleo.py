# SPDX-License-Identifier: GPL-3.0-or-later
"""El controlador: sitio, hora, clima y animación, compartidos por el
panel y el dibujo sobre la vista.

Una sola verdad para cada cosa, la de IngeTrazo cuando existe:

- **ubicación** → ``scene.georef`` (``SceneDatum``: lat, lon, norte). Es la
  que usan el mapa base, el terreno y las sombras; sin ella se usa el sitio
  por defecto de ``core.sun`` y el panel invita a ubicar el proyecto;
- **fecha y hora** → ``scene.shadows`` (mes, día, hora, minuto, huso). Al
  mover la hora del análisis se mueven las sombras reales del modelo, y al
  revés: el panel de Sombras y este panel son dos mandos del mismo sol;
- **clima** → el EPW guardado en el documento (``datos.Store``).
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QObject, QTimer, Signal

from . import clima, solar
from .datos import Store


class Sitio(QObject):
    """Estado vivo de la extensión para el proyecto que está al frente."""

    changed = Signal()          # algo que el panel muestra cambió

    def __init__(self, app) -> None:
        super().__init__(app.window)
        self.app = app
        self.store = Store(app)
        self._epw_cache = (None, None)          # (id del valor, dict)
        self._stats_cache = (None, None)
        self.t0 = time.monotonic()
        # Flujo del viento: repinta suave mientras haya flechas animadas.
        self._wind_timer = QTimer(self)
        self._wind_timer.setInterval(66)
        self._wind_timer.timeout.connect(self._wind_tick)
        # Recorrido solar del día.
        self._sun_timer = QTimer(self)
        self._sun_timer.setInterval(120)
        self._sun_timer.timeout.connect(self._sun_tick)
        #: Panel flotante de información y convenciones: oculto al iniciar.
        self.show_notes = False

    # ---- Sitio -----------------------------------------------------------------
    @property
    def scene(self):
        return self.app.scene

    def datum(self):
        return getattr(self.scene, "georef", None)

    def site(self) -> dict:
        from core.sun import DEFAULT_LAT, DEFAULT_LON, default_utc_offset
        d = self.datum()
        lat, lon = (d.lat, d.lon) if d is not None else (DEFAULT_LAT,
                                                          DEFAULT_LON)
        sh = getattr(self.scene, "shadows", None)
        tz = (sh.utc_offset if sh is not None and sh.utc_offset is not None
              else default_utc_offset(lon))
        return {"lat": lat, "lon": lon, "tz": tz,
                "north": float(getattr(d, "north", 0.0)) if d else 0.0,
                "located": d is not None, "alt": getattr(d, "alt", 0.0)
                if d else 0.0}

    def set_location(self, lat: float, lon: float, alt: float | None = None,
                     tz: int | None = None) -> None:
        """La ubicación del proyecto (el datum de IngeTrazo). Conserva el
        norte; si el sitio se mueve lejos, el origen pasa al nuevo punto."""
        from georef.datum import SceneDatum
        old = self.datum()
        north = getattr(old, "north", 0.0) if old is not None else 0.0
        keep_alt = 0.0
        if alt is not None:
            keep_alt = float(alt)
        elif old is not None:
            dlat = (lat - old.lat) * 111320.0
            dlon = (lon - old.lon) * 111320.0 * math.cos(math.radians(lat))
            if math.hypot(dlat, dlon) < 1000.0:
                keep_alt = old.alt
        self.scene.georef = SceneDatum(lat, lon, alt=keep_alt, north=north)
        if tz is not None and getattr(self.scene, "shadows", None) is not None:
            self.scene.shadows.utc_offset = int(round(tz))
        self._datum_moved()

    def set_north(self, deg: float) -> None:
        from georef.datum import SceneDatum
        d = self.datum()
        if d is None or abs(d.north - deg) < 1e-9:
            return
        self.scene.georef = SceneDatum(d.lat, d.lon, alt=d.alt, north=deg)
        self._datum_moved()

    def _datum_moved(self) -> None:
        vp = self.app.viewport
        self.scene.version += 1
        reset = getattr(vp, "reset_tiles", None)
        if callable(reset):
            reset()
        win = self.app.window
        if getattr(win, "_terrain_on", False):
            build = getattr(win, "_build_terrain", None)
            if callable(build):
                build()
        notify = getattr(vp, "notify_scene_changed", None)
        if callable(notify):
            notify()
        self._shadows_panel_refresh()
        vp.update()
        self.changed.emit()

    # ---- Fecha y hora (las de las sombras) ---------------------------------------
    def shadows(self):
        return getattr(self.scene, "shadows", None)

    def when(self) -> tuple[int, float]:
        sh = self.shadows()
        if sh is None:
            return 172, 13.0
        return solar.doy_of(sh.month, sh.day), sh.hour + sh.minute / 60.0

    def set_when(self, doy: int | None = None, hour: float | None = None) -> None:
        sh = self.shadows()
        if sh is None:
            return
        if doy is not None:
            sh.month, sh.day = solar.month_day(int(doy))
        if hour is not None:
            mins = int(round(max(0.0, min(23.99, float(hour))) * 60))
            mins = min(mins, 23 * 60 + 59)
            sh.hour, sh.minute = mins // 60, mins % 60
        self._shadows_changed()

    def set_shadows(self, on: bool, darkness: float | None = None) -> None:
        sh = self.shadows()
        if sh is None:
            return
        sh.enabled = bool(on)
        if darkness is not None:
            sh.darkness = max(0.0, min(1.0, float(darkness)))
        self._shadows_changed()

    def set_tz(self, tz: int | None) -> None:
        sh = self.shadows()
        if sh is not None:
            sh.utc_offset = None if tz is None else int(tz)
            self._shadows_changed()

    def _shadows_changed(self) -> None:
        win = self.app.window
        act = getattr(win, "_act_shadows", None)
        sh = self.shadows()
        if act is not None and sh is not None and act.isChecked() != sh.enabled:
            act.blockSignals(True)
            act.setChecked(sh.enabled)
            act.blockSignals(False)
        self._shadows_panel_refresh()
        self.scene.version += 1
        self.app.viewport.update()
        self.changed.emit()

    def _shadows_panel_refresh(self) -> None:
        panel = getattr(self.app.window, "shadows_panel", None)
        if panel is not None and hasattr(panel, "refresh"):
            try:
                panel.refresh()
            except Exception:                         # noqa: BLE001
                pass

    def sun_now(self) -> tuple[float, float]:
        s = self.site()
        doy, hour = self.when()
        return solar.sun_at(s["lat"], s["lon"], s["tz"], doy, hour)

    def sun_times(self):
        s = self.site()
        doy, _h = self.when()
        return solar.sun_times(s["lat"], s["lon"], s["tz"], doy)

    def frame(self) -> solar.Frame:
        v = self.store.view()
        return solar.Frame(v.get("center", (0, 0, 0)), v.get("radius", 100.0),
                           self.site()["north"])

    # ---- Clima -----------------------------------------------------------------
    def epw(self):
        raw = self.store.climate()
        if raw is None or not clima.valid(raw):
            return None
        if self._epw_cache[0] is not id(raw):
            self._epw_cache = (id(raw), raw)
            self._stats_cache = (None, None)
        return raw

    def stats(self):
        epw = self.epw()
        if epw is None:
            return None
        per = dict(self.store.view()["period"])
        key = (id(epw), tuple(sorted(per.items())))
        if self._stats_cache[0] != key:
            self._stats_cache = (key, clima.stats(epw, per))
        return self._stats_cache[1]

    def hour_record(self):
        doy, hour = self.when()
        return clima.hourly(self.epw(), doy, hour)

    # ---- Animación ---------------------------------------------------------------
    def phase(self) -> float:
        return time.monotonic() - self.t0

    def update_animation(self) -> None:
        v = self.store.view()
        want = bool(v.get("on") and v["wind"]["show"] and v["wind"]["anim"]
                    and any(w.get("on") for w in self.store.content()["winds"]))
        if want and not self._wind_timer.isActive():
            self._wind_timer.start()
        elif not want and self._wind_timer.isActive():
            self._wind_timer.stop()

    def _wind_tick(self) -> None:
        vp = self.app.viewport
        if vp.isVisible():
            vp.update()

    @property
    def animating_sun(self) -> bool:
        return self._sun_timer.isActive()

    def toggle_sun_animation(self) -> bool:
        if self._sun_timer.isActive():
            self._sun_timer.stop()
        else:
            rise, sset = self.sun_times()
            doy, hour = self.when()
            if rise is not None and (hour < rise or (sset and hour >= sset)):
                self.set_when(hour=rise)
            self._sun_timer.start()
        self.changed.emit()
        return self._sun_timer.isActive()

    def _sun_tick(self) -> None:
        doy, hour = self.when()
        rise, sset = self.sun_times()
        hour += 0.25
        if sset is not None and hour > sset:
            hour = rise if rise is not None else 6.0
        elif hour >= 24.0:
            hour = 0.0
        self.set_when(hour=hour)

    def stop(self) -> None:
        self._wind_timer.stop()
        self._sun_timer.stop()
