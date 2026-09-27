# tests/test_vector_store.py
"""Verifie que VectorStoreManager.search() fonctionne contre l'index deja construit
dans data/vector_db/ (jamais reconstruit par ce test).

Appelle la vraie API Mistral (facture, cout negligeable) : jamais lance par un
simple `pytest`, seulement via `pytest -m api`.
"""
import pytest

from rag.vector_store import VectorStoreManager

pytestmark = pytest.mark.api


# scope="module" : un seul chargement de l'index Faiss pour tout le fichier,
# pas un rechargement disque a chaque fonction de test
@pytest.fixture(scope="module")
def vector_store():
    # Le constructeur charge l'index existant depuis data/vector_db/, il ne le
    # reconstruit jamais tout seul (build_index() est une methode separee)
    return VectorStoreManager()


def test_index_loaded(vector_store):
    """L'index existant (data/vector_db/) doit etre charge, pas reconstruit."""
    assert vector_store.index is not None
    assert vector_store.index.ntotal > 0
    # Autant de chunks que de vecteurs : un decalage rendrait les metadonnees fausses
    assert len(vector_store.document_chunks) == vector_store.index.ntotal


def test_search_returns_plausible_results(vector_store):
    """Une recherche renvoie k chunks, sous la forme de dictionnaires bruts."""
    # search() fait un vrai appel API (embedding de la requete) puis interroge
    # l'index Faiss local
    results = vector_store.search("Combien de points Nikola Jokic a-t-il marques ?", k=3)
    assert len(results) == 3
    for r in results:
        # Format consomme tel quel par rag/generation.py : aucune validation en amont,
        # c'est le formatage du contexte qui suppose ces cles.
        assert set(r) >= {"score", "text", "metadata"}
        # score = similarite en pourcentage (0-100, voir search())
        assert 0 <= r["score"] <= 100
        assert r["text"]
    # Tries par score decroissant : le prompt presente les chunks dans cet ordre
    assert [r["score"] for r in results] == sorted((r["score"] for r in results), reverse=True)


def test_generate_embeddings_batch(vector_store):
    """_generate_embeddings() est le chemin utilise par build_index() (donc par
    scripts/index.py), different de search() qui embed une seule question."""
    fake_chunks = [
        {"id": "0_0", "text": "Stephen Curry a marque 2000 points", "metadata": {"source": "test.xlsx"}},
        {"id": "0_1", "text": "Nikola Jokic a pris 800 rebonds", "metadata": {"source": "test.xlsx"}},
    ]
    embeddings = vector_store._generate_embeddings(fake_chunks)
    assert embeddings is not None
    # 2 chunks en entree -> 2 vecteurs de dimension 1024 (mistral-embed)
    assert embeddings.shape == (2, 1024)
