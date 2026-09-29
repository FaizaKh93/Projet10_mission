# tests/test_harnais.py
"""Tests du harnais d'évaluation : ce qu'il garantit vraiment, mesuré.

Gratuits : le système interrogé est remplacé par une fonction. On mesure des
comportements — temps mural, nombre d'appels — et non des constantes de configuration.
Un test qui compare deux valeurs de `pyproject.toml` ne prouve rien sur l'exécution.
"""
import json
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


def test_ce_que_le_harnais_ecrit_est_serialisable_en_json():
    """Le harnais réécrit le fichier de résultats APRÈS CHAQUE cas.

    Une valeur non sérialisable ferait échouer l'écriture une fois le cas déjà payé, et
    arrêterait la campagne à cet endroit. SQLite peut rendre des `bytes`, que
    `json.dumps` refuse — le risque n'est pas théorique.

    On sérialise donc de vraies lignes, issues de la vraie base, dans la forme exacte
    que la boucle enregistre.
    """
    from rag.sql_tool import executer_sql

    resultat = executer_sql(
        "SELECT p.full_name, s.pts_total, s.fg_pct, s.minutes_per_game "
        "FROM stats s JOIN players p ON p.player_id = s.player_id LIMIT 5"
    )

    enregistrement = {
        "sql_requetes": [resultat.requete],
        "sql_lignes": [resultat.lignes],
    }
    texte = json.dumps(enregistrement, ensure_ascii=False, indent=2)

    # Aller-retour complet : ce que le notebook relira ensuite
    assert json.loads(texte)["sql_lignes"][0] == resultat.lignes
