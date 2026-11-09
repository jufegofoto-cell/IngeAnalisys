# SPDX-License-Identifier: GPL-3.0-or-later
"""Las operaciones del análisis de sitio, sin widgets.

Cada una que cambia el modelo es UN paso de deshacer. El panel, los
menús y el menú contextual llaman aquí; las pruebas también.
"""
from __future__ import annotations

import copy
import math
import uuid

from . import clima, geometria as geo
from .datos import PALETTE, WIND_COLORS, run_step


# ==============================================================================
# EXCEPTION MÉTIER AVEC TRADUCTION AUTOMATIQUE DES MESSAGES
# Permet de traduire immédiatement tout message d'erreur ou d'avertissement
# destiné à l'utilisateur vers sa langue active (Français par défaut).
# ==============================================================================
class SiteError(Exception):
    """Alerte contextuelle pour l'utilisateur, traduite automatiquement."""
    def __init__(self, message: str, *args, **kwargs) -> None:
        try:
            from .i18n import tr  # Contexte d'exécution plugin
        except ImportError:
            from i18n import tr   # Contexte autonome
        # Traduction de la chaîne avant transmission à la classe Exception de base
        super().__init__(tr(str(message)), *args, **kwargs)


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8]


def at_top_level(scene) -> bool:
    return getattr(scene, "edit_group", None) is None


# ---- Masas y lotes -------------------------------------------------------------

def masses(sitio) -> list:
    """Los grupos de nivel superior que son masas o lotes de esta extensión."""
    key = sitio.store.key
    return [g for g in sitio.scene.groups if geo.mass_params(g, key) is not None]


def _next_name(scene, kind: str) -> str:
    base = "Lote" if kind == "lot" else "Masa"
    used = {g.name for g in scene.groups}
    i = 1
    while f"{base} {i}" in used:
        i += 1
    return f"{base} {i}"


def sync_from_mesh(sitio, group) -> dict | None:
    """Los parámetros de la masa leídos de su geometría actual: si el
    usuario la movió, giró o empujó con las herramientas de IngeTrazo, el
    panel muestra (y reconstruye desde) lo que hay, no lo que hubo."""
    key = sitio.store.key
    p = geo.mass_params(group, key)
    if p is None:
        return None
    p = geo.normalize_mass(p)
    if group.xform is not None:
        return p
    mesh = group.mesh
    if not mesh.faces:
        return p
    zs = [v.position.z() for v in mesh.vertices]
    if not zs:
        return p
    zmin, zmax = min(zs), max(zs)
    want = -1.0 if p["kind"] == "mass" and zmax - zmin > 1e-4 else None
    best, best_a = None, 0.0
    for f in mesh.faces:
        n = f.normal()
        if abs(n.z()) < 0.98:
            continue
        if want is not None and n.z() > 0:
            continue
        fz = sum(v.position.z() for v in f.loop) / len(f.loop)
        if abs(fz - zmin) > 1e-3:
            continue
        pts = [(v.position.x(), v.position.y()) for v in f.loop]
        a = geo.area(pts)
        if a > best_a:
            best, best_a = pts, a
    if best is None:
        return p
    p["pts"] = geo.ccw(geo.clean(best))
    p["base"] = zmin
    if p["kind"] == "mass":
        p["h"] = zmax - zmin
    return p


def create_masses_from_faces(sitio, kind: str = "mass",
                             height: float | None = None,
                             consume: bool = True) -> list:
    """Una masa (o lote) por cada cara horizontal seleccionada del modelo.
    La cara pasa a ser la base de la masa (se quita del modelo suelto)."""
    scene = sitio.scene
    if not at_top_level(scene):
        raise SiteError("Cierra el grupo que estás editando y vuelve a intentarlo.")
    from core.mesh import Face
    faces = [e for e in scene.selection
             if isinstance(e, Face) and e in scene.mesh.faces]
    plans = []
    for f in faces:
        fp = geo.footprint_of_face(f)
        if fp is not None and len(fp[0]) >= 3:
            plans.append((f, fp))
    if not plans:
        raise SiteError("Selecciona una o más caras horizontales (dibújalas con "
                        "Rectángulo o Línea) y vuelve a intentarlo.")
    view = sitio.store.view()
    h = float(view["model"]["def_h"] if height is None else height)
    key = sitio.store.key
    made = []

    def mutate(sc):
        layer = geo.ensure_layer(sc, geo.LOT_LAYER if kind == "lot"
                                 else geo.MASS_LAYER)
        n0 = len(masses(sitio))
        for i, (face, (pts, z)) in enumerate(plans):
            color = "#ef4444" if kind == "lot" else PALETTE[(n0 + i) % len(PALETTE)]
            params = {"kind": kind, "pts": pts, "h": 0.0 if kind == "lot" else h,
                      "base": z + (0.02 if kind == "lot" else 0.0),
                      "color": color, "opacity": 0.9 if kind == "lot" else 0.95,
                      "floor_h": view["model"]["floor_h"]}
            g = geo.build_mass(key, params, _next_name(sc, kind))
            g.layer = layer
            sc.groups.append(g)
            made.append(g)
        if consume:
            geo.remove_faces_with_edges(sc.mesh, [f for f, _fp in plans])
        sc.selection.clear()
        sc.selection.update(made)

    label = "lote(s)" if kind == "lot" else "masa(s)"
    if not run_step(sitio.app.viewport, mutate,
                    message=f"{len(plans)} {label} creada(s)."):
        return []
    sitio.changed.emit()
    return made


def create_mass(sitio, pts, kind="mass", h=None, base=0.0, name=None,
                color=None, opacity=None):
    """Una masa desde una planta dada (importación, pruebas, guiones)."""
    view = sitio.store.view()
    key = sitio.store.key
    out = []

    def mutate(sc):
        layer = geo.ensure_layer(sc, geo.LOT_LAYER if kind == "lot"
                                 else geo.MASS_LAYER)
        n0 = len(masses(sitio))
        params = {"kind": kind, "pts": pts,
                  "h": 0.0 if kind == "lot" else float(
                      view["model"]["def_h"] if h is None else h),
                  "base": float(base),
                  "color": color or ("#ef4444" if kind == "lot"
                                     else PALETTE[n0 % len(PALETTE)]),
                  "opacity": opacity if opacity is not None else (
                      0.9 if kind == "lot" else 0.95),
                  "floor_h": view["model"]["floor_h"]}
        g = geo.build_mass(key, params, name or _next_name(sc, kind))
        g.layer = layer
        sc.groups.append(g)
        out.append(g)

    run_step(sitio.app.viewport, mutate)
    sitio.changed.emit()
    return out[0] if out else None


def update_mass(sitio, group, changes: dict, name: str | None = None):
    """Reconstruye la masa con ``changes`` (planta, altura, base, color…)
    en su sitio, como UN paso. Devuelve el grupo nuevo."""
    key = sitio.store.key
    p = sync_from_mesh(sitio, group)
    if p is None:
        return None
    p.update(changes)
    if p.get("kind") == "lot":
        p["h"] = 0.0
    out = []

    def mutate(sc):
        new = geo.build_mass(key, p, name or group.name)
        new.layer = group.layer if p["kind"] == group_kind(sitio, group) else (
            geo.ensure_layer(sc, geo.LOT_LAYER if p["kind"] == "lot"
                             else geo.MASS_LAYER))
        new.hidden = group.hidden
        new.ifc = copy.deepcopy(group.ifc)
        idx = sc.groups.index(group)
        sc.groups[idx] = new
        was = group in sc.selection
        sc.selection.discard(group)
        if was:
            sc.selection.add(new)
        out.append(new)

    run_step(sitio.app.viewport, mutate)
    sitio.changed.emit()
    return out[0] if out else None


def group_kind(sitio, group) -> str:
    p = geo.mass_params(group, sitio.store.key) or {}
    return "lot" if p.get("kind") == "lot" else "mass"


def rotate_mass(sitio, group, deg: float):
    p = sync_from_mesh(sitio, group)
    if p is None:
        return None
    return update_mass(sitio, group, {"pts": geo.rotate_pts(p["pts"], deg)})


def duplicate_mass(sitio, group, dx: float = 5.0, dy: float = -5.0):
    p = sync_from_mesh(sitio, group)
    if p is None:
        return None
    pts = [[x + dx, y + dy] for x, y in p["pts"]]
    return create_mass(sitio, pts, p["kind"], p["h"], p["base"],
                       name=None, color=p["color"], opacity=p["opacity"])


def delete_mass(sitio, group) -> bool:
    def mutate(sc):
        if group in sc.groups:
            sc.groups.remove(group)
        sc.selection.discard(group)

    ok = run_step(sitio.app.viewport, mutate, message=f"«{group.name}» eliminada.")
    sitio.changed.emit()
    return ok


def summary(sitio) -> dict:
    """Totales: huella, área construida (pisos × planta), volumen, lotes y
    los índices de ocupación y construcción cuando hay lotes."""
    tot = {"masses": 0, "lots": 0, "footprint": 0.0, "gfa": 0.0,
           "volume": 0.0, "lot_area": 0.0}
    for g in masses(sitio):
        p = sync_from_mesh(sitio, g)
        st = geo.mass_stats(p)
        if p["kind"] == "lot":
            tot["lots"] += 1
            tot["lot_area"] += st["area"]
        else:
            tot["masses"] += 1
            tot["footprint"] += st["area"]
            tot["gfa"] += st["gfa"]
            tot["volume"] += st["volume"]
    if tot["lot_area"] > 0:
        tot["ios"] = tot["footprint"] / tot["lot_area"]
        tot["ica"] = tot["gfa"] / tot["lot_area"]
    return tot


# ---- Contexto OSM ----------------------------------------------------------------

def context_group(sitio):
    return geo.find_group(sitio.scene, sitio.store.context().get("group"))


def set_context(sitio, buildings: list | None, message: str | None = None):
    """Escribe los edificios y (re)hace su grupo — un paso. ``None`` lo
    quita todo."""
    st = sitio.store
    view = st.view()
    ck = st.context_key()
    old = context_group(sitio)
    terrain = getattr(sitio.scene, "terrain", None) if view["ctx"].get(
        "terrain", True) else None

    def mutate(sc):
        if old is not None and old in sc.groups:
            sc.groups.remove(old)
            sc.selection.discard(old)
        if not buildings:
            sc.plugin_data.pop(ck, None)
            return
        for b in buildings:
            z = 0.0
            if terrain is not None:
                lows = [terrain.height_at(p[0], p[1]) for p in b["pts"]]
                lows = [v for v in lows if v is not None]
                z = min(lows) if lows else 0.0
            b["z0"] = round(z, 3)
        g = geo.build_context(buildings, float(view["ctx"]["hscale"]),
                              terrain=terrain)
        g.layer = geo.ensure_layer(sc, geo.CONTEXT_LAYER,
                                   color=geo.hex_rgb(view["ctx"]["color"]))
        g.material = {"color": geo.hex_rgb(view["ctx"]["color"]),
                      "opacity": float(view["ctx"]["opacity"])}
        g.locked = True
        if old is not None:
            g.hidden = old.hidden
        sc.groups.append(g)
        sc.plugin_data[ck] = {"buildings": buildings, "group": g.uid}

    run_step(sitio.app.viewport, mutate, keys=(ck,), message=message)
    sitio.changed.emit()


def context_from_osm(sitio, payload: dict) -> int:
    from . import osm
    d = sitio.datum()
    if d is None:
        raise SiteError("Ubica el proyecto antes de cargar edificios.")
    view = sitio.store.view()

    def to_local(lat, lon):
        p = d.geodetic_to_local(lat, lon)
        return p.x(), p.y()

    bl = osm.parse(payload, to_local, float(view["ctx"]["def_h"]))
    if not bl:
        raise SiteError("OpenStreetMap no tiene edificios en ese radio.")
    set_context(sitio, bl, message=f"{len(bl)} edificios de contexto cargados.")
    return len(bl)


def update_building(sitio, bid: str, **changes) -> None:
    ctx = copy.deepcopy(sitio.store.context())
    for b in ctx.get("buildings", []):
        if b.get("id") == bid:
            b.update(changes)
            break
    else:
        return
    set_context(sitio, ctx["buildings"])


def restyle_context(sitio) -> None:
    """Color y opacidad del contexto: sin rehacer la geometría."""
    g = context_group(sitio)
    if g is None:
        return
    v = sitio.store.view()["ctx"]
    g.material = {"color": geo.hex_rgb(v["color"]), "opacity": float(v["opacity"])}
    for ly in sitio.scene.layers:
        if ly.name == geo.CONTEXT_LAYER:
            ly.color = geo.hex_rgb(v["color"])
    sitio.store.touch()


def pick_building(sitio, P, px: float, py: float):
    """El edificio bajo el píxel (el más cercano a la cámara)."""
    ctx = sitio.store.context()
    hs = float(sitio.store.view()["ctx"]["hscale"])
    eye = P.eye
    best, best_d = None, math.inf
    for b in ctx.get("buildings", []):
        if b.get("hidden"):
            continue
        z = float(b.get("z0", 0.0)) + float(b.get("h", 9)) * hs
        pts = [(p[0], p[1], z) for p in b["pts"]]
        xy, ok = P(pts)
        if not ok.all():
            continue
        if not geo.point_in_polygon(px, py, xy.tolist()):
            continue
        cx, cy = geo.centroid(b["pts"])
        d = math.dist((eye.x(), eye.y(), eye.z()), (cx, cy, z))
        if d < best_d:
            best, best_d = b["id"], d
    return best


# ---- Anotaciones -----------------------------------------------------------------

def add_pin(sitio, point, label: str | None = None, icon: str | None = None):
    st = sitio.store
    c = st.content()
    n = len(c["pins"]) + 1
    pin = {"id": _new_id("p"), "pos": [point.x(), point.y(), point.z()],
           "label": label if label is not None else f"Punto {n}",
           "icon": icon or None}
    c["pins"].append(pin)
    st.set_content(c)
    sitio.selected_pin = pin["id"]
    sitio.changed.emit()
    return pin["id"]


def update_pin(sitio, pid: str, **changes) -> None:
    st = sitio.store
    c = st.content()
    for p in c["pins"]:
        if p.get("id") == pid:
            p.update(changes)
            break
    else:
        return
    st.set_content(c)
    sitio.changed.emit()


def delete_pin(sitio, pid: str) -> None:
    st = sitio.store
    c = st.content()
    c["pins"] = [p for p in c["pins"] if p.get("id") != pid]
    st.set_content(c)
    if sitio.selected_pin == pid:
        sitio.selected_pin = None
    sitio.changed.emit()


def style_all_pins(sitio) -> None:
    """Quita el estilo propio de cada anotación: todas toman el actual."""
    st = sitio.store
    c = st.content()
    keys = set(st.view()["annot"])
    for p in c["pins"]:
        for k in keys:
            p.pop(k, None)
    st.set_content(c)
    sitio.changed.emit()


def pick_pin(sitio, P, px, py):
    view = sitio.store.view()
    from .dibujo import pin_style
    best, best_d = None, math.inf
    for p in sitio.store.content()["pins"]:
        try:
            x, y, z = (float(v) for v in p["pos"])
        except (KeyError, TypeError, ValueError):
            continue
        s = pin_style(p, view["annot"])
        top = (x, y, z + s["h"])
        q = P.one(top)
        if q is None:
            continue
        r = max(7.0, min(26.0, P.pixel_size(top, s["size"] / 2)))
        d = math.hypot(q.x() - px, q.y() - py)
        if d <= r + 4 and d < best_d:
            best, best_d = p["id"], d
    return best


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


# ---- Mapa base -------------------------------------------------------------------

def auto_zoom(lat: float, extent: float) -> int:
    for z in range(19, 11, -1):
        mpp = 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)
        if 2 * extent / mpp <= 2600:
            return z
    return 12


def load_basemap(sitio, provider: str, extent: float, zoom: int = 0,
                 custom: str = "") -> int:
    from georef.tiles import PRESETS, TileLayer, custom_source
    d = sitio.datum()
    if d is None:
        raise SiteError("Ubica el proyecto antes de descargar el mapa base.")
    if provider == "custom":
        if "{z}" not in custom or "{x}" not in custom or "{y}" not in custom:
            raise SiteError("La URL de teselas debe llevar {z}, {x} y {y}.")
        src = custom_source(custom, name="Personalizado")
    else:
        src = PRESETS.get(provider) or next(iter(PRESETS.values()))
    z = int(zoom) if zoom else auto_zoom(d.lat, extent)
    z = min(z, getattr(src, "max_zoom", 19))
    layer = TileLayer(src, zoom=z)
    layer.set_rectangle(float(extent), float(extent))
    layer.cap_detail(d, max_tiles=500)
    layer.visible = True
    sitio.scene.tile_layer = layer
    vp = sitio.app.viewport
    reset = getattr(vp, "reset_tiles", None)
    if callable(reset):
        reset()
    sitio.scene.version += 1
    vp.update()
    return layer.zoom
