# tests/test_mistral_client.py
"""Vérifie que le client Mistral migré (mistralai 2.x) fonctionne correctement,
après la migration MistralClient (0.x) -> Mistral (2.x).

Appelle la vraie API Mistral (facturé, coût négligeable) : jamais lancé par un
simple `pytest`, seulement via `pytest -m api`.
"""
import pytest

from config import MISTRAL_API_KEY
from mistralai.client import Mistral

# Applique le marker "api" à toutes les fonctions de test de ce fichier d'un coup,
# plutôt que de répéter @pytest.mark.api sur chacune
pytestmark = pytest.mark.api


# scope="module" : le client n'est créé qu'une fois pour tout le fichier, pas
# recréé à chaque fonction de test
@pytest.fixture(scope="module")
def client():
    return Mistral(api_key=MISTRAL_API_KEY)


def test_embeddings_dimension(client):
    """mistral-embed doit renvoyer des vecteurs de 1024 dimensions (cf. doc officielle)."""
    # Appel réel à l'API d'embeddings avec la nouvelle syntaxe migrée (.embeddings.create,
    # paramètre "inputs" au pluriel) - si la migration avait raté ce serait ici que ça casserait
    resp = client.embeddings.create(model="mistral-embed", inputs=["Stephen Curry a marqué 2000 points"])
    # 1024 = dimension fixe du modèle mistral-embed, vérifiée dans la doc officielle
    assert len(resp.data[0].embedding) == 1024


def test_chat_completion(client):
    """chat.complete() doit renvoyer une réponse texte exploitable."""
    # Nouvelle syntaxe migrée : .chat.complete() (avant : .chat()), messages en dict
    # simple (avant : objet ChatMessage, qui n'existe plus dans le SDK 2.x)
    resp = client.chat.complete(
        model="mistral-small-latest",
        messages=[{"role": "user", "content": "Réponds juste 'OK'"}],
        temperature=0.1,
    )
    # Vérifie juste qu'une réponse non vide revient - pas son contenu exact
    assert resp.choices[0].message.content
