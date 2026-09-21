# tests/test_schemas.py
"""Tests des contrats de données du pipeline (src/schemas.py).

Entièrement gratuits : validation pure, aucun appel API. Lancés par un simple
`pytest`.
"""
import pytest
from pydantic import ValidationError

from config import EMBEDDING_DIM
from schemas import (
    EmbeddedChunk,
    PlayerRow,
    RAGAnswer,
    SearchResult,
    SourceDocument,
    TeamRow,
    TextChunk,
)

CONTENU_VALIDE = "Statistiques NBA de la saison régulière pour l'ensemble des joueurs."


# --- SourceDocument : sortie du chargement des fichiers ---


def test_document_valide():
    doc = SourceDocument(page_content=CONTENU_VALIDE, metadata={"source": "regular NBA.xlsx"})
    assert doc.page_content


def test_document_contenu_residuel_refuse():
    """Cas réel : un PDF illisible dont l'extraction et l'OCR ont échoué renvoie
    quand même quelques caractères ("\\nPage 1\\n"), que `if not extracted_content`
    laisse passer puisque la chaîne est non vide."""
    with pytest.raises(ValidationError, match="trop court"):
        SourceDocument(page_content="\nPage 1\n", metadata={"source": "scan.pdf"})


def test_document_sans_source_refuse():
    """Sans source, le document serait indexé sans moyen de tracer d'où il vient."""
    with pytest.raises(ValidationError, match="source"):
        SourceDocument(page_content=CONTENU_VALIDE, metadata={"filename": "x.pdf"})


# --- TextChunk : sortie du découpage ---


def test_chunk_valide():
    assert TextChunk(id="0_3", text="Jokić 2072 points", metadata={"source": "a.xlsx"}).id == "0_3"


def test_chunk_texte_vide_refuse():
    with pytest.raises(ValidationError, match="vide"):
        TextChunk(id="0_3", text="   ", metadata={"source": "a.xlsx"})


def test_chunk_sans_id_refuse():
    """L'id est le chunk_id que le modèle doit citer : sans lui, aucune citation
    n'est vérifiable."""
    with pytest.raises(ValidationError):
        TextChunk(id="", text="texte", metadata={"source": "a.xlsx"})


# --- EmbeddedChunk : sortie de la vectorisation ---


def test_embedding_valide():
    assert len(EmbeddedChunk(id="0_3", embedding=[0.1] * EMBEDDING_DIM).embedding) == EMBEDDING_DIM


def test_embedding_mauvaise_dimension_refuse():
    with pytest.raises(ValidationError, match="dimension"):
        EmbeddedChunk(id="0_3", embedding=[0.1] * 128)


def test_embedding_vecteur_nul_refuse():
    """Le cas central : l'ancien code fabriquait des vecteurs nuls quand l'API
    échouait, ce qui faisait entrer le chunk dans l'index avec un score toujours
    égal à 0 — donc irrécupérable, sans aucun signal."""
    with pytest.raises(ValidationError, match="nul"):
        EmbeddedChunk(id="0_3", embedding=[0.0] * EMBEDDING_DIM)


# --- SearchResult : chunks récupérés, juste avant le prompt ---


def test_search_result_valide():
    r = SearchResult(id="0_3", text="contenu", score=78.4, metadata={"source": "a.xlsx"})
    assert r.raw_score is None  # champ optionnel, utilisé seulement pour le débogage


def test_search_result_score_hors_bornes_refuse():
    with pytest.raises(ValidationError):
        SearchResult(id="0_3", text="contenu", score=420, metadata={"source": "a.xlsx"})


# --- RAGAnswer : sortie du modèle ---


def test_rag_answer_valeurs_par_defaut():
    """Une réponse sans abstention ni citation reste valide : ces champs sont
    optionnels côté modèle."""
    a = RAGAnswer(answer="Jokić a marqué 2072 points.")
    assert a.citations == []
    assert a.abstain is False
    assert a.abstain_reason is None


# --- TeamRow et PlayerRow : lignes de l'Excel, avant insertion en base ---


def test_team_row_valide():
    assert TeamRow(code="OKC", name="Oklahoma City Thunder").code == "OKC"


def test_team_row_code_trop_court_refuse():
    """Un code d'équipe fait 2 à 4 caractères (ATL, BKN...). Une cellule vide ou
    tronquée doit être rejetée, pas insérée telle quelle."""
    with pytest.raises(ValidationError):
        TeamRow(code="", name="Oklahoma City Thunder")


# Les vraies valeurs de SGA sur la saison 2024-25
SGA = {
    "full_name": "Shai Gilgeous-Alexander", "team_code": "OKC",
    "games_played": 76, "wins_total": 63, "losses_total": 13,
    "fgm_total": 859, "fga_total": 1657,
    "three_pm_total": 160, "three_pa_total": 433,
    "ftm_total": 600, "fta_total": 669,
}


def test_player_row_valide():
    assert PlayerRow(**SGA).full_name == "Shai Gilgeous-Alexander"


@pytest.mark.parametrize(
    "reussis, tentes",
    [("fgm_total", "fga_total"), ("three_pm_total", "three_pa_total"), ("ftm_total", "fta_total")],
)
def test_player_row_refuse_plus_de_reussis_que_de_tentes(reussis, tentes):
    """LE test central de cette table, sur les trois familles de tirs.

    On ne peut pas réussir plus de tirs qu'on n'en tente. C'est l'invariant qui
    prouve que la colonne à l'en-tête corrompu (« 15:00:00 ») contient bien 3PM,
    et il détecterait un décalage de colonnes dans un futur export.
    """
    ligne = dict(SGA)
    ligne[reussis] = ligne[tentes] + 1  # un réussi de plus que de tentés : impossible
    with pytest.raises(ValidationError, match="décalées"):
        PlayerRow(**ligne)


def test_player_row_refuse_victoires_defaites_incoherentes():
    """Identité structurelle : tout match joué est une victoire ou une défaite.

    Pas de match nul en NBA, donc w + l == gp — quelle que soit la longueur de la
    saison. On ne vérifie volontairement PAS `gp <= 82` : la saison a déjà été
    écourtée (50 matchs en 1998-99, 66 en 2011-12, 72 en 2020-21), et un joueur
    transféré peut dépasser 82 matchs.
    """
    with pytest.raises(ValidationError, match="games_played"):
        PlayerRow(**{**SGA, "wins_total": 50})  # 50 + 13 != 76


def test_player_row_refuse_valeur_negative():
    """Un nombre de tirs ne peut pas être négatif (contrainte ge=0)."""
    with pytest.raises(ValidationError):
        PlayerRow(**{**SGA, "three_pm_total": -1})
