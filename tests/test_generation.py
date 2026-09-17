# tests/test_generation.py
"""Tests de src/rag/generation.py (agent Pydantic AI + sortie structurée).

Remplace l'ancien tests/test_chat.py : la logique de génération a été extraite de
app/chat.py vers ce module, qui est testable directement (sans Streamlit).

Deux groupes de tests :
- logique pure (formatage du contexte, vérification des citations) : GRATUITS,
  lancés par un simple `pytest`, aucun appel API ;
- génération réelle : marqués "api", lancés seulement via `pytest -m api`.
"""
import pytest

from rag.generation import _format_context, generate_answer, verify_citations
from schemas import RAGAnswer, SearchResult

# Chunks factices réutilisés par les tests gratuits : de vrais SearchResult, donc
# exactement ce que renvoie VectorStoreManager.search() - les tests reproduisent le
# contrat réel du code, pas une approximation en dict
CHUNKS = [
    SearchResult(id="0_3", score=78.4, text="Jokić a marqué 2072 points", metadata={"source": "regular NBA.xlsx"}),
    SearchResult(id="1_7", score=61.2, text="Curry : 93,3% aux lancers francs", metadata={"source": "regular NBA.xlsx"}),
]


# --- Tests gratuits (aucun appel API) ---


def test_format_context_expose_les_chunk_id():
    """Le chunk_id doit apparaître dans le contexte : sans lui, le modèle ne peut
    pas citer ses sources et le champ citations serait inutilisable."""
    context = _format_context(CHUNKS)
    assert "chunk_id: 0_3" in context
    assert "chunk_id: 1_7" in context


def test_format_context_vide():
    """Sans aucun chunk récupéré, un message explicite remplace un contexte vide."""
    assert "Aucune information pertinente" in _format_context([])


def test_verify_citations_garde_les_citations_valides():
    answer = RAGAnswer(answer="Jokić a marqué 2072 points", citations=["0_3"])
    assert verify_citations(answer, CHUNKS).citations == ["0_3"]


def test_verify_citations_retire_les_chunk_id_inventes():
    """Cas central : le modèle cite un chunk qu'il n'a jamais reçu (hallucination
    de source) - la citation doit être retirée, pas conservée telle quelle."""
    answer = RAGAnswer(answer="...", citations=["0_3", "99_99"])
    assert verify_citations(answer, CHUNKS).citations == ["0_3"]


def test_verify_citations_sans_citation():
    answer = RAGAnswer(answer="...", citations=[])
    assert verify_citations(answer, CHUNKS).citations == []


# --- Test payant (vrai appel Mistral via l'agent) ---


@pytest.mark.api
def test_generate_answer_renvoie_une_sortie_structuree():
    """generate_answer() doit renvoyer un RAGAnswer validé, avec des citations
    qui référencent uniquement des chunks réellement fournis."""
    answer = generate_answer(CHUNKS, "Combien de points Nikola Jokić a-t-il marqués ?")
    assert isinstance(answer, RAGAnswer)
    assert answer.answer  # jamais vide
    # Après verify_citations(), toute citation restante est forcément valide
    assert all(c in {"0_3", "1_7"} for c in answer.citations)
