# SPDX-License-Identifier: GPL-3.0-or-later
# ==============================================================================
# MODULE DE LOCALISATION ET TRADUCTION MULTILINGUE (i18n)
# Projet : Extension IngeAnalisys (Analyse de Site) pour IngeTrazo
# Rôle du script :
#   Ce fichier fournit le mécanisme central de traduction pour l'extension.
#   Il détecte la langue choisie par l'utilisateur dans le menu déroulant
#   (ou à défaut la langue d'IngeTrazo), charge les dictionnaires JSON
#   (fr.json, en.json, es.json, pt.json) et traduit toute l'interface à la volée.
# ==============================================================================

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

# Enregistreur de logs dédié
journal = logging.getLogger("ingetrazo.plugins.analisis_sitio.i18n")

# Répertoire contenant les fichiers de traduction (.json)
DOSSIER_LOCALES = Path(__file__).parent / "locales"

# Langues officiellement supportées par la liste déroulante
LANGUES_SUPPORTEES: tuple[str, ...] = ("fr", "es", "en", "pt")

# Cache en mémoire des dictionnaires chargés
_catalogue_en_cache: dict[str, dict[str, str]] = {}

# Langue sélectionnée dans la session courante
_langue_forcee: str | None = None


def definir_langue_active(code_langue: str | None) -> None:
    """
    Définit manuellement la langue active pour l'extension ('fr', 'es', 'en', 'pt').
    Sauvegarde également ce choix dans QSettings pour qu'il soit mémorisé.
    """
    global _langue_forcee
    if code_langue and code_langue.lower() in LANGUES_SUPPORTEES:
        _langue_forcee = code_langue.lower()
        try:
            from PySide6.QtCore import QSettings
            QSettings().setValue("analisis_sitio/language", _langue_forcee)
        except Exception:
            pass
    else:
        _langue_forcee = None


def obtenir_langue_courante() -> str:
    """
    Détermine la langue active selon un ordre de priorité strict :
    1. Langue choisie par l'utilisateur dans la session courante (_langue_forcee).
    2. Langue mémorisée dans les préférences de l'extension (QSettings).
    3. Langue de l'interface IngeTrazo (core.i18n.current_language).
    4. Français ('fr') par défaut.
    """
    # 1. Choix explicite en cours de session
    if _langue_forcee in LANGUES_SUPPORTEES:
        return _langue_forcee

    # 2. Choix mémorisé dans QSettings via la liste déroulante du panneau
    try:
        from PySide6.QtCore import QSettings
        choix = QSettings().value("analisis_sitio/language", "", type=str)
        if choix and choix.lower() in LANGUES_SUPPORTEES:
            return choix.lower()
    except Exception:
        pass

    # 3. Langue globale d'IngeTrazo
    try:
        from core.i18n import current_language
        langue_ingetrazo = current_language()
        if langue_ingetrazo:
            code = langue_ingetrazo.replace("_", "-").split("-")[0].lower()
            if code in LANGUES_SUPPORTEES:
                return code
    except ImportError:
        pass

    # 4. Français par défaut
    return "fr"


def charger_catalogue(code_langue: str) -> dict[str, str]:
    """
    Charge en mémoire le fichier JSON correspondant à la langue demandée.
    """
    if code_langue in _catalogue_en_cache:
        return _catalogue_en_cache[code_langue]

    fichier_json = DOSSIER_LOCALES / f"{code_langue}.json"
    traductions: dict[str, str] = {}

    if fichier_json.is_file():
        try:
            texte_brut = fichier_json.read_text(encoding="utf-8")
            donnees = json.loads(texte_brut)
            if isinstance(donnees, dict):
                traductions = {str(cle): str(val) for cle, val in donnees.items()}
        except Exception as erreur:
            journal.warning("Impossible de lire le catalogue %s : %s", fichier_json, erreur)

    _catalogue_en_cache[code_langue] = traductions
    return traductions


def t(texte_source: str, **arguments: Any) -> str:
    """
    Traduit une chaîne source dans la langue courante.
    Si la langue courante est 'es' (espagnol d'origine), renvoie le texte source
    (sauf si une clé spécifique de formatage existe).
    """
    if not texte_source:
        return ""
    langue = obtenir_langue_courante()
    if langue == "es":
        texte_traduit = texte_source
    else:
        catalogue = charger_catalogue(langue)
        texte_traduit = catalogue.get(texte_source, texte_source)

    if arguments:
        try:
            return texte_traduit.format(**arguments)
        except Exception:
            return texte_traduit

    return texte_traduit


# Alias standard
tr = t
