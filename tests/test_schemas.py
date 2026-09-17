# tests/test_schemas.py
"""Tests des contrats de données du pipeline (src/schemas.py).

Entièrement gratuits : validation pure, aucun appel API. Lancés par un simple
`pytest`.
"""
import pytest
from pydantic import ValidationError

from config import EMBEDDING_DIM
from schemas import EmbeddedChunk, RAGAnswer, SearchResult, SourceDocument, TextChunk

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
