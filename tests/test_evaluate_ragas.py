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
from schemas import RAGAnswer

pytestmark = pytest.mark.api


@pytest.fixture(scope="module")
def vector_store():
    return VectorStoreManager()


def test_query_prototype(vector_store):
    """query_prototype() rend le `Reponse` complet que la boucle d'évaluation découpe.

    Ce test était resté écrit pour l'ancien retour en tuple de deux éléments, alors que
    `repondre()` rend un objet à quatre champs. Il n'échouait nulle part : porté par
    `pytest -m api`, il n'aurait cassé qu'au lancement d'une campagne facturée.
    """
    resultat = query_prototype(
        vector_store, "Combien de points Nikola Jokić a-t-il marqués ?"
    )
    # Exactement ce que fait la boucle : elle lit des ATTRIBUTS, pas un tuple.
    search_results, reponse = resultat.fragments, resultat.reponse

    # Les fragments arrivent en dicts : c'est ce format que la boucle découpe ensuite en
    # `retrieved_contexts` (texte) et `retrieved_sources` (métadonnées). `id` est
    # indispensable aux citations.
    assert isinstance(search_results, list)
    for res in search_results:
        assert {"id", "text", "metadata", "score"} <= set(res)

    # La sortie est structurée, plus du texte libre
    assert isinstance(reponse, RAGAnswer)
    # La chaîne notée par le juge ne doit jamais être vide
    assert reponse.texte_visible().strip()
    # Toute citation rendue a survécu au validateur : elle existe dans le contexte servi,
    # extrait documentaire comme résultat de requête.
    ids_servis = {r["id"] for r in search_results}
    ids_servis |= {f"sql_{i}" for i in range(1, len(resultat.requetes) + 1)}
    assert set(reponse.citations) <= ids_servis

    # Le routage est enregistré par la boucle pour être noté SANS juge : sans motif ni
    # source, la moitié de l'analyse de l'étape 3 n'a plus de matière.
    assert resultat.route.source in ("documents", "base", "les_deux", "aucune")
    assert resultat.route.motif

    # Question purement chiffrée : la base doit avoir été interrogée.
    assert resultat.requetes, "aucune requête SQL sur une question purement chiffrée"
