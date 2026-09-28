# tests/test_pipeline.py
"""Tests de la chaîne : routage, collecte, réponse.

Gratuits — les deux modèles sont simulés. Le conftest interdit de toute façon tout
appel réel hors des tests marqués `api`.

La propriété centrale vérifiée ici : **l'outil SQL n'est pas seulement déconseillé au
modèle quand la route ne l'inclut pas, il lui est retiré**. Une consigne dans un prompt
ne contraint rien ; l'absence de l'outil, si.
"""
import sys
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rag import generation, pipeline
from schemas import RAGAnswer

FRAGMENTS = [
    {"id": "0_1", "text": "Cade a impressionné.", "score": 87.2,
     "metadata": {"source": "Reddit 1.pdf"}},
]


class FauxIndex:
    """Recherche vectorielle simulée, qui compte ses appels."""

    def __init__(self, resultats=None, casse=False):
        self.appels = 0
        self.resultats = FRAGMENTS if resultats is None else resultats
        self.casse = casse

    def search(self, question, k=5):
        self.appels += 1
        if self.casse:
            raise RuntimeError("index indisponible")
        return self.resultats


def reponse_simple(answer="Réponse.", citations=(), abstain=False, reason=None):
    return {"answer": answer, "citations": list(citations),
            "abstain": abstain, "abstain_reason": reason}


@pytest.fixture
def simuler(monkeypatch):
    """Remplace le routeur et le modèle de réponse ; capture les outils proposés."""
    vu = {"outils": None, "appels": 0}

    def installer(source, sortie=None, appelle_sql=None):
        monkeypatch.setattr(
            pipeline, "router",
            lambda q: pipeline.Route(source=source, motif="décision simulée"),
        )

        def modele(messages, info: AgentInfo) -> ModelResponse:
            vu["appels"] += 1
            vu["outils"] = [t.name for t in info.function_tools]
            # Premier tour : appeler l'outil SQL si le test le demande
            if appelle_sql and vu["appels"] == 1:
                return ModelResponse(parts=[ToolCallPart("interroger_base", {"requete_sql": appelle_sql})])
            return ModelResponse(parts=[
                ToolCallPart(info.output_tools[0].name, sortie or reponse_simple())
            ])

        monkeypatch.setattr(generation.agent, "model", FunctionModel(modele))
        return vu

    return installer


# --- La propriété centrale ----------------------------------------------------------


def test_route_documents_retire_l_outil_sql(simuler):
    """Sur une question narrative, le modèle ne doit même pas voir l'outil."""
    vu = simuler("documents")
    index = FauxIndex()

    pipeline.repondre(index, "Quel joueur est salué pour son leadership ?")

    assert vu["outils"] == [], "l'outil SQL était proposé alors que la route l'exclut"


def test_route_base_propose_l_outil_sql(simuler):
    vu = simuler("base")
    pipeline.repondre(FauxIndex(), "Combien de points a marqué ce joueur ?")

    assert vu["outils"] == ["interroger_base"]


def test_route_les_deux_propose_l_outil_sql(simuler):
    vu = simuler("les_deux")
    pipeline.repondre(FauxIndex(), "Qui est ce joueur et combien a-t-il marqué ?")

    assert vu["outils"] == ["interroger_base"]


# --- La collecte suit la route ------------------------------------------------------


def test_route_base_evite_la_recherche_vectorielle(simuler):
    """Une question purement chiffrée ne doit pas payer un embedding pour un contexte
    que le modèle n'utilisera pas."""
    simuler("base")
    index = FauxIndex()

    pipeline.repondre(index, "Combien de points ?")

    assert index.appels == 0


@pytest.mark.parametrize("source", ["documents", "les_deux"])
def test_les_routes_documentaires_lancent_la_recherche(simuler, source):
    simuler(source)
    index = FauxIndex()

    resultat = pipeline.repondre(index, "Une question ?")

    assert index.appels == 1
    assert resultat.fragments == FRAGMENTS


def test_route_aucune_ne_collecte_rien(simuler):
    simuler("aucune")
    index = FauxIndex()

    resultat = pipeline.repondre(index, "Quelle est la capitale du Pérou ?")

    assert index.appels == 0
    assert resultat.fragments == []


# --- Le résultat porte le chemin parcouru -------------------------------------------


def test_la_reponse_porte_sa_route(simuler):
    simuler("base")
    resultat = pipeline.repondre(FauxIndex(), "Combien de points ?")

    assert isinstance(resultat.reponse, RAGAnswer)
    assert resultat.route.source == "base"
    assert resultat.route.motif


def test_les_requetes_executees_sont_rendues(simuler):
    """Sans cela, une réponse chiffrée serait invérifiable après coup."""
    simuler("base", appelle_sql="SELECT COUNT(*) FROM players",
            sortie=reponse_simple(citations=["sql_1"]))

    resultat = pipeline.repondre(FauxIndex(), "Combien de joueurs ?")

    assert len(resultat.requetes) == 1
    assert resultat.requetes[0].lignes == [[569]]
    # Le résultat SQL est citable comme un fragment : le validateur l'a accepté
    assert resultat.reponse.citations == ["sql_1"]


# --- Dégradations -------------------------------------------------------------------


def test_une_recherche_en_echec_ne_perd_pas_la_question(simuler):
    """On poursuit sans contexte : le nœud de réponse s'abstiendra s'il n'a rien."""
    simuler("documents", sortie=reponse_simple(
        answer="Je ne peux pas répondre.", abstain=True, reason="aucun contexte"))

    resultat = pipeline.repondre(FauxIndex(casse=True), "Une question ?")

    assert resultat.fragments == []
    assert resultat.reponse.abstain


def test_un_routeur_en_panne_replie_sur_les_deux_sources(monkeypatch):
    """Une panne du routeur ne doit priver d'aucune source, et rester signalée."""
    def modele(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reponse_simple())])

    monkeypatch.setattr(generation.agent, "model", FunctionModel(modele))
    # Le vrai routeur, dont le conftest interdit l'appel réel : il doit se replier
    index = FauxIndex()

    resultat = pipeline.repondre(index, "Une question quelconque ?")

    assert resultat.route.source == "les_deux"
    assert "indisponible" in resultat.route.motif
    assert index.appels == 1  # la route de repli collecte bien les documents


# --- Ce que le modèle reçoit réellement ---------------------------------------------


def test_le_schema_est_transmis_avec_l_outil(simuler):
    """Le défaut que seul un appel réel avait révélé.

    `description_du_tool()` existait, était testée, et n'était branchée nulle part : le
    modèle devinait les noms de colonnes — `player_name` au lieu de `full_name` — puis
    épuisait ses relances. La description doit voyager AVEC l'outil.
    """
    vu = {"description": None}

    def modele(messages, info: AgentInfo) -> ModelResponse:
        if info.function_tools:
            vu["description"] = info.function_tools[0].description
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reponse_simple())])

    monkeypatch_local = pytest.MonkeyPatch()
    monkeypatch_local.setattr(
        pipeline, "router", lambda q: pipeline.Route(source="base", motif="simulée")
    )
    monkeypatch_local.setattr(generation.agent, "model", FunctionModel(modele))
    try:
        pipeline.repondre(FauxIndex(), "Combien de points ?")
    finally:
        monkeypatch_local.undo()

    assert vu["description"], "l'outil a été proposé sans description"
    # Les noms réels, ceux que le modèle avait inventés faute de les connaître
    assert "full_name" in vu["description"]
    assert "team_code" in vu["description"]
    # Et les limites de la base, pour qu'il n'invente pas une capacité absente
    assert "granularité par match" in vu["description"]


def test_sans_extrait_le_prompt_oriente_vers_la_base(simuler):
    """Sur une route « base », aucun extrait n'est normal. Le message d'origine
    annonçait « aucune information pertinente trouvée » — décourageant le modèle alors
    que l'outil lui était offert."""
    vu = {"prompt": None}

    def modele(messages, info: AgentInfo) -> ModelResponse:
        from pydantic_ai.messages import UserPromptPart
        for m in messages:
            for part in m.parts:
                if isinstance(part, UserPromptPart):
                    vu["prompt"] = part.content
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reponse_simple())])

    mp = pytest.MonkeyPatch()
    mp.setattr(pipeline, "router", lambda q: pipeline.Route(source="base", motif="simulée"))
    mp.setattr(generation.agent, "model", FunctionModel(modele))
    try:
        pipeline.repondre(FauxIndex(resultats=[]), "Combien de points ?")
    finally:
        mp.undo()

    assert "via l'outil à ta disposition" in vu["prompt"]
    assert "Aucune information pertinente" not in vu["prompt"]
