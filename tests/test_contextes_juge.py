# tests/test_contextes_juge.py
"""Ce que le harnais transmet au juge, pour chacune des quatre routes.

Un run à 18 cas a été perdu ici : sur la route `base`, aucun extrait n'est récupéré,
`retrieved_contexts` partait vide et RAGAS refusait l'échantillon — 9 cas sur 18. Le
défaut ne pouvait apparaître qu'en campagne facturée, puisque le harnais n'avait
aucune fonction testable isolément.

D'où cette fonction pure, et ces tests gratuits.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from evaluate_ragas import construire_contextes
from rag.generation import MESSAGE_CONTEXTE_SQL, MESSAGE_CONTEXTE_VIDE
from rag.sql_tool import executer_sql

FRAGMENT = {
    "id": "0_1",
    "text": "Cade has really impressed with his leadership.",
    "score": 87.2,
    "metadata": {"source": "Reddit 1.pdf"},
}


def test_route_documents_transmet_les_extraits():
    contextes, sources = construire_contextes([FRAGMENT], [], base_autorisee=False)

    assert contextes == [FRAGMENT["text"]]
    assert sources == ["Reddit 1.pdf"]


def test_route_base_transmet_le_resultat_sql():
    """Le cas qui a fait perdre le run : aucun extrait, mais un contexte bien réel."""
    resultat = executer_sql(
        "SELECT s.pts_total FROM stats s JOIN players p ON p.player_id = s.player_id "
        "WHERE p.full_name = 'Cade Cunningham'"
    )
    contextes, sources = construire_contextes([], [resultat], base_autorisee=True)

    assert len(contextes) == 1
    assert "1827" in contextes[0], "le chiffre servi au modèle doit atteindre le juge"
    assert sources == ["base NBA (sql_1)"]


def test_route_les_deux_transmet_les_deux():
    resultat = executer_sql("SELECT COUNT(*) FROM players")
    contextes, sources = construire_contextes([FRAGMENT], [resultat], base_autorisee=True)

    assert len(contextes) == 2
    assert contextes[0] == FRAGMENT["text"]
    assert "569" in contextes[1]
    assert sources == ["Reddit 1.pdf", "base NBA (sql_1)"]


def test_une_requete_sans_ligne_reste_un_contexte():
    """B5, Reggie Miller : `SQL exécuté : 0 ligne(s)`.

    L'absence de résultat est précisément ce qui justifie l'abstention — c'est une
    information, pas un vide. Elle doit parvenir au juge.
    """
    resultat = executer_sql(
        "SELECT s.pts_total FROM stats s JOIN players p ON p.player_id = s.player_id "
        "WHERE p.full_name = 'Reggie Miller'"
    )
    assert resultat.lignes == [], "Reggie Miller est censé être absent de la base"

    contextes, _ = construire_contextes([], [resultat], base_autorisee=True)

    assert len(contextes) == 1
    assert "Aucune ligne" in contextes[0]


@pytest.mark.parametrize(
    "base_autorisee, attendu",
    [(True, MESSAGE_CONTEXTE_SQL), (False, MESSAGE_CONTEXTE_VIDE)],
)
def test_sans_rien_le_juge_recoit_le_message_reel_du_prompt(base_autorisee, attendu):
    """Route `aucune`, ou collecte en échec : le modèle a tout de même lu une phrase.

    C'est elle que le juge doit noter, pas une reconstitution — et surtout pas une
    liste vide, que RAGAS refuse.
    """
    contextes, sources = construire_contextes([], [], base_autorisee=base_autorisee)

    assert contextes == [attendu]
    assert sources == ["aucune source"]


@pytest.mark.parametrize(
    "fragments, requetes_sql, base",
    [
        ([FRAGMENT], [], False),
        ([], ["SELECT COUNT(*) FROM teams"], True),
        ([FRAGMENT], ["SELECT COUNT(*) FROM teams"], True),
        ([], [], True),
        ([], [], False),
    ],
)
def test_aucun_cas_ne_part_au_juge_sans_contexte(fragments, requetes_sql, base):
    """La propriété qui aurait sauvé le run, quelle que soit la route."""
    requetes = [executer_sql(q) for q in requetes_sql]
    contextes, sources = construire_contextes(fragments, requetes, base_autorisee=base)

    assert contextes, "RAGAS refuse un échantillon sans contexte"
    assert all(c.strip() for c in contextes), "un contexte vide vaut une absence"
    assert len(contextes) == len(sources), "contextes et sources doivent rester alignés"
