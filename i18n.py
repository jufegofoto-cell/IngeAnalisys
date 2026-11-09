# SPDX-License-Identifier: GPL-3.0-or-later
# ==============================================================================
# MODULE DE LOCALISATION ET TRADUCTION MULTILINGUE (i18n)
# Projet : Extension IngeAnalisys (Analyse de Site) pour IngeTrazo
# Rôle du script :
#   Ce fichier fournit le mécanisme central de traduction pour l'extension.
#   Il détecte automatiquement la langue utilisée par IngeTrazo (ou par le
#   système Windows), charge les catalogues de traduction au format JSON,
#   et remplace à la volée les textes de l'interface par leur équivalent traduit.
# Interactions :
#   - Appelé par tous les fichiers de l'interface (panel.py, dibujo.py, etc.)
#   - Lit les dictionnaires stockés dans le sous-dossier 'locales/' (fr.json, etc.)
#   - S'interface avec l'API interne d'IngeTrazo (core.i18n) s'il est présent.
# ==============================================================================

from __future__ import annotations  # Permet l'évaluation différée des annotations de type

import json  # Bibliothèque standard pour lire et parser les fichiers de traduction JSON
import logging  # Pour journaliser les avertissements en cas de clé ou fichier manquant
from pathlib import Path  # Manipulation propre des chemins de fichiers multiplateforme
from typing import Any  # Typage générique pour les paramètres optionnels

# Création d'un enregistreur de logs dédié à ce module
journal = logging.getLogger("ingetrazo.plugins.analisis_sitio.i18n")

# Répertoire contenant les fichiers de traduction (.json)
DOSSIER_LOCALES = Path(__file__).parent / "locales"

# Langues officiellement supportées par l'extension
LANGUES_SUPPORTEES: tuple[str, ...] = ("fr", "es", "en", "pt")

# Cache en mémoire des dictionnaires de langue déjà chargés (évite de relire le disque à chaque mot)
_catalogue_en_cache: dict[str, dict[str, str]] = {}

# Variable globale pour forcer manuellement une langue si l'utilisateur le choisit
_langue_forcee: str | None = None


def definir_langue_active(code_langue: str | None) -> None:
    """
    Définit manuellement la langue active pour l'extension (ex: 'fr', 'en', 'es').
    Si 'code_langue' vaut None ou 'auto', la détection automatique reprend le relais.
    """
    global _langue_forcee  # Accès à la variable globale du module
    if code_langue and code_langue.lower() in LANGUES_SUPPORTEES:
        _langue_forcee = code_langue.lower()  # On enregistre la langue choisie
    else:
        _langue_forcee = None  # Retour au mode automatique


def obtenir_langue_courante() -> str:
    """
    Détermine la langue à afficher en suivant un ordre de priorité strict :
    1. Choix manuel forcé par l'utilisateur.
    2. Langue active du logiciel IngeTrazo (via son module core.i18n).
    3. Langue du système d'exploitation Windows (via PySide6 QLocale).
    4. Langue par défaut de secours ('fr' si possible, sinon 'es').
    """
    # 1. Si une langue a été explicitement choisie par l'utilisateur
    if _langue_forcee:
        return _langue_forcee

    # 2. Tentative de lecture de la langue actuelle dans IngeTrazo
    try:
        from core.i18n import current_language  # Fonction native d'IngeTrazo
        langue_ingetrazo = current_language()  # Récupère par exemple 'fr', 'es', 'en'
        if langue_ingetrazo:
            code = langue_ingetrazo.replace("_", "-").split("-")[0].lower()
            if code in LANGUES_SUPPORTEES:
                return code  # On adopte la langue configurée dans IngeTrazo
    except ImportError:
        pass  # En dehors d'IngeTrazo (ex: tests indépendants), on continue

    # 3. Détection via la configuration persistante de l'utilisateur (QSettings)
    try:
        from PySide6.QtCore import QSettings  # Module Qt pour lire la base de registre / settings
        config = QSettings()
        choix = config.value("analisis_sitio/language", "auto", type=str)
        if choix and choix != "auto" and choix.lower() in LANGUES_SUPPORTEES:
            return choix.lower()
    except Exception:
        pass  # Si Qt n'est pas encore initialisé, on passe à l'étape suivante

    # 4. Par défaut, pour cet environnement francophone, on privilégie le français
    return "fr"


def charger_catalogue(code_langue: str) -> dict[str, str]:
    """
    Charge en mémoire le fichier JSON contenant les traductions pour la langue demandée.
    Si le fichier a déjà été lu, il est renvoyé directement depuis la mémoire cache.
    """
    # Si le dictionnaire est déjà en mémoire, on le renvoie immédiatement
    if code_langue in _catalogue_en_cache:
        return _catalogue_en_cache[code_langue]

    # Construction du chemin absolu vers le fichier de langue (ex: locales/fr.json)
    fichier_json = DOSSIER_LOCALES / f"{code_langue}.json"
    traductions: dict[str, str] = {}

    # Vérification de l'existence du fichier sur le disque
    if fichier_json.is_file():
        try:
            # Lecture du fichier texte en encodage UTF-8 universel
            texte_brut = fichier_json.read_text(encoding="utf-8")
            donnees = json.loads(texte_brut)  # Décodage de la structure JSON
            if isinstance(donnees, dict):
                # On s'assure que toutes les clés et valeurs sont bien des chaînes de caractères
                traductions = {str(cle): str(val) for cle, val in donnees.items()}
        except Exception as erreur:
            # En cas de problème de lecture (ex: syntaxe JSON erronée), on journalise l'erreur
            journal.warning("Impossible de charger le fichier de traduction %s : %s", fichier_json, erreur)

    # Mise en cache du dictionnaire pour les prochains appels
    _catalogue_en_cache[code_langue] = traductions
    return traductions


def t(texte_source: str, **arguments: Any) -> str:
    """
    Fonction principale de traduction (raccourci 't').
    Recherche la traduction du 'texte_source' dans la langue courante.
    Si la clé est absente ou si la langue est la langue d'origine, le texte source est renvoyé tel quel.
    Permet également d'insérer des variables dynamiques avec la syntaxe {nom_variable}.
    """
    langue = obtenir_langue_courante()  # Récupération de la langue active
    catalogue = charger_catalogue(langue)  # Chargement du dictionnaire correspondant

    # Recherche du texte traduit ; si non trouvé, on garde le texte d'origine
    texte_traduit = catalogue.get(texte_source, texte_source)

    # Si des variables dynamiques ont été passées (ex: t("Jour {n}", n=5))
    if arguments:
        try:
            return texte_traduit.format(**arguments)
        except Exception:
            # En cas d'incohérence de formatage, on retourne le texte brut pour ne jamais planter
            return texte_traduit

    return texte_traduit


# Alias tr() pour compatibilité avec les conventions Qt et Gettext
tr = t
