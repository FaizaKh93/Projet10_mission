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


def test_budgets_compatibles_avec_le_delai_par_cas():
    """`timeout` borne CHAQUE requête, pas la génération entière : depuis que le
    validateur peut relancer le modèle, le pire cas vaut (1 + RELANCES_SORTIE) appels.
    """
    from rag.vector_store import REESSAI_API

    pire_generation = (1 + generation.RELANCES_SORTIE) * generation.DELAI_APPEL_S
    pire_recherche = REESSAI_API.backoff.max_elapsed_time / 1000
    assert pire_generation + pire_recherche <= 180


def test_delai_par_cas_du_harnais_coherent():
    """Le défaut de `--delai-cas` doit suivre les budgets ci-dessus : sans ce test, les
    relever ferait abandonner des cas au milieu d'un run payant."""
    source = (Path(__file__).resolve().parent.parent / "eval" / "evaluate_ragas.py").read_text(
        encoding="utf-8"
    )
    assert "delai_cas: float = 180.0" in source


def test_le_prompt_envoye_est_celui_de_la_reference(monkeypatch):
    """Vérifie le prompt REÇU par le modèle, pas la constante.

    Cette branche fait varier la structure de la sortie, pas la consigne. Un test qui
    se contenterait de comparer `SYSTEM_PROMPT` à la référence passerait même si
    `generate_answer` ne s'en servait pas — c'est exactement ce qui s'est produit une
    fois : la constante était juste, et le prompt envoyé avait perdu ses deux premières
    lignes.
    """
    vu = {}

    def capture(messages, info):
        for m in messages:
            for p in m.parts:
                if isinstance(p, UserPromptPart):
                    vu["prompt"] = p.content
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reponse())])

    monkeypatch.setattr(generation.agent, "model", FunctionModel(capture))
    generation.generate_answer(FRAGMENTS, "Qui mène les Pistons ?")

    attendu = generation.SYSTEM_PROMPT.format(
        context_str=generation.formater_contexte([SearchResult(**f) for f in FRAGMENTS]),
        question="Qui mène les Pistons ?",
    )
    assert vu["prompt"] == attendu
    assert vu["prompt"].startswith("Tu es 'NBA Analyst AI'")


def test_prompt_identique_a_la_reference():
    """La consigne ne doit pas bouger tant qu'une branche ne le décide pas."""
    reference = Path(__file__).resolve().parent.parent / "P10_DSML" / "MistralChat.py"
    if not reference.exists():
        pytest.skip("dossier de référence absent (non versionné)")
    brut = re.search(r'SYSTEM_PROMPT = f"""(.*?)"""', reference.read_text(encoding="utf-8"), re.S)
    attendu = brut.group(1).replace("{{", "{").replace("}}", "}")
    assert generation.SYSTEM_PROMPT == attendu


def test_nombre_maximal_de_requetes_modele(monkeypatch):
    """Compte les appels réels sur chaque chemin, au lieu de les supposer.

    Le budget temporel d'un cas d'évaluation vaut (nombre d'appels) x DELAI_APPEL_S.
    Ce nombre doit être mesuré : un tool, un second agent ou une relance
    supplémentaire le changeraient sans que la configuration bouge d'un caractère.
    """
    def compter(sorties):
        fonction, appels = modele_qui_repond(sorties)
        monkeypatch.setattr(generation.agent, "model", FunctionModel(fonction))
        generation.generate_answer(FRAGMENTS, "Une question de test ?")
        return len(appels)

    chemins = {
        "sortie valide du premier coup": compter(reponse(citations=["0_1"])),
        "citation inventée puis corrigée": compter(
            [reponse(citations=["9_99"]), reponse(citations=["0_1"])]
        ),
        "citation inventée obstinée": compter(reponse(citations=["9_99"])),
        "abstention": compter(reponse(answer="Non.", citations=[], abstain=True, reason="absent")),
    }
    maximum = max(chemins.values())
    assert maximum == 1 + generation.RELANCES_SORTIE, chemins


def test_aucun_tool_ni_second_agent():
    """Condition sous laquelle le compte ci-dessus reste le maximum.

    Un tool ajoute un aller-retour par appel, un second agent en ajoute un par question.
    Ce test tombera le jour où l'un des deux arrive — c'est-à-dire au moment de
    recalculer le budget, pas au milieu d'un run payant.
    """
    source = Path(generation.__file__).read_text(encoding="utf-8")
    assert "@agent.tool" not in source
    assert source.count("Agent(") == 1
