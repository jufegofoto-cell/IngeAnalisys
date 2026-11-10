# SPDX-License-Identifier: GPL-3.0-or-later
# ==============================================================================
# OUTILS D'INTERACTION DIRECTE ET INFÉRENCES GÉOMÉTRIQUES
# Rôle du script :
#   Ce fichier fournit les outils interactifs en un clic (placement d'une
#   annotation 3D, recentrage de l'héliodon) avec accrochage géométrique (snap)
#   et affichage des messages d'aide dans la barre d'état.
# ==============================================================================
"""Herramientas de un clic (con inferencias): ubicar una anotación, el centro…"""
from __future__ import annotations

# Imports Qt pour les touches clavier et vecteurs 3D
from PySide6.QtCore import Qt
from PySide6.QtGui import QVector3D

# Import tolérant de la fonction de traduction
try:
    from .i18n import tr
except ImportError:
    from i18n import tr


def make_pick_tool(prompt: str, done, cancelled, repeat: bool = False):
    """
    Crée dynamiquement un outil de pointage 3D temporaire s'intégrant au système d'outils d'IngeTrazo.
    """
    from tools.base import Tool  # Classe de base des outils interactifs d'IngeTrazo

    class _PickPoint(Tool):
        name = prompt  # Nom affiché de l'outil
        shortcut = None  # Pas de raccourci clavier attribué
        uses_snap = True  # Active l'accrochage sur les sommets, arêtes et faces

        def __init__(self) -> None:
            self.hover_point = None  # Point survolé par le curseur

        def on_activate(self, viewport) -> None:
            self.hover_point = None  # Réinitialisation à l'activation

        def on_deactivate(self, viewport) -> None:
            self.hover_point = None  # Réinitialisation à la désactivation

        def on_hover(self, ctx) -> None:
            self.hover_point = ctx.world  # Mémorise la coordonnée 3D survolée
            ctx.viewport.update()  # Demande un rafraîchissement visuel

        def on_click(self, ctx) -> None:
            done(QVector3D(ctx.world), ctx)  # Exécute l'action avec le point 3D cliqué
            if not repeat:
                cancelled()  # Désactive l'outil si le mode répétition n'est pas activé

        def on_key(self, viewport, key, modifiers) -> bool:
            if key == Qt.Key_Escape:
                cancelled()  # Touche Échap : annule l'opération en cours
                return True
            return False

        def rubber_band_lines(self):
            return []  # Aucune ligne élastique à tracer

    return _PickPoint()


def begin(app, tool, prompt: str) -> None:
    """
    Active l'outil interactif et affiche l'instruction dans la barre d'état d'IngeTrazo.
    """
    vp = app.viewport  # Référence vers la vue 3D
    vp.set_active_tool(tool)  # Activation de l'outil
    try:
        # Message dans la barre d'état avec indication de la touche d'annulation
        message = f"{tr(prompt)} — {tr('Esc cancela')}"
        app.window.statusBar().showMessage(message)
    except Exception:
        pass
    vp.setFocus()  # Donne le focus au viewport pour intercepter les clics


def end(app) -> None:
    """
    Termine l'outil interactif, efface la barre d'état et réactive l'outil de sélection par défaut.
    """
    win = app.window
    try:
        win.statusBar().clearMessage()  # Effacement du message d'état
    except Exception:
        pass
    activate = getattr(win, "_activate_tool", None)
    if callable(activate):
        activate("select")  # Retour à l'outil flèche de sélection standard
