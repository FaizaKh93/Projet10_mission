# tests/test_smoke_reel.py
"""Vérifie contre le VRAI modèle ce qu'un modèle simulé ne peut pas prouver.

`test_generation.py` démontre l'orchestration — le validateur relance, le budget est
borné, les contrats arrêtent les entrées fautives — mais il simule les réponses. Reste
une question que seule l'API peut trancher : `mistral-small-latest` respecte-t-il le
schéma `RAGAnswer`, et cite-t-il des identifiants exploitables ?

**Questions propres à ce test, délibérément absentes de `eval/testset.json`.** Un test
qui reprendrait des cas d'évaluation brouillerait la frontière entre les deux : le jeu
de test mesure la qualité des réponses, ces tests vérifient la plomberie. Ils doivent
pouvoir être lus et modifiés sans toucher à la mesure.

Facturé : 2 appels, un par test.
"""
import logging

import pytest

from config import SEARCH_K
from rag.generation import generate_answer
from rag.vector_store import VectorStoreManager
from schemas import RAGAnswer

pytestmark = pytest.mark.api

# Sondes de plomberie, pas cas d'évaluation. La première a de quoi être répondue par le
# corpus Reddit, la seconde n'a rien à voir avec lui.
QUESTION_COUVERTE = "De quelles équipes parlent ces discussions ?"
QUESTION_HORS_SUJET = "Quel est le prix d'un billet pour assister à un match ?"


@pytest.fixture(scope="module", autouse=True)
def journal():
    """Rend visibles les avertissements du validateur : sans eux, un défaut de
    conformité du modèle est indiscernable d'une panne réseau."""
    logging.basicConfig(level=logging.WARNING, format="    [log] %(message)s", force=True)


@pytest.fixture(scope="module")
def vector_store():
    return VectorStoreManager()


def interroger(vector_store, question):
    fragments = vector_store.search(question, k=SEARCH_K)
    reponse = generate_answer(fragments, question)

    print(f"\n--- {question}")
    print(f"    abstain  : {reponse.abstain}   citations : {reponse.citations}")
    print(f"    réponse  : {reponse.answer[:160]}")
    if reponse.abstain_reason:
        print(f"    motif    : {reponse.abstain_reason[:140]}")
    return reponse, {f["id"] for f in fragments}


def verifier_la_plomberie(reponse, ids_servis):
    """Ce qui doit tenir quelle que soit la réponse du modèle."""
    assert isinstance(reponse, RAGAnswer)
    assert reponse.texte_visible().strip(), "réponse vide"
    # Toute citation rendue a franchi le validateur, donc existe dans le contexte servi
    assert set(reponse.citations) <= ids_servis, "une citation a échappé au validateur"
    # Une panne technique satisfait toutes les assertions ci-dessus : sans ce contrôle,
    # le test passerait pendant que le système ne répond pas du tout.
    assert "Erreur technique" not in (reponse.abstain_reason or ""), reponse.abstain_reason
    # Le contrat impose qu'une abstention soit motivée : on vérifie qu'il a tenu
    if reponse.abstain:
        assert reponse.abstain_reason


def test_question_couverte_par_le_corpus(vector_store):
    """Le modèle doit produire une sortie structurée et citer des fragments réels."""
    reponse, ids_servis = interroger(vector_store, QUESTION_COUVERTE)
    verifier_la_plomberie(reponse, ids_servis)


def test_question_hors_sujet(vector_store):
    """Rien dans le corpus ne parle de billetterie.

    L'abstention est **observée, pas exigée** : c'est un comportement que le run
    d'évaluation doit mesurer, et un test qui l'imposerait ici en masquerait le
    résultat. Ce qui est exigé, c'est que la plomberie tienne.
    """
    reponse, ids_servis = interroger(vector_store, QUESTION_HORS_SUJET)
    verifier_la_plomberie(reponse, ids_servis)
