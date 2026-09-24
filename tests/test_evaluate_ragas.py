# tests/test_evaluate_ragas.py
"""Vérifie que query_prototype() (eval/evaluate_ragas.py) enchaîne correctement
recherche + génération via l'agent Pydantic AI.

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
from schemas import RAGAnswer
from rag.vector_store import VectorStoreManager

pytestmark = pytest.mark.api


@pytest.fixture(scope="module")
def vector_store():
    return VectorStoreManager()


def test_query_prototype(vector_store):
    """query_prototype() renvoie 4 valeurs : les chunks, les chunks élargis à la
    provenance SQL (jugés par la faithfulness seule), la réponse vue par
    l'utilisateur, et le RAGAnswer complet."""
    contexts, contexts_complets, answer, rag_answer = query_prototype(
        vector_store, "Combien de points Nikola Jokić a-t-il marqués ?"
    )
    # Le contexte élargi contient au moins les chunks, plus la provenance SQL s'il y en a
    assert len(contexts_complets) >= len(contexts)
    # contexts = liste des textes des chunks récupérés (peut être vide, mais jamais None)
    assert isinstance(contexts, list)
    # answer = chaîne envoyée au juge RAGAS, ne doit jamais être vide
    assert answer
    # rag_answer = sortie structurée, source des champs abstain/citations du JSON de résultats
    assert isinstance(rag_answer, RAGAnswer)
    # Si le modèle s'abstient, la raison doit se retrouver dans la réponse notée par RAGAS
    if rag_answer.abstain and rag_answer.abstain_reason:
        assert rag_answer.abstain_reason in answer
