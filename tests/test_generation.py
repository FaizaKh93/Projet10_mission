# tests/test_generation.py
"""Teste la génération sans appeler l'API : le client Mistral est remplacé par un double.

Ce qui est vérifié ici, c'est le contrat que l'évaluation mesure — format du contexte,
contenu du prompt, température, et comportement quand ça se passe mal (contexte vide,
API en erreur, recherche en échec).
"""
from types import SimpleNamespace

import pytest

from rag import generation


@pytest.fixture
def chunks():
    """Deux chunks au format rendu par VectorStoreManager.search()."""
    return [
        {"text": "Cade a impressionné.", "score": 87.25, "metadata": {"source": "Reddit 1.pdf"}},
        {"text": "Haliburton parle beaucoup.", "score": 71.0, "metadata": {}},
    ]


def faux_client(capture: dict, contenu: str = "Réponse du modèle."):
    """Client minimal : enregistre les arguments reçus et rend une réponse fixe."""
    def complete(**kwargs):
        capture.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=contenu))]
        )
    return SimpleNamespace(chat=SimpleNamespace(complete=complete))


def test_formater_contexte_reprend_source_score_et_texte(chunks):
    contexte = generation.formater_contexte(chunks)
    # Source et score sur la première ligne, contenu sur la seconde
    assert "Source: Reddit 1.pdf (Score: 87.2%)" in contexte
    assert "Contenu: Cade a impressionné." in contexte
    # Métadonnées absentes : la source retombe sur « Inconnue » plutôt que de planter
    assert "Source: Inconnue (Score: 71.0%)" in contexte
    # Les chunks sont séparés par le délimiteur, jamais collés
    assert contexte.count("\n\n---\n\n") == len(chunks) - 1


def test_formater_contexte_vide_annonce_l_absence():
    """Sans chunk, le prompt porte une phrase explicite plutôt qu'un bloc vide."""
    assert generation.formater_contexte([]) == generation.MESSAGE_CONTEXTE_VIDE


def test_generate_answer_construit_le_prompt_attendu(chunks, monkeypatch):
    capture = {}
    monkeypatch.setattr(generation, "client", faux_client(capture))

    reponse = generation.generate_answer(chunks, "Qui mène les Pistons ?")

    assert reponse == "Réponse du modèle."
    # Un seul message, rôle user : la référence ne sépare pas de message système
    assert len(capture["messages"]) == 1
    assert capture["messages"][0]["role"] == "user"
    envoye = capture["messages"][0]["content"]
    # Le prompt contient bien la question et le contexte récupéré
    assert "Qui mène les Pistons ?" in envoye
    assert "Cade a impressionné." in envoye
    assert envoye.endswith("RÉPONSE DE L'ANALYSTE NBA:")
    # Température basse, pour des réponses ancrées dans le contexte
    assert capture["temperature"] == 0.1


def test_generer_reponse_refuse_un_prompt_vide(monkeypatch):
    """Aucun appel ne doit partir si la liste de messages est vide."""
    appels = []
    monkeypatch.setattr(
        generation, "client",
        SimpleNamespace(chat=SimpleNamespace(complete=lambda **kw: appels.append(kw)))
    )
    assert generation.generer_reponse([]) == "Je ne peux pas traiter une demande vide."
    assert appels == []


def test_generer_reponse_absorbe_une_erreur_api(monkeypatch):
    """Une panne de l'API rend un message, jamais une exception qui casse le run."""
    def explose(**kwargs):
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(
        generation, "client", SimpleNamespace(chat=SimpleNamespace(complete=explose))
    )
    assert "erreur technique" in generation.generer_reponse([{"role": "user", "content": "?"}])


def test_generer_reponse_sans_choix(monkeypatch):
    """Réponse structurellement valide mais sans contenu : message dédié."""
    monkeypatch.setattr(
        generation, "client",
        SimpleNamespace(chat=SimpleNamespace(complete=lambda **kw: SimpleNamespace(choices=[])))
    )
    assert "pas pu générer" in generation.generer_reponse([{"role": "user", "content": "?"}])
