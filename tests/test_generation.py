# tests/test_generation.py
"""Tests de src/rag/generation.py (agent Pydantic AI + sortie structurée).

Remplace l'ancien tests/test_chat.py : la logique de génération a été extraite de
app/chat.py vers ce module, qui est testable directement (sans Streamlit).

Deux groupes de tests :
- logique pure (formatage du contexte, vérification des citations) : GRATUITS,
  lancés par un simple `pytest`, aucun appel API ;
- génération réelle : marqués "api", lancés seulement via `pytest -m api`.
"""
import sqlite3
import types

import pytest
from pydantic_ai import ModelRetry

from rag import generation
from rag.generation import (
    TraceSQL,
    _format_context,
    _formater_resultat,
    generate_answer,
    interroger_base_nba,
    verify_citations,
)
from rag.sql_tool import description_du_tool, executer_sql
from schemas import AnswerWithSQL, RAGAnswer, SearchResult, SQLResult

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


# --- Tool SQL branché à l'agent (gratuits : le tool est appelé sans LLM) ---


@pytest.fixture
def base_nba(tmp_path):
    """Base minimale au schéma réel, suffisante pour exercer le branchement."""
    chemin = tmp_path / "nba.db"
    con = sqlite3.connect(chemin)
    con.executescript(
        """
        CREATE TABLE players (player_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL) STRICT;
        CREATE TABLE teams (code TEXT PRIMARY KEY, name TEXT NOT NULL) STRICT;
        CREATE TABLE stats (player_id INTEGER, team_code TEXT, pts_total INTEGER, fg_pct REAL) STRICT;
        INSERT INTO players VALUES (1,'Nikola Jokic'), (2,'Jamal Murray');
        INSERT INTO teams VALUES ('DEN','Denver Nuggets');
        INSERT INTO stats VALUES (1,'DEN',2072,57.6), (2,'DEN',1200,47.4);
        """
    )
    con.commit()
    con.close()
    return chemin


@pytest.fixture
def tool_sur_base(monkeypatch, base_nba):
    """Pointe le tool vers la base temporaire, en gardant la VRAIE executer_sql.

    Seul le chemin de la base est substitué : l'autoriseur, la lecture seule et les
    limites s'appliquent donc réellement pendant ces tests.
    """
    monkeypatch.setattr(generation, "executer_sql", lambda requete: executer_sql(requete, base_nba))


def _contexte(trace: TraceSQL):
    """RunContext minimal : le tool n'utilise que ctx.deps."""
    return types.SimpleNamespace(deps=trace)


def test_tool_execute_la_requete_et_renvoie_les_lignes(tool_sur_base):
    trace = TraceSQL()
    texte = interroger_base_nba(_contexte(trace), "SELECT full_name FROM players ORDER BY player_id")
    assert "Nikola Jokic" in texte
    assert "full_name" in texte  # l'en-tête doit être là pour que le modèle nomme la colonne


def test_tool_enregistre_la_requete_dans_la_trace(tool_sur_base):
    """C'est cette trace qui alimente sql_queries : si elle est vide, la réponse
    ne serait pas traçable."""
    trace = TraceSQL()
    interroger_base_nba(_contexte(trace), "SELECT pts_total FROM stats WHERE player_id = 1")
    assert [r.query for r in trace.resultats] == ["SELECT pts_total FROM stats WHERE player_id = 1"]
    assert trace.resultats[0].rows == [{"pts_total": 2072}]


@pytest.mark.parametrize(
    "requete, attendu",
    [
        ("DELETE FROM players", "refusée"),
        ("SELECT colonne_absente FROM players", "invalide"),
        ("SELECT 1; DROP TABLE teams", "Une seule requête"),
    ],
)
def test_tool_leve_model_retry_avec_un_message_exploitable(tool_sur_base, requete, attendu):
    """Une requête rejetée ne doit pas faire échouer le run : ModelRetry renvoie le
    message au modèle, qui corrige lui-même sa requête. D'où l'exigence que le
    message dise LEQUEL des cas s'est produit."""
    with pytest.raises(ModelRetry, match=attendu):
        interroger_base_nba(_contexte(TraceSQL()), requete)


def test_tool_echoue_ne_pollue_pas_la_trace(tool_sur_base):
    """Une requête refusée n'a rien exécuté : elle ne doit pas apparaître dans
    sql_queries, sinon la traçabilité mentirait."""
    trace = TraceSQL()
    with pytest.raises(ModelRetry):
        interroger_base_nba(_contexte(trace), "DELETE FROM players")
    assert trace.resultats == []


# --- Mise en forme du résultat pour le modèle ---


def test_formater_resultat_signale_la_troncature():
    """Sans cet avertissement, le modèle conclurait « il y a 2 joueurs » sur un extrait."""
    resultat = SQLResult(
        query="SELECT full_name FROM players", columns=["full_name"],
        rows=[{"full_name": "A"}, {"full_name": "B"}],
        row_count=2, truncated=True, execution_ms=1.0,
    )
    assert "Coupé à 2 lignes" in _formater_resultat(resultat)


def test_formater_resultat_vide_est_explicite():
    """Zéro ligne est une information : la donnée n'existe pas. Le modèle doit le
    lire comme tel plutôt que comme une absence de réponse."""
    resultat = SQLResult(
        query="SELECT 1", columns=["x"], rows=[], row_count=0, truncated=False, execution_ms=1.0
    )
    assert "Aucune ligne" in _formater_resultat(resultat)


# --- Description exposée au modèle ---


def test_description_contient_le_schema_et_les_exemples(base_nba):
    """La description porte tout ce que le modèle doit savoir : sans le schéma il
    inventerait des colonnes, sans les exemples il oublierait les jointures."""
    description = description_du_tool(f"sqlite:///{base_nba}")
    assert "CREATE TABLE" in description
    assert "_total" in description  # conventions d'unités
    assert "Exemples de questions" in description
    assert "JOIN players p USING (player_id)" in description


def test_le_tool_est_enregistre_sur_agent():
    assert "interroger_base_nba" in generation.agent._function_toolset.tools


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
