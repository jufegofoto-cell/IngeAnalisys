# SPDX-License-Identifier: GPL-3.0-or-later
# ==============================================================================
# POINT D'ENTRÉE DE L'EXTENSION INGEANALISYS (ANALYSE DE SITE)
# Rôle du script :
#   Ce fichier initialise l'extension au démarrage d'IngeTrazo. Il enregistre
#   le panneau latéral, configure les calques d'affichage 3D sur le viewport,
#   ajoute les commandes dans la barre de menus et le menu contextuel (clic droit),
#   et déclare les métadonnées pour l'inspecteur d'objets (Outliner).
# Internationalisation :
#   Utilise le module i18n pour afficher tous les menus et libellés en français
#   (ou selon la langue configurée dans IngeTrazo).
# ==============================================================================
"""Análisis de Sitio — una EXTENSIÓN de IngeTrazo."""
from __future__ import annotations

import logging  # Module standard de journalisation

# Import du module de traduction interne à l'extension
from .i18n import tr

__version__ = "1.8.0"  # Version actuelle de l'extension
log = logging.getLogger("ingetrazo.plugins.analisis_sitio")  # Logger spécifique à l'extension


def setup(app) -> None:
    """
    Fonction principale appelée automatiquement par IngeTrazo lors du chargement de l'extension.
    Elle configure l'ensemble des interactions avec l'interface graphique et la scène 3D.
    """
    # Vérification de la compatibilité de l'API d'extensions (version 1 ou 2 requise)
    if getattr(app, "api_version", 1) not in (1, 2):
        log.warning("Análisis de Sitio attend l'API d'extensions version 2")
        return

    # Importations Qt et modules internes différées pour un démarrage rapide
    from PySide6.QtCore import QTimer

    from . import acciones as act  # Module des actions modifiant le document
    from .dibujo import Overlay, Projector  # Moteur de rendu graphique 3D en surimpression
    from .nucleo import Sitio  # Cœur de gestion des données du site
    from .panel import SitePanel  # Panneau d'interface utilisateur latéral

    # Instanciation du modèle de données, du moteur de dessin et de l'interface
    sitio = Sitio(app)  # Modèle central contenant les paramètres solaires et météo
    overlay = Overlay(sitio)  # Gestionnaire du tracé 3D (héliodon, rose des vents, etc.)
    panel = SitePanel(sitio, overlay)  # Panneau de commandes avec les 12 sections
    dock = app.add_panel(tr("Análisis de sitio"), panel)  # Intégration du panneau dans le dock latéral

    # Enregistrement du calque de dessin sur la vue 3D
    app.add_overlay(lambda vp, painter: overlay.draw(vp, painter))

    # ---- Gestion de la sélection et suppression dans la vue 3D ----------------
    def pick(viewport, px, py):
        """Détecte si un clic souris dans la vue 3D touche une annotation ou un bâtiment OSM."""
        P = Projector(viewport)  # Outil de projection 3D vers 2D
        pid = act.pick_pin(sitio, P, px, py)  # Vérifie si une épingle d'annotation est cliquée
        if pid is not None:
            return ("pin", pid)
        if sitio.edit_context:
            bid = act.pick_building(sitio, P, px, py)  # Vérifie si un bâtiment OSM est cliqué
            if bid is not None:
                return ("bld", bid)
        return None

    def on_select(item):
        """Réagit à la sélection d'un objet en mettant à jour le panneau latéral."""
        if item is None:
            sitio.selected_pin = None
            panel.schedule_refresh()
        elif item[0] == "pin":
            panel.pin_selected(item[1])  # Active la modification de l'annotation
        else:
            panel.building_selected(item[1])  # Active les informations du bâtiment
        app.viewport.update()  # Rafraîchit l'affichage 3D

    def delete(item):
        """Supprime l'élément sélectionné lorsque l'utilisateur appuie sur la touche Suppr."""
        if item[0] == "pin":
            act.delete_pin(sitio, item[1])  # Supprime l'annotation
        else:
            act.update_building(sitio, item[1], hidden=True)  # Masque le bâtiment
            sitio.selected_building = None

    # Enregistrement de la sélection interactive auprès d'IngeTrazo
    app.add_pickable(pick, on_select=on_select, delete=delete)

    # ---- Détection des modifications du document (Annuler/Rétablir, ouverture) ----
    def changed():
        """Met à jour l'animation et rafraîchit le panneau quand la scène 3D change."""
        sitio.update_animation()
        panel.schedule_refresh()

    app.on_document_changed(changed)
    if hasattr(app, "on_document_closed"):
        app.on_document_closed(lambda _scene: None)

    # ---- Menu supérieur : Extensiones ▸ Analyse de site -------------------------
    def show():
        """Affiche et met au premier plan le panneau de l'extension."""
        app.show_panel(dock)

    def later(fn):
        """Exécute une fonction lors du prochain cycle de la boucle d'événements Qt."""
        return lambda: QTimer.singleShot(0, fn)

    # Création du sous-menu dédié traduit
    sub = app.add_menu(tr("Análisis de sitio"))
    if sub is not None:
        # Liste des entrées de menu avec leurs traductions dynamiques
        entries = (
            (tr("Mostrar panel"), show),
            (None, None),  # Séparateur visuel
            (tr("Abrir clima (EPW)…"), lambda: (show(), panel._on_open_epw())),
            (tr("Elegir ubicación en el mapa…"), lambda: (show(), panel._on_pick_map())),
            (tr("Descargar mapa base"), panel._on_basemap),
            (tr("Cargar edificios de OpenStreetMap"), panel._on_osm),
            (None, None),  # Séparateur visuel
            (tr("Masa desde las caras seleccionadas"),
             lambda: panel._make_masses("mass")),
            (tr("Lote desde las caras seleccionadas"),
             lambda: panel._make_masses("lot")),
            (tr("Nueva anotación (clic en la vista)"),
             lambda: panel._place_pin(False)),
            (None, None),  # Séparateur visuel
            (tr("Vista desde el sol"), lambda: panel.set_view("sun")),
            (tr("Animar recorrido solar"), panel._on_anim),
            (tr("Mostrar / ocultar el análisis"), _toggle(sitio, panel)),
            (None, None),  # Séparateur visuel
            (tr("Exportar imagen (PNG)…"), panel._on_png),
        )
        for text, fn in entries:
            if text is None:
                sub.addSeparator()  # Ajoute une ligne de séparation
            else:
                a = sub.addAction(text)  # Ajoute l'action avec son texte traduit
                a.triggered.connect(lambda _c=False, f=fn: f())
    else:
        # Repli si la fenêtre ne supporte pas de sous-menu complet
        app.add_menu_action(tr("Análisis de sitio…"), show)

    # ---- Menu contextuel (clic droit sur une face ou un groupe) -----------------
    def context_menu(menu, selection):
        """Ajoute des options spécifiques à l'analyse de site dans le menu contextuel."""
        from core.mesh import Face  # Type de face géométrique IngeTrazo
        scene = app.scene
        groups = [g for g in selection if g in act.masses(sitio)]
        faces = [e for e in selection if isinstance(e, Face) and e in scene.mesh.faces]

        if groups:
            g = groups[0]
            # Option pour modifier directement une masse sélectionnée
            menu.addAction(tr("Editar masa en Análisis de sitio")).triggered.connect(
                lambda: QTimer.singleShot(0, lambda: (show(), panel.refresh(), panel.select_mass(g))))
        if faces:
            # Options pour convertir les faces sélectionnées en masse ou en parcelle
            menu.addAction(tr("Convertir en masa")).triggered.connect(
                later(lambda: panel._make_masses("mass")))
            menu.addAction(tr("Convertir en lote")).triggered.connect(
                later(lambda: panel._make_masses("lot")))

    app.add_context_menu(context_menu)

    # ---- Déclarations pour l'inspecteur d'objets (Outliner) ----------------------
    key = app.key

    def outliner_kind(group):
        """Fournit les métadonnées (surface, hauteur, étages) affichées dans l'arborescence."""
        from . import geometria as geo
        p = geo.mass_params(group, key)
        if p is None:
            return None
        p = geo.normalize_mass(p)
        st = geo.mass_stats(p)
        if p["kind"] == "lot":
            return {"type": tr("Lote"), "props": [(tr("Área"), f"{st['area']:,.1f} m²")]}
        return {"type": tr("Masa"), "props": [
            (tr("Altura"), f"{p['h']:.2f} m"),
            (tr("Pisos"), str(st["floors"])),
            (tr("Área"), f"{st['area']:,.1f} m²"),
            (tr("Área construida"), f"{st['gfa']:,.0f} m²")]}

    app.add_outliner_kind(outliner_kind)
    sitio.update_animation()

    # Rétrocompatibilité et accès via console Python
    app.window.__dict__.setdefault("_ext_instances", {})[key] = {
        "sitio": sitio, "panel": panel, "overlay": overlay, "dock": dock}


def _toggle(sitio, panel):
    """Bascule l'affichage global des calques d'analyse (activé / désactivé)."""
    def fn():
        v = sitio.store.view()
        v["on"] = not v.get("on", True)  # Inverse l'état d'affichage
        sitio.store.touch()  # Marque le document comme modifié
        sitio.update_animation()  # Relance ou stoppe l'animation du vent/soleil
        panel.refresh()  # Met à jour les cases à cocher du panneau
    return fn
