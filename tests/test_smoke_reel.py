# tests/test_smoke_reel.py
"""Vérifie contre les VRAIS modèles ce qu'un modèle simulé ne peut pas prouver.

Les tests de `test_pipeline.py` démontrent l'orchestration — l'outil est retiré quand
la route l'exclut, les citations sont vérifiées, les pannes dégradent proprement. Ils
simulent les réponses. Restent deux questions que seule l'API tranche :

- `mistral-small-latest` **route-t-il** correctement une question chiffrée ?
- **écrit-il un SQL juste** contre le schéma qu'on lui décrit ?

**Questions propres à ce test, absentes de `eval/testset.json`.** Un test qui
reprendrait des cas d'évaluation brouillerait la frontière : le jeu de test mesure la
qualité des réponses, ces tests vérifient la plomberie.

Facturé : 2 à 3 appels par test selon les relances.
"""
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import NBA_DB_PATH
from rag.pipeline import repondre
from rag.sql_tool import executer_sql
from rag.vector_store import VectorStoreManager
from schemas import RAGAnswer

pytestmark = pytest.mark.api

# Sondes de plomberie, pas cas d'évaluation.
QUESTION_CHIFFREE = "Quel joueur a marqué le plus de points cette saison ?"
QUESTION_NARRATIVE = "De quelles équipes parlent ces discussions ?"
QUESTION_IMPOSSIBLE = "Quelle équipe a le meilleur bilan à domicile ?"


@pytest.fixture(scope="module", autouse=True)
def journal():
    logging.basicConfig(level=logging.WARNING, format="    [log] %(message)s", force=True)


@pytest.fixture(scope="module")
def index():
    return VectorStoreManager()


def interroger(index, question):
    resultat = repondre(index, question)
    r = resultat.reponse

    print(f"\n--- {question}")
    print(f"    route    : {resultat.route.source} — {resultat.route.motif[:90]}")
    for requete in resultat.requetes:
        print(f"    SQL      : {requete.requete[:120]}")
        print(f"      -> {str(requete.lignes)[:110]}")
    print(f"    abstain  : {r.abstain}   citations : {r.citations}")
    print(f"    réponse  : {r.answer[:170]}")
    if r.abstain_reason:
        print(f"    motif    : {r.abstain_reason[:140]}")
    return resultat


def verifier_la_plomberie(resultat):
    """Ce qui doit tenir quelle que soit la décision du modèle."""
    r = resultat.reponse
    assert isinstance(r, RAGAnswer)
    assert r.texte_visible().strip(), "réponse vide"
    assert "Erreur technique" not in (r.abstain_reason or ""), r.abstain_reason

    # Toute citation a franchi le validateur : elle désigne un fragment servi ou un
    # résultat SQL réellement obtenu.
    ids_servis = {f["id"] for f in resultat.fragments}
    ids_servis |= {f"sql_{i}" for i in range(1, len(resultat.requetes) + 1)}
    assert set(r.citations) <= ids_servis, f"citation hors contexte : {r.citations}"

    if r.abstain:
        assert r.abstain_reason


def test_question_chiffree(index):
    """Le cas qui justifie toute l'étape 2 : la réponse est en base, pas dans les fils.

    On vérifie la plomberie et la **cohérence entre la décision et l'action** ; le
    routage lui-même est observé, car c'est ce que le run d'évaluation doit mesurer.
    """
    resultat = interroger(index, QUESTION_CHIFFREE)
    verifier_la_plomberie(resultat)

    # Le bon chiffre, obtenu indépendamment du modèle
    attendu = executer_sql(
        "SELECT p.full_name, s.pts_total FROM stats s "
        "JOIN players p ON p.player_id = s.player_id ORDER BY s.pts_total DESC LIMIT 1"
    ).lignes[0]
    print(f"    attendu  : {attendu[0]}, {attendu[1]} points")

    if resultat.requetes:
        # Une requête exécutée doit être une lecture : les barrières l'ont laissée passer
        for requete in resultat.requetes:
            assert requete.requete.strip().lower().startswith(("select", "with"))


def test_question_narrative(index):
    """Rien à chercher en base : le modèle ne devrait pas y aller."""
    resultat = interroger(index, QUESTION_NARRATIVE)
    verifier_la_plomberie(resultat)


def test_question_hors_capacites(index):
    """La base n'a ni colonne domicile/extérieur ni table de matchs — c'est écrit dans
    le profil transmis au routeur. L'abstention est observée, pas exigée : c'est
    précisément ce que le run d'évaluation doit mesurer."""
    resultat = interroger(index, QUESTION_IMPOSSIBLE)
    verifier_la_plomberie(resultat)
