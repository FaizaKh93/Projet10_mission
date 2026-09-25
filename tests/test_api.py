# tests/test_api.py
"""Tests de l'API REST (app/api.py).

**Entièrement gratuits.** Les deux appels payants du pipeline sont remplacés par des
simulacres :

    manager.search(...)   -> un appel d'embedding Mistral
    generate_answer(...)  -> génération, plus les appels de tool éventuels

Neutraliser un seul des deux ne suffit pas : une recherche vectorielle part sur le
réseau même si la génération est simulée. La fixture `api` pose les deux d'un coup, et
`test_aucun_appel_reseau_possible` vérifie que la garantie tient.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.api as module_api  # noqa: E402
from schemas import AnswerWithSQL, SearchResult  # noqa: E402

CHUNKS = [
    SearchResult(id="0_3", score=78.4, text="Jokić a marqué 2072 points", metadata={"source": "x"}),
]


def reponse_type(**champs) -> AnswerWithSQL:
    """Une réponse plausible du système, dont les tests font varier un champ."""
    defauts = dict(
        answer="Nikola Jokić a marqué 2072 points cette saison.",
        citations=["0_3"],
        abstain=False,
        abstain_reason=None,
        sql_queries=["SELECT s.pts_total FROM stats s"],
        sql_results=["pts_total\n2072"],
    )
    return AnswerWithSQL(**{**defauts, **champs})


class VectorStoreFactice:
    """Remplace VectorStoreManager. `index` non nul : l'API le lit pour décider du 503."""

    index = object()
    document_chunks = ["un chunk"]
    appels = 0

    def search(self, question, k=5):
        VectorStoreFactice.appels += 1
        return CHUNKS


@pytest.fixture
def api(monkeypatch):
    """Un client HTTP dont le pipeline est entièrement simulé.

    Le journal est vidé à chaque test : il vit dans le module, donc une ligne laissée
    par un test précédent fausserait les suivants.
    """
    module_api._journal.clear()
    monkeypatch.setattr(module_api, "generate_answer", lambda chunks, question: reponse_type())
    with TestClient(module_api.app) as client:
        monkeypatch.setitem(module_api._ressources, "vector_store", VectorStoreFactice())
        yield client


# --- Garantie : aucun appel payant ne peut avoir lieu -------------------------------


def test_aucun_appel_reseau_possible(api, monkeypatch):
    """Garde-fou de toute la suite : si la vraie recherche était appelée, ce test
    échouerait au lieu de coûter de l'argent en silence."""

    def interdit(*a, **k):
        raise AssertionError("appel réseau réel : le simulacre n'a pas été posé")

    monkeypatch.setattr(module_api.VectorStoreManager, "search", interdit)
    assert api.post("/ask", json={"question": "Combien de points ?"}).status_code == 200


# --- Endpoints sans pipeline --------------------------------------------------------


def test_health_repond_sans_dependance(api):
    """Liveness : doit répondre même si tout le reste est en panne."""
    assert api.get("/health").json() == {"status": "ok"}


def test_racine_liste_les_endpoints(api):
    corps = api.get("/").json()
    assert corps["documentation"] == "/docs"
    assert {"/health", "/ready", "/ask", "/logs"} <= set(corps["endpoints"])


def test_ready_verifie_les_trois_dependances(api):
    verifications = api.get("/ready").json()["verifications"]
    assert set(verifications) == {"index_vectoriel", "base_nba", "cle_mistral"}


def test_ready_renvoie_503_et_nomme_la_dependance_absente(api):
    """Un 200 mensonger ferait router du trafic vers un service incapable de répondre."""
    module_api._ressources["vector_store"] = None
    reponse = api.get("/ready")
    assert reponse.status_code == 503
    assert reponse.json()["detail"]["index_vectoriel"] is False


# --- Validation de l'entrée ---------------------------------------------------------


@pytest.mark.parametrize("question", ["ab", "", "x" * 501])
def test_question_hors_bornes_rejetee_avant_le_pipeline(api, question):
    """422 rendu par Pydantic : la question n'atteint jamais le modèle, donc ne coûte rien."""
    avant = VectorStoreFactice.appels
    assert api.post("/ask", json={"question": question}).status_code == 422
    assert VectorStoreFactice.appels == avant, "le pipeline a été atteint malgré le rejet"


# --- /ask : les trois issues --------------------------------------------------------


def test_reponse_normale_decompose_la_latence(api):
    corps = api.post("/ask", json={"question": "Combien de points ?"}).json()
    assert corps["reponse"]["answer"]
    assert set(corps["latence_ms"]) == {"recherche", "generation", "total"}


def test_une_abstention_est_un_succes(api, monkeypatch):
    """La distinction que tout le projet défend : le système a abouti, il a conclu
    qu'il ne pouvait pas répondre. La compter comme une panne fausserait toute lecture."""
    monkeypatch.setattr(
        module_api,
        "generate_answer",
        lambda c, q: reponse_type(
            abstain=True, abstain_reason="Aucun indicateur domicile/extérieur.", sql_queries=[]
        ),
    )
    assert api.post("/ask", json={"question": "Domicile ou extérieur ?"}).status_code == 200

    ligne = api.get("/logs").json()[0]
    assert ligne["issue_appel"] == "abouti"
    assert ligne["abstention"] is True
    assert ligne["abstain_reason"]


def test_index_absent_renvoie_503_et_journalise(api):
    module_api._ressources["vector_store"] = None
    assert api.post("/ask", json={"question": "Combien de points ?"}).status_code == 503

    ligne = api.get("/logs").json()[0]
    assert ligne["issue_appel"] == "echec"
    assert "scripts/index.py" in ligne["erreur"]


def test_panne_de_generation_renvoie_500_et_journalise(api, monkeypatch):
    def planter(chunks, question):
        raise RuntimeError("API Mistral injoignable")

    monkeypatch.setattr(module_api, "generate_answer", planter)
    assert api.post("/ask", json={"question": "Combien de points ?"}).status_code == 500

    ligne = api.get("/logs").json()[0]
    assert ligne["issue_appel"] == "echec"
    assert "RuntimeError" in ligne["erreur"]
    assert ligne["latence_ms"]["total"] > 0


# --- Journal ------------------------------------------------------------------------


def test_le_journal_releve_la_provenance(api):
    """Requêtes SQL et citations sont relevées dans la trace, jamais déclarées par le
    modèle : une requête journalisée a forcément tourné."""
    api.post("/ask", json={"question": "Combien de points ?"})
    ligne = api.get("/logs").json()[0]
    assert ligne["base_interrogee"] is True
    assert ligne["requetes_sql"] == ["SELECT s.pts_total FROM stats s"]
    assert ligne["citations"] == ["0_3"]


def test_reponse_tronquee_dans_le_journal(api, monkeypatch):
    monkeypatch.setattr(module_api, "generate_answer", lambda c, q: reponse_type(answer="A" * 900))
    api.post("/ask", json={"question": "Une longue réponse"})
    assert len(api.get("/logs").json()[0]["extrait_reponse"]) == 300


def test_journal_du_plus_recent_au_plus_ancien(api):
    for n in ("premiere", "deuxieme", "troisieme"):
        api.post("/ask", json={"question": f"Question {n} ?"})
    questions = [ligne["question"] for ligne in api.get("/logs").json()]
    assert questions == ["Question troisieme ?", "Question deuxieme ?", "Question premiere ?"]


def test_journal_borne_a_cent_appels(api):
    """`deque(maxlen=100)` : la mémoire est bornée par construction, pas par vigilance."""
    for n in range(105):
        api.post("/ask", json={"question": f"Question {n} ?"})
    lignes = api.get("/logs").json()
    assert len(lignes) == 100
    assert lignes[0]["question"] == "Question 104 ?"


def test_limit_reduit_sans_reordonner(api):
    for n in range(5):
        api.post("/ask", json={"question": f"Question {n} ?"})
    lignes = api.get("/logs", params={"limit": 2}).json()
    assert [ligne["question"] for ligne in lignes] == ["Question 4 ?", "Question 3 ?"]
