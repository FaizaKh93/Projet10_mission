# tests/test_loaders.py
"""Tests de src/loading/loaders.py : règle métier sur le schéma Excel et
chargement paresseux du modèle OCR.

Gratuits par défaut (aucun appel API, aucun modèle chargé). Le seul test qui
initialise réellement EasyOCR est marqué `slow` et exclu de `pytest`.
"""
import datetime

import pandas as pd
import pytest

from loading import loaders
from loading.loaders import extract_text_from_excel, validate_excel_schema


# --- Règle métier : schéma Excel ---


def test_validate_excel_schema_detecte_entete_corrompu():
    """Cas réellement rencontré dans ce projet : Excel a réinterprété l'en-tête
    "3PM" (tirs à 3 points réussis) comme l'horaire "3 PM", stocké en
    datetime.time et affiché "15:00". Les valeurs étaient intactes, seul le nom
    de colonne était corrompu — d'où un simple signalement, sans rejet."""
    df = pd.DataFrame({"PTS": [1], datetime.time(15, 0): [2], "FG%": [3]})
    anomalies = validate_excel_schema(df, "Données NBA", "regular NBA.xlsx")
    assert len(anomalies) == 1
    assert "15:00" in anomalies[0]


def test_validate_excel_schema_tableau_sain():
    """Aucun faux positif sur des en-têtes normaux."""
    df = pd.DataFrame({"PTS": [1], "3PM": [2], "FG%": [3]})
    assert validate_excel_schema(df, "Données NBA", "regular NBA.xlsx") == []


# --- Chargement paresseux de l'OCR ---


def test_import_ne_charge_pas_ocr():
    """Importer loaders.py ne doit plus initialiser EasyOCR : avant, un simple
    import chargeait un modèle OCR complet (~30 s), même sans PDF à traiter."""
    assert loaders._ocr_reader is None


def test_excel_ne_declenche_pas_ocr(tmp_path, monkeypatch):
    """Le traitement d'un Excel ne doit jamais toucher à l'OCR."""
    appels = []
    monkeypatch.setattr(loaders, "get_ocr_reader", lambda: appels.append(1))

    fichier = tmp_path / "test.xlsx"
    pd.DataFrame({"PTS": [2072], "REB": [889]}).to_excel(fichier, index=False)

    texte = extract_text_from_excel(str(fichier))
    assert "2072" in texte
    assert appels == []  # OCR jamais sollicité


@pytest.mark.slow
def test_get_ocr_reader_initialise_le_modele():
    """Vérifie le vrai chemin OCR, que le chargement paresseux a modifié.
    Marqué `slow` : charge réellement EasyOCR (plusieurs dizaines de secondes)."""
    lecteur = loaders.get_ocr_reader()
    assert lecteur is not None
    assert loaders._ocr_reader is lecteur  # mis en cache, pas rechargé au 2e appel
    assert loaders.get_ocr_reader() is lecteur
