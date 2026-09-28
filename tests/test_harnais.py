# tests/test_harnais.py
"""Tests du harnais d'évaluation : ce qu'il garantit vraiment, mesuré.

Gratuits : le système interrogé est remplacé par une fonction. On mesure des
comportements — temps mural, nombre d'appels — et non des constantes de configuration.
Un test qui compare deux valeurs de `pyproject.toml` ne prouve rien sur l'exécution.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

import evaluate_ragas as ev


def test_le_delai_rend_reellement_la_main():
    """Le point que `with ThreadPoolExecutor(...)` faisait échouer en silence.

    Sa sortie appelle `shutdown(wait=True)`, qui attend le thread bloqué : le délai
    était décoratif — 4 s mesurées pour 1 s demandée. Ce test mesure le temps mural,
    seule façon de le voir ; aucune lecture de configuration ne l'aurait détecté.
    """
    def systeme_bloque(manager, question):
        time.sleep(5)
        return [], None

    ev.query_prototype = systeme_bloque
    debut = time.monotonic()
    with pytest.raises(TimeoutError, match="abandonnée"):
        ev.interroger_avec_delai(None, "Question ?", delai=1.0)
    ecoule = time.monotonic() - debut

    assert ecoule < 2.0, f"le harnais a attendu {ecoule:.1f} s pour un délai de 1 s"


def test_un_cas_rapide_n_est_pas_penalise():
    """Le garde-fou ne doit rien coûter au cas nominal."""
    def systeme_rapide(manager, question):
        return ["fragment"], "réponse"

    ev.query_prototype = systeme_rapide
    debut = time.monotonic()
    fragments, reponse = ev.interroger_avec_delai(None, "Question ?", delai=10.0)
    assert (time.monotonic() - debut) < 1.0
    assert fragments == ["fragment"] and reponse == "réponse"


def test_le_delai_par_cas_couvre_le_pire_cas_mesure():
    """Le défaut de `--delai-cas` doit dépasser le pire cas RÉEL, mesuré ailleurs.

    Les deux bornes viennent du code, mais le nombre d'appels qu'elles multiplient est
    compté par exécution dans tests/test_generation.py et tests/test_pipeline_contracts.py,
    pas déduit de la configuration.
    """
    from rag.generation import DELAI_APPEL_S, RELANCES_SORTIE
    from rag.vector_store import REESSAI_API

    pire_generation = (1 + RELANCES_SORTIE) * DELAI_APPEL_S
    pire_recherche = REESSAI_API.backoff.max_elapsed_time / 1000
    import inspect

    defaut = inspect.signature(ev.main).parameters["delai_cas"].default
    assert defaut >= pire_generation + pire_recherche
