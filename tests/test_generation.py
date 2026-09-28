# tests/test_generation.py
"""Teste la génération sans appeler l'API.

Le modèle est remplacé par un `FunctionModel` de Pydantic AI : on décide ce qu'il
« répond », donc on peut exercer des comportements qu'une vraie API ne produirait pas à
la demande — une citation inventée, par exemple.
"""
import re
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from rag import generation
from schemas import RAGAnswer, SearchResult

FRAGMENTS = [
    {"id": "0_1", "text": "Cade a impressionné.", "score": 87.25,
     "metadata": {"source": "Reddit 1.pdf"}},
    {"id": "0_2", "text": "Haliburton parle beaucoup.", "score": 71.0, "metadata": {}},
]


def modele_qui_repond(sortie):
    """Un modèle simulé qui rend la ou les sorties données, dans l'ordre."""
    sorties = [sortie] if isinstance(sortie, dict) else list(sortie)
    appels = []

    def fonction(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        appels.append(messages)
        charge = sorties[min(len(appels) - 1, len(sorties) - 1)]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, charge)])

    return fonction, appels


def reponse(answer="Cade Cunningham.", citations=("0_1",), abstain=False, reason=None):
    return {"answer": answer, "citations": list(citations),
            "abstain": abstain, "abstain_reason": reason}


# --- Mise en forme du contexte ------------------------------------------------------


def test_le_contexte_porte_les_identifiants():
    """Sans identifiant dans le prompt, le modèle ne peut citer que le fichier — qui
    compte des dizaines de fragments — et aucune vérification n'est possible."""
    contexte = generation.formater_contexte([SearchResult(**f) for f in FRAGMENTS])

    assert "[0_1] Source: Reddit 1.pdf (Score: 87.2%)" in contexte
    assert "[0_2] Source: Inconnue (Score: 71.0%)" in contexte  # métadonnées absentes
    assert contexte.count("\n\n---\n\n") == len(FRAGMENTS) - 1


def test_contexte_vide_annonce_l_absence():
    assert generation.formater_contexte([]) == generation.MESSAGE_CONTEXTE_VIDE


# --- Contrats d'entrée --------------------------------------------------------------


def test_question_vide_refusee_avant_tout_appel(monkeypatch):
    """Le contrat d'entrée doit arrêter la requête avant de payer un appel."""
    fonction, appels = modele_qui_repond(reponse())
    monkeypatch.setattr(generation.agent, "model", FunctionModel(fonction))

    with pytest.raises(Exception):
        generation.generate_answer(FRAGMENTS, "  ")
    assert appels == []


def test_fragment_malforme_refuse(monkeypatch):
    """Un score hors bornes signale que la normalisation a changé."""
    fonction, _ = modele_qui_repond(reponse())
    monkeypatch.setattr(generation.agent, "model", FunctionModel(fonction))

    with pytest.raises(Exception):
        generation.generate_answer([{**FRAGMENTS[0], "score": 140}], "Question ?")


# --- Vérification des citations -----------------------------------------------------


def test_reponse_bien_citee_acceptee(monkeypatch):
    fonction, appels = modele_qui_repond(reponse(citations=["0_1"]))
    monkeypatch.setattr(generation.agent, "model", FunctionModel(fonction))

    r = generation.generate_answer(FRAGMENTS, "Qui mène les Pistons ?")

    assert isinstance(r, RAGAnswer)
    assert r.citations == ["0_1"] and len(appels) == 1


def test_citation_inventee_renvoyee_au_modele(monkeypatch):
    """Le cœur du dispositif : le modèle cite un fragment absent du contexte, le
    validateur le renvoie corriger. En Python, pas dans le prompt — un modèle qui
    invente une citation peut tout aussi bien affirmer qu'elle est correcte."""
    fonction, appels = modele_qui_repond([
        reponse(citations=["9_99"]),  # identifiant jamais servi
        reponse(citations=["0_1"]),   # correction
    ])
    monkeypatch.setattr(generation.agent, "model", FunctionModel(fonction))

    r = generation.generate_answer(FRAGMENTS, "Qui mène les Pistons ?")

    assert r.citations == ["0_1"]
    assert len(appels) == 2, "le modèle aurait dû être relancé une fois"


def test_citation_obstinee_ne_boucle_pas(monkeypatch):
    """Le budget de relances est borné : un modèle qui s'entête ne coûte pas un run."""
    fonction, appels = modele_qui_repond(reponse(citations=["9_99"]))
    monkeypatch.setattr(generation.agent, "model", FunctionModel(fonction))

    r = generation.generate_answer(FRAGMENTS, "Qui mène les Pistons ?")

    # L'échec est rendu comme une abstention, pas comme une exception
    assert r.abstain and r.abstain_reason
    assert len(appels) == 1 + generation.RELANCES_SORTIE


# --- Sortie --------------------------------------------------------------------------


def test_abstention_porte_son_motif(monkeypatch):
    fonction, _ = modele_qui_repond(
        reponse(answer="Je ne peux pas répondre.", citations=[],
                abstain=True, reason="Reggie Miller est absent des données.")
    )
    monkeypatch.setattr(generation.agent, "model", FunctionModel(fonction))

    r = generation.generate_answer(FRAGMENTS, "Combien de points pour Reggie Miller ?")

    assert r.abstain
    assert "Reggie Miller" in r.texte_visible()


def test_panne_rendue_comme_abstention(monkeypatch):
    """L'interface et le harnais attendent une réponse, pas une exception."""
    def explose(messages, info):
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(generation.agent, "model", FunctionModel(explose))

    r = generation.generate_answer(FRAGMENTS, "Question ?")

    assert r.abstain and "Erreur technique" in (r.abstain_reason or "")


# --- Budgets -------------------------------------------------------------------------


def test_budget_couvre_le_chemin_le_plus_long():
    """Le pire chemin, composant par composant, et non une somme devinée.

    `timeout` borne CHAQUE requête, pas la chaîne. Depuis l'ajout du routeur et de
    l'outil SQL, une question hybride peut enchaîner : routage, premier appel, exécution
    SQL, retour après l'outil, puis une relance du validateur.

    Ce n'est PAS le temps d'une question normale — une question narrative simple fait
    un routage et un appel. C'est la borne que le harnais doit couvrir pour ne jamais
    abandonner un cas légitime.
    """
    from rag.pipeline import DELAI_ROUTAGE_S
    from rag.sql_tool import DELAI_MAX_SECONDES
    from rag.vector_store import REESSAI_API

    chemin_le_plus_long = (
        REESSAI_API.backoff.max_elapsed_time / 1000      # recherche vectorielle
        + DELAI_ROUTAGE_S                                 # routage
        + generation.DELAI_APPEL_S                        # premier appel de génération
        + DELAI_MAX_SECONDES                              # exécution SQL
        + generation.DELAI_APPEL_S                        # retour après l'outil
        + generation.RELANCES_SORTIE * generation.DELAI_APPEL_S  # relance du validateur
    )
    assert chemin_le_plus_long <= 240, f"pire chemin : {chemin_le_plus_long} s"


def test_delais_locaux_tous_bornes():
    """Chaque étape a son propre délai, justifié pour elle-même.

    Un budget global large ne remplace pas des délais locaux stricts : il ne serait
    qu'un garde-fou de dernier recours, pas une réponse à une latence non bornée.
    """
    from rag.pipeline import DELAI_ROUTAGE_S
    from rag.sql_tool import DELAI_MAX_SECONDES
    from rag.vector_store import REESSAI_API

    assert 0 < DELAI_ROUTAGE_S < generation.DELAI_APPEL_S, "le routage doit être plus court"
    assert 0 < DELAI_MAX_SECONDES <= 10, "une requête SQL locale n'a pas besoin de plus"
    assert 0 < REESSAI_API.backoff.max_elapsed_time / 1000 <= 60


def test_delai_par_cas_du_harnais_coherent():
    """Le défaut de `--delai-cas` doit couvrir le pire chemin ci-dessus : sans ce test,
    ajouter une étape ferait abandonner des cas au milieu d'un run payant."""
    source = (Path(__file__).resolve().parent.parent / "eval" / "evaluate_ragas.py").read_text(
        encoding="utf-8"
    )
    assert "delai_cas: float = 240.0" in source
