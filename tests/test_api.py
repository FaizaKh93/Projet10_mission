# tests/test_api.py
"""Tests de l'API REST (app/api.py).

**Entièrement gratuits.** Le seul appel payant du pipeline — `repondre()`, qui enchaîne
routage, embedding de la question et génération — est remplacé par un simulacre. Le
simuler à ce niveau neutralise les trois d'un coup : rien ne part sur le réseau.

`test_aucun_appel_reseau_possible` vérifie que la garantie tient, plutôt que de la
supposer.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.api as module_api  # noqa: E402
from rag.pipeline import Reponse, Route  # noqa: E402
from schemas import RAGAnswer, SQLResult  # noqa: E402

FRAGMENTS = [
    {"id": "0_3", "score": 78.4, "text": "Jokić a marqué 2072 points",
     "metadata": {"source": "Reddit 1.pdf"}},
]

REQUETE = SQLResult(
    requete="SELECT s.pts_total FROM stats s",
    colonnes=["pts_total"],
    lignes=[[2072]],
)


def resultat_type(**champs) -> Reponse:
    """Un résultat plausible du pipeline, dont les tests font varier un champ."""
    reponse = RAGAnswer(**{
        "answer": "Nikola Jokić a marqué 2072 points cette saison.",
        "citations": ["sql_1"],
        "abstain": False,
        "abstain_reason": None,
        **champs.pop("reponse", {}),
    })
    defauts = dict(
        route=Route(source="base", motif="La question demande un chiffre."),
        fragments=FRAGMENTS,
        requetes=[REQUETE],
    )
    return Reponse(reponse=reponse, **{**defauts, **champs})


class VectorStoreFactice:
    """Remplace VectorStoreManager. `index` non nul : l'API le lit pour décider du 503."""

    index = object()
    document_chunks = ["un chunk"]
    appels = 0

    def search(self, question, k=5):
        VectorStoreFactice.appels += 1
        return FRAGMENTS


@pytest.fixture
def api(monkeypatch):
    """Un client HTTP dont le pipeline est entièrement simulé.

    Le journal est vidé à chaque test : il vit dans le module, donc une ligne laissée
    par un test précédent fausserait les suivants.
    """
    module_api._journal.clear()
    monkeypatch.setattr(module_api, "repondre", lambda manager, question: resultat_type())
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


def test_reponse_normale_porte_sa_latence(api):
    """On vérifie la présence et la forme, pas une durée : un pipeline simulé répond en
    moins d'un dixième de milliseconde, donc `total` vaut légitimement 0.0."""
    corps = api.post("/ask", json={"question": "Combien de points ?"}).json()
    assert corps["reponse"]["answer"]
    assert set(corps["latence_ms"]) == {"total"}
    assert corps["latence_ms"]["total"] >= 0


def test_la_reponse_porte_la_route_et_le_sql(api):
    """Sans la route ni la requête, une réponse chiffrée est invérifiable : l'appelant
    devrait croire le modèle sur parole."""
    corps = api.post("/ask", json={"question": "Combien de points ?"}).json()

    assert corps["route"] == "base"
    assert corps["route_motif"]
    assert corps["requetes"][0]["requete"] == "SELECT s.pts_total FROM stats s"
    assert corps["requetes"][0]["lignes"] == [[2072]]


def test_une_abstention_est_un_succes(api, monkeypatch):
    """La distinction que tout le projet défend : le système a abouti, il a conclu
    qu'il ne pouvait pas répondre. La compter comme une panne fausserait toute lecture."""
    monkeypatch.setattr(
        module_api,
        "repondre",
        lambda m, q: resultat_type(
            reponse={"abstain": True, "abstain_reason": "Aucun indicateur domicile/extérieur.",
                     "citations": []},
            requetes=[],
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
    def planter(manager, question):
        raise RuntimeError("API Mistral injoignable")

    monkeypatch.setattr(module_api, "repondre", planter)
    assert api.post("/ask", json={"question": "Combien de points ?"}).status_code == 500

    ligne = api.get("/logs").json()[0]
    assert ligne["issue_appel"] == "echec"
    assert "RuntimeError" in ligne["erreur"]
    # La latence est relevée même en panne : sans elle, un incident lent passerait
    # pour un incident immédiat. Sa valeur dépend de l'horloge, pas son existence.
    assert "total" in ligne["latence_ms"]


# --- Journal ------------------------------------------------------------------------


def test_le_journal_releve_la_provenance(api):
    """Requêtes SQL et citations sont relevées dans le contexte du run, jamais déclarées
    par le modèle : une requête journalisée a forcément tourné."""
    api.post("/ask", json={"question": "Combien de points ?"})
    ligne = api.get("/logs").json()[0]
    assert ligne["base_interrogee"] is True
    assert ligne["requetes_sql"] == ["SELECT s.pts_total FROM stats s"]
    assert ligne["citations"] == ["sql_1"]
    assert ligne["route"] == "base"
    assert ligne["route_motif"]


def test_reponse_tronquee_dans_le_journal(api, monkeypatch):
    monkeypatch.setattr(
        module_api, "repondre", lambda m, q: resultat_type(reponse={"answer": "A" * 900})
    )
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
