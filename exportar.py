# SPDX-License-Identifier: GPL-3.0-or-later
# ==============================================================================
# MODULE D'EXPORTATION D'IMAGES ET GRAPHIQUES CLIMATIQUES
# Rôle du script :
#   Ce fichier gère la génération et l'enregistrement sur disque des captures
#   haute résolution de la vue 3D avec les calques d'analyse superposés (HUD,
#   diagramme solaire, rose des vents), ainsi que des graphiques météorologiques
#   annuels au format PNG.
# ==============================================================================
"""Salidas: imagen de la vista con el análisis y gráficos del clima."""
from __future__ import annotations

# Import du moteur de rendu graphique 2D de Qt
from PySide6.QtGui import QPainter

from . import clima  # Fonctions météo pour les couleurs de la rose des vents
from .acciones import SiteError  # Exception spécifique pour les messages utilisateur

# Import tolérant de la fonction de traduction i18n
try:
    from .i18n import tr
except ImportError:
    from i18n import tr


def render_png(sitio, overlay, width: int):
    """
    Rend la vue 3D courante sous forme d'une image mémoire QImage à la largeur spécifiée,
    en y dessinant en surimpression l'ensemble des calques d'analyse de site.
    """
    vp = sitio.app.viewport  # Récupération du viewport 3D actif d'IngeTrazo
    img = vp.render_image(int(width), sharp_shadows=True)  # Calcul du rendu avec ombres nettes
    if img is None:
        raise SiteError(tr("La vista no está lista para exportar."))  # Message traduit si échec

    p = QPainter(img)  # Initialisation du pinceau de dessin sur l'image
    # Mise à l'échelle pour adapter la résolution demandée aux proportions de la vue
    p.scale(img.width() / max(1, vp.width()),
            img.height() / max(1, vp.height()))
    overlay.draw(vp, p, export=True)  # Dessin des calques solaires, vents et annotations
    p.end()  # Finalisation du dessin
    return img


def export_png(sitio, overlay, path: str, width: int = 1920) -> str:
    """
    Exporte la vue 3D actuelle sous forme de fichier image PNG haute résolution.
    """
    img = render_png(sitio, overlay, width)  # Génération du rendu en mémoire
    if not img.save(path):  # Enregistrement effectif dans le fichier
        raise SiteError(tr("No se pudo guardar {path}.", path=path))  # Alerte si erreur d'écriture
    return path


def export_charts(sitio, base_path: str) -> list[str]:
    """
    Exporte les graphiques climatiques (rose des vents et températures mensuelles) en PNG.
    """
    from .graficos import chart_image  # Import différé du générateur de graphiques
    stats = sitio.stats()  # Récupération des données statistiques météo
    if not stats:
        raise SiteError(tr("Carga primero un archivo EPW."))  # Exige un fichier météo préalable

    w = sitio.store.view()["wind"]  # Paramètres visuels du vent
    cols = clima.rose_colors(w.get("pal", "amber"), w.get("c0"), w.get("c1"))
    # Nettoyage de l'extension du chemin de base
    stem = base_path[:-4] if base_path.lower().endswith(".png") else base_path
    out = []

    # Génération et sauvegarde successive des deux graphiques
    for kind, suffix in (("rose", "_rosa.png"), ("months", "_mensual.png")):
        path = stem + suffix  # Chemin complet du fichier cible
        if not chart_image(kind, stats, cols).save(path):
            raise SiteError(tr("No se pudo guardar {path}.", path=path))
        out.append(path)

    return out
