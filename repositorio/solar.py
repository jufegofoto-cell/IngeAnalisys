# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometría del diagrama solar — trayectorias, analemas, banda anual.

La posición del sol es la del propio IngeTrazo (``core.sun``, ecuaciones
NOAA): la misma que proyecta las sombras, así el diagrama y la sombra no
se contradicen nunca. Aquí sólo se pasa de (altitud, azimut) a puntos del
modelo: X este, Y norte, Z arriba, girados por el ángulo de norte del
datum (``SceneDatum.north``) cuando el modelo no mira al norte.

Todo devuelve listas de tuplas (x, y, z) en metros; quien dibuja las
proyecta de una vez con ``ExtensionApp.world_to_pixels``.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

from core.sun import solar_position

YEAR = 2026                       # el año de ShadowSettings (no bisiesto)
OBLIQUITY = 23.44


def doy_of(month: int, day: int) -> int:
    try:
        return date(YEAR, month, day).timetuple().tm_yday
    except ValueError:
        return 80


def month_day(doy: int) -> tuple[int, int]:
    d = date(YEAR, 1, 1) + timedelta(days=max(1, min(365, int(doy))) - 1)
    return d.month, d.day


def sun_at(lat: float, lon: float, tz: float, doy: int,
           hour: float) -> tuple[float, float]:
    """``(altitud, azimut)`` en grados para la hora LOCAL del día del año."""
    m, d = month_day(doy)
    base = datetime(YEAR, m, d, tzinfo=timezone(timedelta(hours=tz)))
    when = (base + timedelta(hours=hour)).astimezone(timezone.utc)
    return solar_position(lat, lon, when)


def sun_times(lat: float, lon: float, tz: float, doy: int,
              horizon: float = -0.833) -> tuple[float | None, float | None]:
    """Hora local de salida y puesta (con refracción estándar), o None."""
    rise = sset = None
    prev = sun_at(lat, lon, tz, doy, 0.0)[0]
    step = 5.0 / 60.0
    h = step
    while h <= 24.0 + 1e-9:
        cur = sun_at(lat, lon, tz, doy, h)[0]
        if prev < horizon <= cur and rise is None:
            rise = _refine(lat, lon, tz, doy, h - step, h, horizon)
        if prev >= horizon > cur:
            sset = _refine(lat, lon, tz, doy, h - step, h, horizon)
        prev = cur
        h += step
    return rise, sset


def _refine(lat, lon, tz, doy, a, b, horizon) -> float:
    fa = sun_at(lat, lon, tz, doy, a)[0] - horizon
    for _ in range(18):
        m = 0.5 * (a + b)
        fm = sun_at(lat, lon, tz, doy, m)[0] - horizon
        if (fa < 0) == (fm < 0):
            a, fa = m, fm
        else:
            b = m
    return 0.5 * (a + b)


def fmt_hour(h: float | None) -> str:
    if h is None or (isinstance(h, float) and math.isnan(h)):
        return "—"
    hh = int(math.floor(h))
    mm = int(round((h - hh) * 60))
    if mm == 60:
        hh, mm = hh + 1, 0
    return f"{hh:02d}:{mm:02d}"


class Frame:
    """Centro y orientación del diagrama: de (alt, az) a puntos del modelo."""

    def __init__(self, center=(0.0, 0.0, 0.0), radius: float = 100.0,
                 north_deg: float = 0.0) -> None:
        self.cx, self.cy, self.cz = (float(c) for c in center)
        self.r = float(radius)
        b = math.radians(north_deg)
        self._c, self._s = math.cos(b), math.sin(b)

    def grid_to_local(self, e: float, n: float) -> tuple[float, float]:
        """Este/norte geográficos → ejes del modelo (como SceneDatum)."""
        return e * self._c + n * self._s, n * self._c - e * self._s

    def direction(self, alt: float, az: float) -> tuple[float, float, float]:
        er, ar = math.radians(alt), math.radians(az)
        x, y = self.grid_to_local(math.cos(er) * math.sin(ar),
                                  math.cos(er) * math.cos(ar))
        return x, y, math.sin(er)

    def sky(self, alt: float, az: float, scale: float = 1.0,
            lift: float = 0.0) -> tuple[float, float, float]:
        """Punto de la cúpula de radio ``r·scale`` (altitud ≥ 0)."""
        dx, dy, dz = self.direction(max(alt, 0.0), az)
        r = self.r * scale
        return (self.cx + dx * r, self.cy + dy * r, self.cz + dz * r + lift)

    def ground(self, az: float, radius: float,
               lift: float = 0.0) -> tuple[float, float, float]:
        """Punto en el plano del suelo a ``radius`` en el rumbo ``az``."""
        x, y = self.grid_to_local(math.sin(math.radians(az)) * radius,
                                  math.cos(math.radians(az)) * radius)
        return (self.cx + x, self.cy + y, self.cz + lift)


def _split_day(samples):
    """[(alt, az, …)] → tramos consecutivos con el sol sobre el horizonte."""
    runs, cur = [], []
    for s in samples:
        if s[0] > 0:
            cur.append(s)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def day_path(lat, lon, tz, doy, frame: Frame, steps_per_hour: int = 12):
    """Tramos (listas de puntos) de la trayectoria de un día."""
    samples = [sun_at(lat, lon, tz, doy, i / steps_per_hour)
               for i in range(24 * steps_per_hour + 1)]
    return [[frame.sky(a, z) for a, z in run] for run in _split_day(samples)]


def hour_marks(lat, lon, tz, doy, frame: Frame, min_alt: float = 0.5):
    """[(hora, punto, alt)] de las horas en punto con el sol arriba."""
    out = []
    for h in range(24):
        a, z = sun_at(lat, lon, tz, doy, float(h))
        if a > min_alt:
            out.append((h, frame.sky(a, z), a))
    return out


def analemmas(lat, lon, tz, frame: Frame):
    """Para cada hora, los tramos de su analema (día 1 → 365, cada 3)."""
    out = []
    for h in range(24):
        samples = [sun_at(lat, lon, tz, min(d, 365), float(h))
                   for d in range(1, 367, 3)]
        if not any(a > 0 for a, _ in samples):
            continue
        samples.append(samples[0])
        out.extend([frame.sky(a, z) for a, z in run]
                   for run in _split_day(samples) if len(run) > 1)
    return out


def _alt_az(lat: float, decl: float, ha: float) -> tuple[float, float]:
    """Altitud/azimut desde declinación y ángulo horario (radianes)."""
    phi = math.radians(lat)
    s = (math.sin(phi) * math.sin(decl)
         + math.cos(phi) * math.cos(decl) * math.cos(ha))
    alt = math.asin(max(-1.0, min(1.0, s)))
    az = math.degrees(math.atan2(
        math.sin(ha), math.cos(ha) * math.sin(phi)
        - math.tan(decl) * math.cos(phi))) + 180.0
    return math.degrees(alt), az % 360.0


def band_quads(lat: float, frame: Frame, rows: int = 16, cols: int = 96):
    """Cuadriláteros de la banda solar anual (entre ambos solsticios): la
    franja de cielo por la que pasa el sol a lo largo del año."""
    e = math.radians(OBLIQUITY)
    grid = []
    for i in range(rows + 1):
        decl = -e + 2 * e * i / rows
        row = []
        for j in range(cols + 1):
            ha = math.radians(-180.0 + 360.0 * j / cols)
            row.append(_alt_az(lat, decl, ha))
        grid.append(row)
    quads = []
    for i in range(rows):
        for j in range(cols):
            c = (grid[i][j], grid[i][j + 1], grid[i + 1][j + 1], grid[i + 1][j])
            if any(a > 0 for a, _ in c):
                quads.append([frame.sky(a, z) for a, z in c])
    return quads
