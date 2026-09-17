# tests/test_chat.py
"""Vérifie que generer_reponse() (app/chat.py) fonctionne toujours après la
migration du client Mistral - jamais testé jusqu'ici.

Attention : importer app/chat.py a des effets de bord réels dès l'import (il
construit un vrai client Mistral et charge l'index Faiss au niveau module) - ce
n'est pas juste des définitions de fonctions. Ça fonctionne parce que Streamlit
dégrade proprement hors de `streamlit run` ("bare mode", juste des warnings), mais
c'est un signe de plus qu'extraire cette logique dans src/rag/generation.py (prévu
au commit suivant) donnerait un code plus simple à tester.

Appelle la vraie API Mistral (facturé, coût négligeable) : jamais lancé par un
simple `pytest`, seulement via `pytest -m api`.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

pytestmark = pytest.mark.api


def test_generer_reponse():
    """generer_reponse() doit renvoyer une réponse texte non vide, via la syntaxe
    migrée (client.chat.complete(), messages en dict simple au lieu de ChatMessage)."""
    # Import fait ICI (dans la fonction), pas en haut du fichier : les effets de
    # bord de l'import (client + index) ne se déclenchent qu'à l'exécution du
    # test, jamais pendant la simple collecte de pytest
    import chat

    response = chat.generer_reponse([{"role": "user", "content": "Réponds juste 'OK'"}])
    assert response
    # generer_reponse() ne lève pas d'exception en cas de problème, elle renvoie
    # un message d'erreur textuel (voir chat.py) - donc on vérifie aussi ça
    assert "erreur" not in response.lower()
