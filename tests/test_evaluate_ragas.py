# tests/test_evaluate_ragas.py
"""Vérifie que query_prototype() (eval/evaluate_ragas.py) fonctionne toujours après
la migration du client Mistral - ce fichier n'avait jamais été ré-exécuté depuis
la migration (seulement relu/vérifié syntaxiquement).

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
from mistralai.client import Mistral
from config import MISTRAL_API_KEY

pytestmark = pytest.mark.api


@pytest.fixture(scope="module")
def vector_store():
    return VectorStoreManager()


@pytest.fixture(scope="module")
def mistral_client():
    return Mistral(api_key=MISTRAL_API_KEY)


def test_query_prototype(vector_store, mistral_client):
    """query_prototype() doit enchaîner recherche + génération sans erreur, en
    utilisant en interne la syntaxe migrée (mistral_client.chat.complete(),
    plus l'ancien .chat())."""
    contexts, answer = query_prototype(
        vector_store, mistral_client, "Combien de points Nikola Jokić a-t-il marqués ?"
    )
    # contexts = liste des textes des chunks récupérés (peut être vide, mais jamais None)
    assert isinstance(contexts, list)
    # answer = la réponse générée par Mistral, ne doit jamais être vide
    assert answer
