# tests/test_vector_store.py
"""Vérifie que VectorStoreManager.search() fonctionne toujours après la migration
du client Mistral, contre l'index déjà construit sur data/vector_db/ (jamais
reconstruit par ce test).

Appelle la vraie API Mistral (facturé, coût négligeable) : jamais lancé par un
simple `pytest`, seulement via `pytest -m api`.
"""
import pytest

from rag.vector_store import VectorStoreManager
from schemas import SearchResult

pytestmark = pytest.mark.api


# scope="module" : un seul chargement de l'index Faiss (302 vecteurs) pour tout
# le fichier, pas un rechargement disque à chaque fonction de test
@pytest.fixture(scope="module")
def vector_store():
    # Le constructeur charge l'index existant depuis data/vector_db/, il ne le
    # reconstruit jamais tout seul (build_index() est une méthode séparée)
    return VectorStoreManager()


def test_index_loaded(vector_store):
    """L'index existant (data/vector_db/) doit être chargé, pas reconstruit."""
    # Vérifie juste que le chargement depuis disque a fonctionné
    assert vector_store.index is not None
    assert vector_store.index.ntotal > 0


def test_search_returns_plausible_results(vector_store):
    """Une recherche doit renvoyer k chunks avec un score de similarité valide."""
    # search() fait un vrai appel API (embedding de la requête) puis interroge
    # l'index Faiss local - donc teste le client migré ET la recherche ensemble
    results = vector_store.search("Combien de points Nikola Jokić a-t-il marqués ?", k=3)
    assert len(results) == 3
    for r in results:
        # search() renvoie des SearchResult validés, plus des dict bruts
        assert isinstance(r, SearchResult)
        # score = similarité en pourcentage (0-100%, voir vector_store.py:search())
        assert 0 <= r.score <= 100
        assert r.text
        # id = chunk_id, indispensable à RAGAnswer.citations et à leur vérification
        assert r.id


def test_generate_embeddings_batch(vector_store):
    """_generate_embeddings() est le chemin de code utilisé par build_index() (donc
    par scripts/index.py lors d'une indexation) - différent de search() qui embed
    une seule question. Jamais exercé par les deux tests précédents."""
    # Chunks factices au format réel produit par _split_documents_to_chunks() :
    # id + text + metadata. _generate_embeddings() lit "text" pour l'appel API et
    # "id" pour valider chaque vecteur via EmbeddedChunk.
    fake_chunks = [
        {"id": "0_0", "text": "Stephen Curry a marqué 2000 points", "metadata": {"source": "test.xlsx"}},
        {"id": "0_1", "text": "Nikola Jokić a pris 800 rebonds", "metadata": {"source": "test.xlsx"}},
    ]
    # Appel réel via la syntaxe migrée (.embeddings.create, "inputs") à l'intérieur
    # de _generate_embeddings() - ne touche jamais à l'index déjà chargé sur disque
    embeddings = vector_store._generate_embeddings(fake_chunks)
    assert embeddings is not None
    # 2 chunks en entrée -> 2 vecteurs en sortie, chacun de dimension 1024 (mistral-embed)
    assert embeddings.shape == (2, 1024)
