# tests/test_evaluate_ragas.py
"""Vérifie que query_prototype() (eval/evaluate_ragas.py) enchaîne bien recherche
puis génération, et rend ce que la boucle d'évaluation attend.

Appelle la vraie API Mistral (facturé, coût négligeable) : jamais lancé par un
simple `pytest`, seulement via `pytest -m api`.
"""
import sys
from pathlib import Path

import pytest

# eval/ n'est pas un package installé (comme src/, cf. conftest.py) - il faut
# l'ajouter explicitement au sys.path pour pouvoir importer evaluate_ragas
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from evaluate_ragas import query_prototype
from rag.vector_store import VectorStoreManager

pytestmark = pytest.mark.api


@pytest.fixture(scope="module")
def vector_store():
    return VectorStoreManager()


def test_query_prototype(vector_store):
    """query_prototype() rend les chunks bruts et la réponse texte."""
    search_results, answer = query_prototype(
        vector_store, "Combien de points Nikola Jokić a-t-il marqués ?"
    )
    # Les chunks arrivent en dicts : c'est ce format que la boucle découpe ensuite
    # en `retrieved_contexts` (texte) et `retrieved_sources` (métadonnées).
    assert isinstance(search_results, list)
    for res in search_results:
        assert "text" in res and "metadata" in res and "score" in res
    # La réponse envoyée au juge RAGAS ne doit jamais être vide
    assert isinstance(answer, str) and answer.strip()
