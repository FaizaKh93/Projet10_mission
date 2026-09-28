# tests/test_pipeline_contracts.py
"""Vérifie que les contrats de schemas.py sont réellement branchés dans le pipeline.

Gratuits : aucun appel API. Le client Mistral est remplacé par un double là où il
faudrait en faire un.

test_schemas.py vérifie que les modèles refusent ce qu'il faut ; ici on vérifie que le
code les appelle — un contrat défini mais jamais invoqué ne protège rien.
"""
import pickle

import faiss
import numpy as np
import pytest

from config import EMBEDDING_DIM
from loading.loaders import load_and_parse_files
from rag import vector_store as vs
from rag.vector_store import REESSAI_API, VectorStoreManager


@pytest.fixture
def manager():
    """Un manager sans passer par __init__, qui chargerait l'index du disque."""
    return VectorStoreManager.__new__(VectorStoreManager)


def document(texte, source="Reddit 1.pdf"):
    return {"page_content": texte, "metadata": {"source": source, "filename": source}}


# --- Contrat 1 : documents ----------------------------------------------------------


def test_document_residuel_ecarte_les_autres_passent(tmp_path, caplog):
    """Un fichier illisible ne doit pas empêcher d'indexer les autres."""
    (tmp_path / "bon.txt").write_text("x" * 200, encoding="utf-8")
    (tmp_path / "residuel.txt").write_text("Page 1", encoding="utf-8")  # sous le seuil

    docs = load_and_parse_files(str(tmp_path), extensions={".txt"})

    assert [d["metadata"]["filename"] for d in docs] == ["bon.txt"]
    assert "Document écarté" in caplog.text


# --- Contrat 2 : fragments ----------------------------------------------------------


def test_decoupage_rend_des_fragments_valides(manager):
    chunks = manager._split_documents_to_chunks([document("Cade a impressionné. " * 100)])

    assert chunks, "le découpage n'a produit aucun fragment"
    for c in chunks:
        assert set(c) == {"id", "text", "metadata"}
        doc, _, frag = c["id"].partition("_")
        assert doc.isdigit() and frag.isdigit()
        assert c["metadata"]["source"] == "Reddit 1.pdf"


def test_decoupage_refuse_un_document_sans_source(manager):
    """Sans source, le fragment s'afficherait « Inconnue » dans le prompt."""
    sans_source = {"page_content": "texte " * 100, "metadata": {"filename": "x.pdf"}}
    with pytest.raises(Exception, match="source"):
        manager._split_documents_to_chunks([sans_source])


# --- Contrat 3 : lot d'embeddings ---------------------------------------------------


def client_double(reponse):
    """Double du client Mistral : rend les vecteurs qu'on lui donne."""
    class Embeddings:
        def create(self, model, inputs):
            return type("R", (), {"data": [type("D", (), {"embedding": v})() for v in reponse]})()
    return type("C", (), {"embeddings": Embeddings()})()


def fragments(n):
    return [{"id": f"0_{i}", "text": f"texte {i}", "metadata": {"source": "s"}} for i in range(n)]


def test_lot_valide_passe(manager):
    manager.mistral_client = client_double([[0.1] * EMBEDDING_DIM] * 3)
    v = manager._generate_embeddings(fragments(3))
    assert v.shape == (3, EMBEDDING_DIM)


def test_lot_plus_court_interrompt(manager):
    """L'API rend moins de vecteurs que de textes : tout ce qui suit serait décalé."""
    manager.mistral_client = client_double([[0.1] * EMBEDDING_DIM] * 2)
    with pytest.raises(RuntimeError, match="embeddings invalides"):
        manager._generate_embeddings(fragments(3))


def test_vecteur_nul_interrompt(manager):
    """Le cas que la référence produisait elle-même pour « ne pas bloquer »."""
    manager.mistral_client = client_double([[0.1] * EMBEDDING_DIM, [0.0] * EMBEDDING_DIM])
    with pytest.raises(RuntimeError, match="embeddings invalides"):
        manager._generate_embeddings(fragments(2))


def test_mauvaise_dimension_interrompt(manager):
    manager.mistral_client = client_double([[0.1] * 512])
    with pytest.raises(RuntimeError, match="embeddings invalides"):
        manager._generate_embeddings(fragments(1))


# --- Contrat 4 : cohérence index / fragments au chargement --------------------------


def ecrire_index(dossier, n_vecteurs, n_fragments):
    idx = faiss.IndexFlatIP(EMBEDDING_DIM)
    idx.add(np.ones((n_vecteurs, EMBEDDING_DIM), dtype="float32"))
    chemin_idx = dossier / "i.idx"
    chemin_pkl = dossier / "c.pkl"
    faiss.write_index(idx, str(chemin_idx))
    chemin_pkl.write_bytes(pickle.dumps(fragments(n_fragments)))
    return str(chemin_idx), str(chemin_pkl)


def test_index_et_fragments_accordes(tmp_path, monkeypatch):
    i, c = ecrire_index(tmp_path, 4, 4)
    monkeypatch.setattr(vs, "FAISS_INDEX_FILE", i)
    monkeypatch.setattr(vs, "DOCUMENT_CHUNKS_FILE", c)
    m = VectorStoreManager()
    assert m.index.ntotal == 4 and len(m.document_chunks) == 4


def test_index_desaccorde_refuse(tmp_path, monkeypatch, caplog):
    """Cas réel : une indexation interrompue laisse un index et un pickle de tailles
    différentes. La référence les chargeait sans rien dire, et chaque réponse citait
    ensuite le mauvais fragment."""
    i, c = ecrire_index(tmp_path, 4, 7)
    monkeypatch.setattr(vs, "FAISS_INDEX_FILE", i)
    monkeypatch.setattr(vs, "DOCUMENT_CHUNKS_FILE", c)

    m = VectorStoreManager()

    assert m.index is None and m.document_chunks == []
    assert "désaccordés" in caplog.text


# --- Politique de réessai -----------------------------------------------------------


def test_politique_de_reessai_declaree():
    """Le SDK ne réessaie RIEN tant qu'on ne lui passe pas de configuration : son
    `retry_config` vaut UNSET par défaut. Ce test fige le fait qu'on la lui passe."""
    assert REESSAI_API.strategy == "backoff"
    assert REESSAI_API.retry_connection_errors is True
    # Le jitter évite que tous les lots repartent au même instant après un 429
    assert REESSAI_API.backoff.jitter_ms
    # Délai croissant, et un plafond pour ne pas attendre indéfiniment
    assert REESSAI_API.backoff.exponent > 1
    assert REESSAI_API.backoff.max_elapsed_time


def test_le_client_porte_la_politique(tmp_path, monkeypatch):
    i, c = ecrire_index(tmp_path, 2, 2)
    monkeypatch.setattr(vs, "FAISS_INDEX_FILE", i)
    monkeypatch.setattr(vs, "DOCUMENT_CHUNKS_FILE", c)
    m = VectorStoreManager()
    assert m.mistral_client.sdk_configuration.retry_config is REESSAI_API


def test_violation_de_contrat_nommee_dans_l_erreur(manager):
    """Un échec permanent doit être distinguable d'un échec réseau dans les logs :
    réessayer un vecteur nul ne ferait que répéter le bug."""
    manager.mistral_client = client_double([[0.0] * EMBEDDING_DIM])
    with pytest.raises(RuntimeError, match="embeddings invalides"):
        manager._generate_embeddings(fragments(1))


def test_echec_transitoire_distingue(manager):
    """Une panne réseau remonte avec un autre message, après épuisement des réessais."""
    class ClientCasse:
        class embeddings:
            @staticmethod
            def create(model, inputs):
                raise ConnectionError("503 Service Unavailable")

    manager.mistral_client = ClientCasse()
    with pytest.raises(RuntimeError, match="interrompue au lot"):
        manager._generate_embeddings(fragments(1))


# --- Combien d'appels l'API reçoit-elle réellement ? --------------------------------


def test_une_recherche_emet_exactement_un_embedding(tmp_path, monkeypatch):
    """Compté, pas déduit.

    `SEARCH_K` dit combien de voisins FAISS renvoie, il ne dit **rien** du nombre
    d'appels d'embedding. Le budget temporel d'un cas d'évaluation repose pourtant sur
    ce nombre : il doit être mesuré.
    """
    i, c = ecrire_index(tmp_path, 4, 4)
    monkeypatch.setattr(vs, "FAISS_INDEX_FILE", i)
    monkeypatch.setattr(vs, "DOCUMENT_CHUNKS_FILE", c)
    m = VectorStoreManager()

    appels = []

    class Embeddings:
        @staticmethod
        def create(model, inputs):
            appels.append(inputs)
            return type("R", (), {"data": [type("D", (), {"embedding": [0.1] * EMBEDDING_DIM})()]})()

    m.mistral_client = type("C", (), {"embeddings": Embeddings()})()
    resultats = m.search("Combien de points ?", k=3)

    assert len(appels) == 1, f"{len(appels)} appels d'embedding pour une recherche"
    assert appels[0] == ["Combien de points ?"], "un seul texte envoyé, la question"
    assert len(resultats) <= 3
