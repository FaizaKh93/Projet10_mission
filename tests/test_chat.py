# tests/test_chat.py
"""Teste l'interface Streamlit sans navigateur et sans appel payant.

`AppTest` est le harnais officiel de Streamlit : il exécute le script comme le ferait
le serveur, expose les widgets produits, et permet de simuler une saisie. Le modèle et
l'embedding sont remplacés par des doubles.

Ce qu'on vérifie ici n'est pas l'apparence mais le **contrat de l'interface** : ce que
l'utilisateur lit correspond-il à ce que le système a réellement répondu, abstention et
citations comprises.
"""
import sys
from pathlib import Path

import pytest
import streamlit as st
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from streamlit.testing.v1 import AppTest

CHEMIN_APP = str(Path(__file__).resolve().parent.parent / "app" / "chat.py")

FRAGMENTS = [
    {"id": "0_1", "text": "Cade a impressionné par son leadership.", "score": 87.2,
     "raw_score": 0.87, "metadata": {"source": "Reddit 1.pdf"}},
]


def modele_rendant(charge):
    def fonction(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, charge)])
    return fonction


@pytest.fixture
def app(monkeypatch):
    """L'application, avec la recherche et le modèle neutralisés.

    Les doubles sont posés sur les modules importés, pas sur le script : celui-ci les
    résout au moment de son exécution par AppTest.
    """
    from rag import generation, vector_store

    monkeypatch.setattr(vector_store.VectorStoreManager, "__init__", lambda self: None)
    monkeypatch.setattr(vector_store.VectorStoreManager, "index",
                        type("I", (), {"ntotal": 100})(), raising=False)
    monkeypatch.setattr(vector_store.VectorStoreManager, "document_chunks",
                        ["fragment"] * 100, raising=False)
    monkeypatch.setattr(vector_store.VectorStoreManager, "search",
                        lambda self, q, k=5: FRAGMENTS, raising=False)

    def lancer(charge):
        monkeypatch.setattr(generation.agent, "model", FunctionModel(modele_rendant(charge)))
        # `@st.cache_resource` survit d'un test à l'autre dans le même processus : sans
        # ce vidage, le gestionnaire mis en cache par un test précédent serait réutilisé
        # et les tests deviendraient dépendants de leur ordre.
        st.cache_resource.clear()
        return AppTest.from_file(CHEMIN_APP, default_timeout=30).run()

    return lancer


def reponse(answer="Cade Cunningham.", citations=("0_1",), abstain=False, reason=None):
    return {"answer": answer, "citations": list(citations),
            "abstain": abstain, "abstain_reason": reason}


def test_l_application_demarre(app):
    """Au premier chargement : un titre, un message d'accueil, aucune erreur."""
    at = app(reponse())

    assert not at.exception
    assert at.title[0].value == "NBA Analyst AI"
    assert "Bonjour" in at.chat_message[0].markdown[0].value
    assert not at.error


def test_une_question_affiche_la_reponse_et_ses_citations(app):
    """Le chemin nominal : la réponse du modèle et les fragments qu'elle invoque."""
    at = app(reponse(answer="Cade Cunningham.", citations=["0_1"]))
    at.chat_input[0].set_value("Qui mène les Pistons ?").run()

    assert not at.exception
    textes = [m.value for bloc in at.chat_message for m in bloc.markdown]
    assert any("Cade Cunningham." in t for t in textes)
    # Les citations sont affichées : c'est ce qui rend la réponse vérifiable par l'œil
    assert any("0_1" in c.value for c in at.caption)


def test_une_abstention_montre_son_motif(app):
    """Le motif porte la substance d'un refus : le cacher laisserait l'utilisateur
    devant une phrase creuse."""
    at = app(reponse(answer="Je ne peux pas répondre.", citations=[],
                     abstain=True, reason="Reggie Miller est absent des données."))
    at.chat_input[0].set_value("Combien de points pour Reggie Miller ?").run()

    assert not at.exception
    textes = " ".join(m.value for bloc in at.chat_message for m in bloc.markdown)
    assert "Reggie Miller est absent des données." in textes
    assert "[Réponse incertaine]" in textes
    # Une abstention n'invoque aucun fragment : pas de bandeau de citations
    assert not at.caption or all("0_1" not in c.value for c in at.caption)


def test_index_absent_arrete_proprement(app, monkeypatch):
    """Sans index, l'application doit le dire au lieu de répondre à partir de rien."""
    from rag import vector_store

    monkeypatch.setattr(vector_store.VectorStoreManager, "index", None, raising=False)
    at = app(reponse())

    assert not at.exception
    assert any("index" in e.value.lower() for e in at.error)
