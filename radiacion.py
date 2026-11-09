# SPDX-License-Identifier: GPL-3.0-or-later
"""Radiación solar incidente sobre las superficies del modelo.

El método de BioSim (y de Ecotect / Ladybug) para cada punto de muestra
de una superficie, acumulado en el periodo elegido (kWh/m²):

    I = I_dir + I_dif + I_ref
    I_dir = DNI · cos θ · soleado      (θ: sol ↔ normal; soleado 0/1 por
                                         trazado de rayos contra TODO el
                                         modelo visible, contexto incluido)
    I_dif = DHI · F_cielo              (cielo isótropo; F_cielo medido con
                                         los 145 parches de Tregenza, así
                                         el patio cerrado ve menos cielo)
    I_ref = GHI · ρ · (1 − cos β) / 2   (reflejada por el suelo, albedo ρ)

con DNI/DHI del EPW (hora EPW h = intervalo h−1…h, sol a mitad de hora),
o, sin EPW, un cielo despejado teórico (Meinel), avisado como tal.

Eficiencia: las horas del periodo se agrupan por posición del sol (1°
para un día, 2° para periodos largos): un año son ~1000 direcciones, no
4400 horas. Cada dirección se prueba de una vez para todos los puntos con
una rejilla 2D en el plano perpendicular al sol (numpy, sin bucles por
punto).

Las superficies se subdividen en triángulos de lado ≤ ``grid`` m: cada
uno es un punto de muestra (malla de análisis) o, en modo «por cara», se
promedian por área en la cara.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import numpy as np

from core.sun import solar_position

from . import solar

YEAR = solar.YEAR
_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


# ---- Paletas ------------------------------------------------------------------

PALETTES = {
    # Ecotect «Incident Solar Radiation»: azul → violeta → rojo → amarillo.
    "ecotect": ("Ecotect (azul → amarillo; en sombras, amarillo = luz)",
                ((0.0, (0, 0, 255)), (0.15, (64, 0, 200)), (0.3, (110, 0, 150)),
                 (0.45, (150, 0, 110)), (0.6, (210, 0, 50)), (0.72, (255, 30, 0)),
                 (0.85, (255, 140, 0)), (1.0, (255, 255, 0)))),
    # BioSim «Total radiation»: azul → cian → verde → amarillo → rojo.
    "grises": ("Grises (blanco = sol, negro = sombra)",
               ((0.0, (255, 255, 255)), (1.0, (20, 20, 20)))),
    # Ecotect «Shading time [h]»: una clase por hora, de amarillo (sol) a
    # azul marino (sombra). Se pinta por clases, no en degradado.
    "ecotect_horas": ("Ecotect por horas (amarillo = sol → azul = sombra)",
                      tuple((i / 10.0, c) for i, c in enumerate((
                          (255, 255, 51), (255, 178, 0), (255, 122, 0),
                          (255, 26, 26), (240, 20, 110), (225, 25, 175),
                          (20, 178, 178), (26, 138, 204), (26, 80, 180),
                          (42, 26, 153), (13, 13, 102))))),
    "termica": ("Térmica (negro → blanco; en sombras, negro = sombra)",
                ((0.0, (0, 0, 0)), (0.35, (120, 20, 20)), (0.65, (240, 120, 0)),
                 (0.85, (255, 220, 60)), (1.0, (255, 255, 255)))),
}


#: Paletas que se pintan por clases (bandas), como en Ecotect.
STEPPED = {"ecotect_horas"}

#: Paletas cuyo extremo alto es el claro (amarillo, blanco): en los mapas
#: de sombras se leen al revés — lo más iluminado claro, lo más sombreado
#: oscuro (azul, negro). En radiación se usan tal cual.
LIGHT_HIGH = {"ecotect", "termica"}


def color_at(t: float, palette: str = "ecotect") -> tuple[int, int, int]:
    stops = PALETTES.get(palette, PALETTES["ecotect"])[1]
    t = max(0.0, min(1.0, float(t)))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(int(round(a + (b - a) * f)) for a, b in zip(c0, c1))
    return stops[-1][1]


def colors_for(values, vmin: float, vmax: float, palette: str) -> np.ndarray:
    """Colores 0–1 (n, 3) — una tabla de 256 tonos, sin bucle por valor."""
    lut = np.asarray([color_at(i / 255.0, palette) for i in range(256)],
                     float) / 255.0
    v = np.asarray(values, float)
    span = max(vmax - vmin, 1e-9)
    idx = np.clip(np.round((v - vmin) / span * 255), 0, 255).astype(int)
    return lut[idx]


# ---- Periodo y clima ----------------------------------------------------------

def period_hours(kind: str, doy: int = 172, month: int = 6,
                 period: dict | None = None) -> list[int]:
    """Índices horarios del año (0…8759) del periodo."""
    def day_range(d0, d1):
        return [h for d in range(d0, d1 + 1) for h in range((d - 1) * 24, d * 24)]
    if kind == "day":
        return day_range(doy, doy)
    if kind == "month":
        d0 = sum(_DAYS[:month - 1]) + 1
        return day_range(d0, d0 + _DAYS[month - 1] - 1)
    if kind == "period" and period:
        from .clima import in_period
        out = []
        for i in range(8760):
            d = date(YEAR, 1, 1) + timedelta(days=i // 24)
            if in_period(d.month, i % 24, period):
                out.append(i)
        return out
    return list(range(8760))


def _clear_sky(alt: float) -> tuple[float, float]:
    """DNI, DHI (W/m²) de cielo despejado: Meinel con la masa de aire de
    Kasten–Young; difusa ≈ 10 % de la directa normal (orden ASHRAE)."""
    if alt <= 0:
        return 0.0, 0.0
    z = math.radians(90.0 - alt)
    am = 1.0 / (math.cos(z) + 0.50572 * (96.07995 - math.degrees(z)) ** -1.6364)
    dni = 1353.0 * 0.7 ** (am ** 0.678)
    return dni, 0.1 * dni


def sun_bins(site: dict, hours, epw=None, step_deg: float | None = None):
    """Agrupa las horas por posición del sol. Devuelve
    ``(dirs (k,3) en el modelo, W_dir (k,) Wh/m² de DNI, DHI_total,
    GHI_total, fuente)``."""
    lat, lon, tz = site["lat"], site["lon"], site["tz"]
    hours = list(hours)
    step = step_deg or (1.0 if len(hours) <= 48 else
                        2.0 if len(hours) <= 31 * 24 else 3.0)
    fr = solar.Frame((0, 0, 0), 1.0, site.get("north", 0.0))
    bins: dict = {}
    dhi_t = ghi_t = 0.0
    tzinfo = timezone(timedelta(hours=tz))
    for i in hours:
        d = date(YEAR, 1, 1) + timedelta(days=i // 24)
        when = datetime(d.year, d.month, d.day, i % 24, 30, tzinfo=tzinfo)
        alt, az = solar_position(lat, lon, when.astimezone(timezone.utc))
        if epw is not None:
            dni, dhi = float(epw["dni"][i]), float(epw["dhr"][i])
        else:
            dni, dhi = _clear_sky(alt)
        if alt <= 0.5:
            continue
        if dni + dhi < 1:
            continue
        ghi = dni * math.sin(math.radians(alt)) + dhi
        dhi_t += dhi
        ghi_t += ghi
        if dni <= 0:
            continue
        key = (round(alt / step) * step, round(az / step) * step % 360)
        bins[key] = bins.get(key, 0.0) + dni
    dirs = np.asarray([fr.direction(a, z) for a, z in bins], float).reshape(-1, 3)
    w = np.asarray(list(bins.values()), float)
    return dirs, w, dhi_t, ghi_t, ("EPW" if epw is not None else
                                  tr("cielo despejado teórico"))


def tregenza(north: float = 0.0):
    """Las 145 direcciones de Tregenza y su ángulo sólido."""
    bands = ((6, 30), (18, 30), (30, 24), (42, 24), (54, 18), (66, 12), (78, 6))
    fr = solar.Frame((0, 0, 0), 1.0, north)
    dirs, omega = [], []
    for alt, n in bands:
        lo, hi = math.radians(alt - 6), math.radians(alt + 6)
        om = 2 * math.pi * (math.sin(hi) - math.sin(lo)) / n
        for k in range(n):
            dirs.append(fr.direction(alt, k * 360.0 / n))
            omega.append(om)
    dirs.append((0.0, 0.0, 1.0))
    omega.append(2 * math.pi * (1 - math.sin(math.radians(84))))
    return np.asarray(dirs), np.asarray(omega)


# ---- Geometría ------------------------------------------------------------------

def _mat(m):
    if m is None:
        return None
    return np.asarray(m.data(), float).reshape(4, 4).T      # columna → fila


def _xf(pts: np.ndarray, M) -> np.ndarray:
    if M is None:
        return pts
    return pts @ M[:3, :3].T + M[:3, 3]


def face_tris(face, M) -> np.ndarray:
    """Triángulos de la cara en coordenadas de mundo: (t, 3, 3)."""
    tris = face.triangulate()
    if not tris:
        return np.zeros((0, 3, 3))
    a = np.asarray([[(p.x(), p.y(), p.z()) for p in t] for t in tris], float)
    return _xf(a.reshape(-1, 3), M).reshape(-1, 3, 3)


def subdivide(tris: np.ndarray, grid: float) -> np.ndarray:
    """Cada triángulo en k² triángulos iguales con lado ≤ ``grid``."""
    out = []
    for t in tris:
        e = max(np.linalg.norm(t[1] - t[0]), np.linalg.norm(t[2] - t[1]),
                np.linalg.norm(t[0] - t[2]))
        k = max(1, min(64, int(math.ceil(e / max(grid, 1e-3)))))
        if k == 1:
            out.append(t[None])
            continue
        a, b, c = t
        u, v = (b - a) / k, (c - a) / k
        sub = []
        for i in range(k):
            for j in range(k - i):
                p = a + u * i + v * j
                sub.append((p, p + u, p + v))
                if i + j < k - 1:
                    sub.append((p + u, p + u + v, p + v))
        out.append(np.asarray(sub))
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


class Targets:
    """Las superficies analizadas: celdas (triángulos), su cara de origen,
    normal y área."""

    def __init__(self) -> None:
        self.cells = []          # (n,3,3) por cara
        self.normals = []        # (3,) por cara
        self.polys = []          # (outer, holes) en mundo, por cara
        self.ground = []         # True para el suelo generado
        self.n_faces = 0

    def add_face(self, face, M, grid: float) -> None:
        tris = face_tris(face, M)
        if not len(tris):
            return
        n = face.normal()
        nv = np.array([n.x(), n.y(), n.z()], float)
        if M is not None:
            nv = M[:3, :3] @ nv
        ln = np.linalg.norm(nv)
        if ln < 1e-9:
            return
        nv /= ln
        self.cells.append(subdivide(tris, grid))
        self.normals.append(nv)
        outer = _xf(np.asarray([(p.x(), p.y(), p.z()) for p in face.vertices]), M)
        holes = [_xf(np.asarray([(p.x(), p.y(), p.z()) for p in h]), M)
                 for h in face.holes]
        self.polys.append((outer, holes))
        self.ground.append(False)
        self.n_faces += 1

    def add_plane(self, x0, y0, x1, y1, z, grid: float, cover=None) -> None:
        """Un rectángulo horizontal (el suelo) en celdas cuadradas, sin las
        celdas que quedan bajo ``cover`` (caras a ras de suelo)."""
        nx = max(1, int(math.ceil((x1 - x0) / grid)))
        ny = max(1, int(math.ceil((y1 - y0) / grid)))
        xs = np.linspace(x0, x1, nx + 1)
        ys = np.linspace(y0, y1, ny + 1)
        X, Y = np.meshgrid(xs, ys, indexing="ij")
        a = np.stack([X[:-1, :-1], Y[:-1, :-1]], -1).reshape(-1, 2)
        b = np.stack([X[1:, :-1], Y[1:, :-1]], -1).reshape(-1, 2)
        c = np.stack([X[1:, 1:], Y[1:, 1:]], -1).reshape(-1, 2)
        d = np.stack([X[:-1, 1:], Y[:-1, 1:]], -1).reshape(-1, 2)

        def z3(p):
            return np.column_stack([p, np.full(len(p), z)])
        t1 = np.stack([z3(a), z3(b), z3(c)], 1)
        t2 = np.stack([z3(a), z3(c), z3(d)], 1)
        cells = np.concatenate([t1, t2])
        if cover is not None and len(cells):
            probe = cells.mean(axis=1) - np.array([0.0, 0.0, 0.5])
            free = cover.lit(probe, np.array([0.0, 0.0, 1.0]))
            cells = cells[free]
        if not len(cells):
            return
        self.cells.append(cells)
        self.normals.append(np.array([0.0, 0.0, 1.0]))
        outer = np.array([[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]])
        self.polys.append((outer, []))
        self.ground.append(True)
        self.n_faces += 1

    def arrays(self):
        if not self.cells:
            z = np.zeros((0, 3))
            return z, z, np.zeros(0), np.zeros(0, int)
        tris = np.concatenate(self.cells)
        owner = np.concatenate([np.full(len(c), i) for i, c in enumerate(self.cells)])
        normals = np.asarray(self.normals)[owner]
        cent = tris.mean(axis=1)
        area = 0.5 * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0],
                                             tris[:, 2] - tris[:, 0]), axis=1)
        return cent, normals, area, owner


# ---- Trazado de rayos -------------------------------------------------------------

def _basis(d):
    a = np.array([1.0, 0, 0]) if abs(d[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(d, a)
    u /= np.linalg.norm(u)
    return u, np.cross(d, u)


class Occluders:
    """Todos los triángulos que dan sombra, listos para probar direcciones."""

    def __init__(self, tris: np.ndarray) -> None:
        self.tris = np.asarray(tris, float).reshape(-1, 3, 3)

    def lit(self, pts: np.ndarray, d: np.ndarray) -> np.ndarray:
        """¿Qué puntos ven la dirección ``d`` (hacia el sol / el cielo)?"""
        n = len(pts)
        lit = np.ones(n, bool)
        if not len(self.tris) or not n:
            return lit
        u, v = _basis(d)
        T = self.tris
        tu, tv, td = T @ u, T @ v, T @ d                     # (t,3)
        pu, pv, pd = pts @ u, pts @ v, pts @ d
        # Sólo triángulos con algún vértice «delante» (hacia el sol) del
        # punto más atrasado: el resto no puede tapar nada.
        keep = td.max(axis=1) > pd.min() + 1e-6
        tu, tv, td = tu[keep], tv[keep], td[keep]
        if not len(tu):
            return lit
        lo_u, hi_u = min(tu.min(), pu.min()), max(tu.max(), pu.max())
        lo_v, hi_v = min(tv.min(), pv.min()), max(tv.max(), pv.max())
        span = max(hi_u - lo_u, hi_v - lo_v, 1e-6)
        g = int(min(256, max(8, math.sqrt(len(tu)) * 1.5)))
        cs = span / g
        ci0 = np.clip(((tu.min(1) - lo_u) / cs).astype(int), 0, g - 1)
        ci1 = np.clip(((tu.max(1) - lo_u) / cs).astype(int), 0, g - 1)
        cj0 = np.clip(((tv.min(1) - lo_v) / cs).astype(int), 0, g - 1)
        cj1 = np.clip(((tv.max(1) - lo_v) / cs).astype(int), 0, g - 1)
        nu, nv = ci1 - ci0 + 1, cj1 - cj0 + 1
        cnt = nu * nv
        big = cnt > 4096                                    # suelos enormes
        tri_ids, cells = [], []
        small = np.nonzero(~big)[0]
        if len(small):
            reps = cnt[small]
            tid = np.repeat(small, reps)
            off = np.arange(reps.sum()) - np.repeat(np.cumsum(reps) - reps, reps)
            nvr = np.repeat(nv[small], reps)
            ii = np.repeat(ci0[small], reps) + off // nvr
            jj = np.repeat(cj0[small], reps) + off % nvr
            tri_ids.append(tid)
            cells.append(ii * g + jj)
        pi = np.clip(((pu - lo_u) / cs).astype(int), 0, g - 1)
        pj = np.clip(((pv - lo_v) / cs).astype(int), 0, g - 1)
        pcell = pi * g + pj
        pairs_p, pairs_t = [], []
        if tri_ids:
            tid = np.concatenate(tri_ids)
            cel = np.concatenate(cells)
            order = np.argsort(cel, kind="stable")
            tid, cel = tid[order], cel[order]
            start = np.searchsorted(cel, pcell, "left")
            stop = np.searchsorted(cel, pcell, "right")
            cnt_p = stop - start
            if cnt_p.sum():
                pp = np.repeat(np.arange(n), cnt_p)
                off = np.arange(cnt_p.sum()) - np.repeat(np.cumsum(cnt_p) - cnt_p,
                                                          cnt_p)
                pairs_p.append(pp)
                pairs_t.append(tid[np.repeat(start, cnt_p) + off])
        bigs = np.nonzero(big)[0]
        if len(bigs):
            pairs_p.append(np.repeat(np.arange(n), len(bigs)))
            pairs_t.append(np.tile(bigs, n))
        if not pairs_p:
            return lit
        P = np.concatenate(pairs_p)
        Tt = np.concatenate(pairs_t)
        # Prueba exacta: el punto dentro del triángulo proyectado y el
        # triángulo, en ese lugar, más cerca del sol que el punto.
        for a in range(0, len(P), 2_000_000):
            p, t = P[a:a + 2_000_000], Tt[a:a + 2_000_000]
            x, y = pu[p], pv[p]
            x0, y0 = tu[t, 0], tv[t, 0]
            x1, y1 = tu[t, 1], tv[t, 1]
            x2, y2 = tu[t, 2], tv[t, 2]
            den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            ok = np.abs(den) > 1e-12
            den = np.where(ok, den, 1.0)
            w0 = ((y1 - y2) * (x - x2) + (x2 - x1) * (y - y2)) / den
            w1 = ((y2 - y0) * (x - x2) + (x0 - x2) * (y - y2)) / den
            w2 = 1.0 - w0 - w1
            inside = ok & (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
            depth = w0 * td[t, 0] + w1 * td[t, 1] + w2 * td[t, 2]
            hit = inside & (depth > pd[p] + 1e-4)
            lit[p[hit]] = False
        return lit


def occluders_from_scene(scene, skip=()) -> Occluders:
    """Triángulos de todo lo visible (modelo, masas, contexto…), menos los
    grupos en ``skip`` (los propios mapas de radiación)."""
    skip_faces = set()
    for g in skip:
        if g is not None:
            skip_faces.update(g.mesh.faces)
    chunks = []
    for f, m in scene.iter_world_faces():
        if f in skip_faces or f.attrs.get("hidden"):
            continue
        t = face_tris(f, _mat(m))
        if len(t):
            chunks.append(t)
    return Occluders(np.concatenate(chunks) if chunks else np.zeros((0, 3, 3)))


# ---- Cálculo --------------------------------------------------------------------

def compute(targets: Targets, occ: Occluders, site: dict, hours, epw=None,
            albedo: float = 0.2, progress=None) -> dict:
    """Radiación por celda (kWh/m² en el periodo) y por cara."""
    cent, normals, area, owner = targets.arrays()
    n = len(cent)
    pts = cent + normals * 0.02                     # fuera de su propia cara
    dirs, w, dhi_t, ghi_t, source = sun_bins(site, hours, epw)
    sky_d, sky_om = tregenza(site.get("north", 0.0))
    steps = len(dirs) + len(sky_d)
    direct = np.zeros(n)
    done = 0
    for k in range(len(dirs)):
        cos = normals @ dirs[k]
        front = cos > 1e-6
        if front.any():
            idx = np.nonzero(front)[0]
            lit = occ.lit(pts[idx], dirs[k])
            direct[idx[lit]] += w[k] * cos[idx[lit]]
        done += 1
        if progress and done % 10 == 0 and progress(done / steps):
            return {}
    fsky = np.zeros(n)
    for k in range(len(sky_d)):
        cos = normals @ sky_d[k]
        front = cos > 1e-6
        if front.any():
            idx = np.nonzero(front)[0]
            lit = occ.lit(pts[idx], sky_d[k])
            fsky[idx[lit]] += cos[idx[lit]] * sky_om[k] / math.pi
        done += 1
        if progress and done % 10 == 0 and progress(done / steps):
            return {}
    # Normalizar a la vista exacta del cielo libre (la discretización da
    # ~1 en un techo, no 1.000): un plano horizontal despejado ve 1.
    open_h = float(np.sum(sky_d[:, 2].clip(0) * sky_om) / math.pi)
    fsky = np.clip(fsky / max(open_h, 1e-9), 0.0, 1.0)
    cosb = normals[:, 2]
    floor = cosb < -0.98                             # caras hacia el suelo
    gnd = np.where(floor, 0.0, (1 - cosb) / 2)
    diffuse = dhi_t * fsky
    refl = ghi_t * albedo * gnd
    total = (direct + diffuse + refl) / 1000.0       # Wh → kWh
    nf = targets.n_faces
    face_val = np.zeros(nf)
    face_area = np.zeros(nf)
    np.add.at(face_val, owner, total * area)
    np.add.at(face_area, owner, area)
    face_val = face_val / np.maximum(face_area, 1e-12)
    return {"cell": total, "cell_dir": direct / 1000.0,
            "cell_dif": diffuse / 1000.0, "cell_ref": refl / 1000.0,
            "face": face_val, "face_area": face_area, "owner": owner,
            "area": area, "source": source, "n_sun": len(dirs),
            "svf": fsky}


# ---- Horas de sombra (estudio de sombras) ---------------------------------------

def compute_shadow_hours(targets: Targets, occ: Occluders, site: dict, doy: int,
                         h0: float, h1: float, step_min: int,
                         progress=None) -> dict:
    """Horas en sombra de cada celda entre ``h0`` y ``h1`` (hora local) del
    día ``doy``, probando el sol cada ``step_min`` minutos. Una cara que da
    la espalda al sol está en sombra (sombra propia); cada paso con el sol
    sobre el horizonte vale Δt."""
    cent, normals, area, owner = targets.arrays()
    pts = cent + normals * 0.02
    fr = solar.Frame((0, 0, 0), 1.0, site.get("north", 0.0))
    dt = step_min / 60.0
    times = []
    t = float(h0)
    while t <= h1 + 1e-6:
        times.append(round(t, 4))
        t += dt
    shade = np.zeros(len(cent))
    n_sun = 0
    for i, t in enumerate(times):
        if progress and progress(i / max(1, len(times))):
            return {}
        alt, az = solar.sun_at(site["lat"], site["lon"], site["tz"], doy, t)
        if alt <= 0.5:
            continue
        n_sun += 1
        d = np.asarray(fr.direction(alt, az))
        cos = normals @ d
        lit = np.zeros(len(cent), bool)
        idx = np.nonzero(cos > 1e-6)[0]
        if len(idx):
            lit[idx] = occ.lit(pts[idx], d)
        shade[~lit] += dt
    nf = targets.n_faces
    face_val = np.zeros(nf)
    face_area = np.zeros(nf)
    np.add.at(face_val, owner, shade * area)
    np.add.at(face_area, owner, area)
    return {"cell": shade, "face": face_val / np.maximum(face_area, 1e-12),
            "area": area, "steps": n_sun, "hours": n_sun * dt, "times": times}


# ---- Los mapas en el modelo ------------------------------------------------------

#: Las dos clases de mapa: título del grupo y capa.
KINDS = {"radiation": "Radiación solar", "shadows": "Estudio de sombras"}
LAYER = "Radiación solar"
OFFSET = 0.08            # m sobre la superficie: sin «z-fighting» de lejos


def find_group(scene, key: str, kind: str = "radiation"):
    for g in scene.groups:
        ext = getattr(g, "ext", None) or {}
        if isinstance(ext.get(key), dict) and ext[key].get("kind") == kind:
            return g
    return None


def map_groups(scene, key: str) -> list:
    return [g for g in (find_group(scene, key, k) for k in KINDS) if g is not None]


def visible_map(scene, key: str):
    """El mapa que se ve (sólo uno a la vez)."""
    for g in map_groups(scene, key):
        if not g.hidden and scene.entity_visible(g):
            return g
    return None


def build_group(key: str, targets: Targets, res: dict, mode: str,
                meta: dict, kind: str = "radiation"):
    """El grupo con el mapa: una cara por celda (``mode`` "cells") o por
    superficie ("faces"), separada de la original y pintada."""
    from core.group import Group
    from core.mesh import Mesh
    pos, sizes, counts, values = [], [], [], []
    lift = np.asarray([OFFSET * (0.5 if g else 1.0) for g in targets.ground])
    if mode == "faces":
        for i, (outer, holes) in enumerate(targets.polys):
            nv = targets.normals[i] * lift[i]
            rings = [outer + nv] + [h + nv for h in holes]
            for r in rings:
                pos.extend(r.tolist())
                sizes.append(len(r))
            counts.append(len(rings))
            values.append(float(res["face"][i]))
    else:
        cent, normals, _a, owner = targets.arrays()
        tris = np.concatenate(targets.cells)
        tris = tris + (normals * lift[owner][:, None])[:, None, :]
        pos = tris.reshape(-1, 3).tolist()
        sizes = [3] * len(tris)
        counts = [1] * len(tris)
        values = [float(v) for v in res["cell"]]
    mesh = Mesh()
    faces = mesh.add_faces_bulk(np.asarray(pos, float),
                                np.asarray(sizes, np.int64),
                                np.asarray(counts, np.int64))
    if mode != "faces":
        for e in mesh.edges:                 # sin la trama de triángulos
            if len(e.faces) > 1:
                e.hidden = True
    g = Group(mesh, name=tr(KINDS[kind]))
    g.component = False
    g.ext = {key: dict(meta, kind=kind, mode=mode,
                       values=[round(v, 3) for v in values[:len(faces)]])}
    paint(g, key)
    return g


def value_range(g, key: str) -> tuple[float, float]:
    vals = (g.ext.get(key) or {}).get("values") or [0.0]
    return float(min(vals)), float(max(vals))


def scale(data: dict) -> dict:
    """La escala de un mapa, la misma para pintarlo y para su leyenda.

    - ``lo``/``hi``: valores que se muestran (para sombras, horas de sombra o,
      con ``show`` = "sun", horas de sol = total − sombra);
    - ``stepped``: por clases (una por hora en sombras, 10 en radiación);
    - ``invert``: en horas de sol se invierte la paleta para que el color
      del sol siga siendo el mismo (amarillo / blanco = soleado)."""
    vals = data.get("values") or [0.0]
    shadows = data.get("kind") == "shadows"
    sun = shadows and data.get("show") == "sun"
    total = float(data.get("hours", max(vals)))
    if shadows:
        lo, hi = 0.0, total
    else:
        lo, hi = float(min(vals)), float(max(vals))
        if not data.get("auto", True):
            lo, hi = float(data.get("vmin", lo)), float(data.get("vmax", hi))
    pal = data.get("palette", "grises" if shadows else "ecotect")
    if pal not in PALETTES:                      # p. ej. «BioSim», ya retirada
        pal = "grises" if shadows else "ecotect"
    stepped = pal in STEPPED
    n = 10
    if stepped and shadows:
        n = max(1, int(math.ceil(total - 1e-6)))
    return {"lo": lo, "hi": hi, "palette": pal, "stepped": stepped, "n": n,
            "invert": sun, "sun": sun, "total": total, "shadows": shadows}


def scale_t(sc: dict, disp) -> np.ndarray:
    """Valor mostrado → posición 0–1 en la paleta."""
    v = np.asarray(disp, float)
    span = max(sc["hi"] - sc["lo"], 1e-9)
    if sc["stepped"]:
        if sc["shadows"]:
            # Las clases se cuentan siempre en horas de sombra: en «horas de
            # sol» cada punto conserva su color y sólo cambian los rótulos.
            shade = sc["total"] - v if sc["sun"] else v
            cls = np.floor(shade + 1e-6)
            return np.clip(cls, 0, sc["n"]) / sc["n"]
        else:
            cls = np.floor((v - sc["lo"]) / span * sc["n"] + 1e-9)
        t = np.clip(cls, 0, sc["n"]) / sc["n"]
    else:
        t = np.clip((v - sc["lo"]) / span, 0.0, 1.0)
    t = 1.0 - t if sc["invert"] else t           # ahora: fracción en sombra
    if sc["shadows"] and sc["palette"] in LIGHT_HIGH:
        t = 1.0 - t                              # claro = luz, oscuro = sombra
    return t


def shown_values(data: dict) -> np.ndarray:
    vals = np.asarray(data.get("values") or [], float)
    if data.get("kind") == "shadows" and data.get("show") == "sun":
        return float(data.get("hours", 0.0)) - vals
    return vals


def paint(g, key: str) -> bool:
    """Colorea las caras del mapa con la paleta y la escala guardadas."""
    data = g.ext.get(key) or {}
    vals = data.get("values") or []
    faces = list(g.mesh.faces)
    if len(vals) != len(faces):
        return False
    sc = scale(data)
    data["lo"], data["hi"] = sc["lo"], sc["hi"]
    t = scale_t(sc, shown_values(data))
    if sc["stepped"]:
        # Clases: el color exacto de cada una (pocas, sin tabla).
        uniq, inv = np.unique(np.round(t, 9), return_inverse=True)
        table = np.asarray([color_at(u, sc["palette"]) for u in uniq],
                           float) / 255.0
        cols = table[inv]
    else:
        lut = np.asarray([color_at(i / 255.0, sc["palette"])
                          for i in range(256)], float) / 255.0
        cols = lut[np.clip(np.round(t * 255), 0, 255).astype(int)]
    for f, c in zip(faces, cols):
        f.attrs["color"] = (float(c[0]), float(c[1]), float(c[2]))
    mesh = g.mesh
    mesh._chunk_dirty = True
    if hasattr(mesh, "_mut_serial"):
        mesh._mut_serial += 1
    return True


# ---- De principio a fin ------------------------------------------------------------

MAX_CELLS = 60000


def collect_targets(sitio, mode: str, grid: float, ground: bool = False,
                    ground_r: float = 0.0) -> Targets:
    """Superficies a analizar: la selección, las masas y lotes, o todo el
    modelo visible (sin el contexto OSM ni los mapas), y si se pide un
    suelo alrededor (donde caen las sombras aunque no haya geometría)."""
    from core.group import iter_placements
    from core.mesh import Face

    from . import acciones as act
    scene = sitio.scene
    key = sitio.store.key
    maps = map_groups(scene, key)
    ctx_g = act.context_group(sitio)
    T = Targets()

    def add_group(g0):
        for g, m in iter_placements(g0):
            M = _mat(m)
            for f in g.mesh.faces:
                if not f.attrs.get("hidden"):
                    T.add_face(f, M, grid)

    if mode == "sel":
        for e in list(scene.selection):
            if isinstance(e, Face):
                T.add_face(e, None, grid)
            elif hasattr(e, "mesh") and e not in maps:
                add_group(e)
    elif mode == "masses":
        for g in act.masses(sitio):
            if scene.entity_visible(g):
                add_group(g)
    elif mode == "all":
        skip = set()
        for g in maps + [ctx_g]:
            if g is not None:
                skip.update(g.mesh.faces)
        for f, m in scene.iter_world_faces():
            if f not in skip and not f.attrs.get("hidden"):
                T.add_face(f, _mat(m), grid)
    if ground:
        add_ground(T, sitio, grid, ground_r, maps + [ctx_g])
    return T


def add_ground(T: Targets, sitio, grid: float, radius: float, skip) -> None:
    """Un suelo cuadrado bajo el modelo: alrededor de las superficies
    elegidas (o de todo el modelo), con margen para las sombras largas."""
    if T.cells:
        pts = np.concatenate(T.cells).reshape(-1, 3)
    else:
        chunks = []
        skip_f = set()
        for g in skip:
            if g is not None:
                skip_f.update(g.mesh.faces)
        for f, m in sitio.scene.iter_world_faces():
            if f not in skip_f:
                chunks.append(face_tris(f, _mat(m)).reshape(-1, 3))
        pts = np.concatenate(chunks) if chunks else np.zeros((1, 3))
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    height = max(hi[2] - lo[2], 3.0)
    margin = float(radius) if radius > 0 else min(max(2.5 * height, 15.0), 150.0)
    x0, y0 = lo[0] - margin, lo[1] - margin
    x1, y1 = hi[0] + margin, hi[1] + margin
    g = max(grid, max(x1 - x0, y1 - y0) / 160.0)    # ≤ ~50 000 celdas
    z0 = float(lo[2])
    # Fuera del suelo lo que ya está cubierto a ras de suelo: las bases de
    # los edificios (también los del contexto) y los lotes. Ahí el suelo no
    # cuenta y su mapa parpadearía contra esas caras.
    flat = []
    skip_f = set()
    for gg in skip:
        if gg is not None and gg is not _ctx_of(skip):
            skip_f.update(gg.mesh.faces)
    for f, m in sitio.scene.iter_world_faces():
        if f in skip_f:
            continue
        t = face_tris(f, _mat(m))
        if len(t):
            zz = t[:, :, 2]
            keep = (np.abs(zz - z0) < 0.3).all(axis=1)
            if keep.any():
                flat.append(t[keep])
    T.add_plane(x0, y0, x1, y1, z0, g,
                cover=Occluders(np.concatenate(flat)) if flat else None)


def _ctx_of(skip):
    """En ``skip`` van los mapas y, al final, el grupo del contexto: el
    contexto SÍ cuenta para recortar el suelo bajo sus edificios."""
    return skip[-1] if skip else None


def n_cells(T: Targets) -> int:
    return int(sum(len(c) for c in T.cells))


def _occ(sitio):
    return occluders_from_scene(sitio.scene,
                                skip=map_groups(sitio.scene, sitio.store.key))


def _targets(sitio, settings):
    from .acciones import SiteError
    grid = float(settings.get("grid", 1.0))
    target = settings.get("target", "all")
    ground = bool(settings.get("ground", False))
    gr = float(settings.get("ground_r", 0.0))
    T = collect_targets(sitio, target, grid, ground, gr)
    if not T.n_faces or not n_cells(T):
        raise SiteError({"sel": "Selecciona las caras o grupos a analizar.",
                         "masses": "No hay masas ni lotes visibles.",
                         }.get(target, "No hay superficies visibles que "
                                       "analizar."))
    while n_cells(T) > MAX_CELLS and grid < 20:
        grid *= 1.6
        T = collect_targets(sitio, target, grid, ground, gr)
    return T, grid


def _place(sitio, kind: str, new, message: str) -> None:
    """Deja el mapa nuevo (sustituye al de su clase y oculta el otro)."""
    from .datos import run_step
    from .geometria import ensure_layer
    key = sitio.store.key
    old = find_group(sitio.scene, key, kind)
    others = [g for g in map_groups(sitio.scene, key) if g is not old]

    def mutate(sc):
        if old is not None and old in sc.groups:
            sc.groups.remove(old)
            sc.selection.discard(old)
        for o in others:
            o.hidden = True
        new.layer = ensure_layer(sc, LAYER)
        new.locked = True
        sc.groups.append(new)

    run_step(sitio.app.viewport, mutate, message=message)


def analyse(sitio, settings: dict, progress=None) -> dict | None:
    """Radiación incidente: calcula y deja el mapa en el modelo (un paso
    de deshacer). Devuelve el resumen, o None si se canceló."""
    import time
    t0 = time.monotonic()
    T, grid = _targets(sitio, settings)
    doy, _h = sitio.when()
    view = sitio.store.view()
    hours = period_hours(settings.get("period", "day"), doy,
                         int(settings.get("month", 6)), view.get("period"))
    res = compute(T, _occ(sitio), sitio.site(), hours, sitio.epw(),
                  float(settings.get("albedo", 0.2)), progress)
    if not res:
        return None
    key = sitio.store.key
    per = settings.get("period", "day")
    m, d = solar.month_day(doy)
    from .clima import MONTHS, MONTHS_LONG
    label = {"day": f"{d} {MONTHS_LONG[m - 1]}",
             "month": MONTHS_LONG[int(settings.get('month', 6)) - 1].capitalize(),
             "period": tr("Periodo de análisis ({m0}–{m1}, {h0}–{h1} h)",
                 m0=MONTHS[view['period']['m0'] - 1], m1=MONTHS[view['period']['m1'] - 1],
                 h0=view['period']['h0'], h1=view['period']['h1']),
             "year": tr("Año completo")}.get(per, per)
    meta = {"period": label, "source": res["source"], "grid": round(grid, 2),
            "palette": settings.get("palette", "ecotect"),
            "auto": bool(settings.get("auto", True)),
            "vmin": float(settings.get("vmin", 0.0)),
            "vmax": float(settings.get("vmax", 1000.0))}
    new = build_group(key, T, res, settings.get("mode", "cells"), meta)
    _place(sitio, "radiation", new, tr("Mapa de radiación listo."))
    a, tot = res["area"], res["cell"]
    summary = {
        "faces": T.n_faces, "cells": len(tot), "area": float(a.sum()),
        "min": float(tot.min()), "max": float(tot.max()),
        "mean": float((tot * a).sum() / max(a.sum(), 1e-9)),
        "energy": float((tot * a).sum()), "source": res["source"],
        "n_sun": res["n_sun"], "grid": grid, "period": label,
        "seconds": time.monotonic() - t0}
    new.ext[key]["summary"] = summary
    sitio.changed.emit()
    return summary


def shadow_study(sitio, settings: dict, progress=None) -> dict | None:
    """Estudio de sombras del día de la sección 4, sobre el modelo y el
    suelo, en la vista principal: horas de sombra por punto."""
    import time

    from .acciones import SiteError
    from .clima import MONTHS
    t0 = time.monotonic()
    h0, h1 = float(settings.get("h0", 7)), float(settings.get("h1", 18))
    step = int(settings.get("step", 30))
    if h1 <= h0:
        raise SiteError("La hora final debe ser mayor que la inicial.")
    T, grid = _targets(sitio, settings)
    doy, _h = sitio.when()
    res = compute_shadow_hours(T, _occ(sitio), sitio.site(), doy, h0, h1, step,
                               progress)
    if not res:
        return None
    if not res["steps"]:
        raise SiteError("El sol no está sobre el horizonte en ese intervalo.")
    key = sitio.store.key
    m, d = solar.month_day(doy)
    caption = (f"{MONTHS[m - 1]} {d} · {solar.fmt_hour(h0)}→{solar.fmt_hour(h1)}"
               f" · Δt {step} min · {res['steps']} {tr('pasos')}")
    meta = {"period": caption, "source": tr("{hours} h de sol en el intervalo", hours=f"{res['hours']:.1f}"),
            "grid": round(grid, 2), "palette": settings.get("palette", "grises"),
            "show": settings.get("show", "shade"),
            "auto": False, "vmin": 0.0, "vmax": float(res["hours"]),
            "steps": res["steps"], "hours": res["hours"]}
    new = build_group(key, T, res, settings.get("mode", "cells"), meta,
                      kind="shadows")
    _place(sitio, "shadows", new, tr("Estudio de sombras listo."))
    a, sh = res["area"], res["cell"]
    summary = {"faces": T.n_faces, "cells": len(sh), "area": float(a.sum()),
               "steps": res["steps"], "hours": res["hours"], "grid": grid,
               "caption": caption,
               "full_sun": float(a[sh < 1e-9].sum()),
               "full_shade": float(a[sh >= res["hours"] - 1e-9].sum()),
               "mean": float((sh * a).sum() / max(a.sum(), 1e-9)),
               "seconds": time.monotonic() - t0}
    new.ext[key]["summary"] = summary
    sitio.changed.emit()
    return summary


def recolor(sitio, kind: str = "radiation", **changes) -> bool:
    g = find_group(sitio.scene, sitio.store.key, kind)
    if g is None:
        return False
    g.ext[sitio.store.key].update(changes)
    ok = paint(g, sitio.store.key)
    sitio.scene.version += 1
    notify = getattr(sitio.app.viewport, "notify_scene_changed", None)
    if callable(notify):
        notify()
    sitio.app.viewport.update()
    return ok


def show_map(sitio, kind: str | None) -> None:
    """Muestra un mapa (y oculta el otro), o ninguno con ``None``."""
    for k in KINDS:
        g = find_group(sitio.scene, sitio.store.key, k)
        if g is not None:
            g.hidden = (k != kind)
    sitio.store.touch()
    notify = getattr(sitio.app.viewport, "notify_scene_changed", None)
    if callable(notify):
        notify()
