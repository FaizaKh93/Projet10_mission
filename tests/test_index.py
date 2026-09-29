# tests/test_index.py
"""Tests du script d'indexation (`scripts/index.py`).

Gratuits : les deux étapes coûteuses — l'OCR du chargement et les embeddings de la
construction d'index — sont remplacées par des simulacres. Ce qui reste, et qui est
l'objet de ces tests, c'est l'**orchestration** : quand indexer, quand s'arrêter, et
avec quel périmètre.

Le test le plus important est celui du périmètre. `EXTENSIONS_INDEXEES` vit dans
`config.py`, et l'indexation doit l'appliquer sans jamais la redéfinir : une liste
écrite en dur ici déferait en silence la décision qui a retiré le classeur de l'index.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import index as script_index
from config import EXTENSIONS_INDEXEES

DOCUMENTS = [
    {"page_content": "Wolves by far, they took the champs to the brink.",
     "metadata": {"source": "Reddit 1.pdf"}},
]


class IndexFactice:
    """Remplace VectorStoreManager : enregistre ce qu'on lui demande de construire."""

    def __init__(self, ntotal: int = 42):
        self.documents_construits = None
        self.index = type("FaissFactice", (), {"ntotal": ntotal})() if ntotal else None

    def build_index(self, documents):
        self.documents_construits = documents


@pytest.fixture
def indexation(monkeypatch):
    """Pose les simulacres et rend de quoi observer ce que le script a fait."""
    observe = {"extensions": None, "repertoire": None, "url": None, "store": IndexFactice()}

    def faux_chargement(repertoire, extensions=None):
        observe["repertoire"] = repertoire
        observe["extensions"] = extensions
        return observe.get("documents", DOCUMENTS)

    monkeypatch.setattr(script_index, "load_and_parse_files", faux_chargement)
    monkeypatch.setattr(script_index, "VectorStoreManager", lambda: observe["store"])
    return observe


# --- Le périmètre d'indexation ------------------------------------------------------


def test_le_perimetre_vient_de_la_configuration(indexation):
    """La propriété qui protège la décision de ne pas indexer le classeur.

    `EXTENSIONS_INDEXEES` est déclaré dans `config.py` ; l'indexation l'applique, elle
    ne le choisit pas. Une liste écrite en dur ici serait invisible à la lecture du
    fichier de configuration, et le classeur reviendrait dans l'index sans un mot.
    """
    script_index.run_indexing("data/inputs")

    assert indexation["extensions"] == EXTENSIONS_INDEXEES
    assert indexation["extensions"] is EXTENSIONS_INDEXEES, "le périmètre a été recopié"


def test_le_repertoire_demande_est_celui_qui_est_lu(indexation):
    script_index.run_indexing("un/autre/dossier")

    assert indexation["repertoire"] == "un/autre/dossier"


# --- Le chemin nominal --------------------------------------------------------------


def test_les_documents_charges_sont_ceux_qui_sont_indexes(indexation):
    script_index.run_indexing("data/inputs")

    assert indexation["store"].documents_construits == DOCUMENTS


# --- Les arrêts anticipés -----------------------------------------------------------


def test_sans_document_aucun_index_n_est_construit(indexation):
    """Construire un index vide écraserait l'index existant par du vide — une panne
    d'OCR suffirait à effacer le travail d'indexation précédent."""
    indexation["documents"] = []

    script_index.run_indexing("data/inputs")

    assert indexation["store"].documents_construits is None


def test_un_telechargement_en_echec_arrete_tout(indexation, monkeypatch):
    """Mieux vaut ne rien indexer qu'indexer des données incomplètes : on ne peut pas
    distinguer après coup un index partiel d'un index complet."""
    monkeypatch.setattr(script_index, "download_and_extract_zip", lambda url, rep: False)

    script_index.run_indexing("data/inputs", data_url="https://exemple/inputs.zip")

    assert indexation["extensions"] is None, "le chargement a eu lieu malgré l'échec"
    assert indexation["store"].documents_construits is None


def test_un_telechargement_reussi_poursuit_l_indexation(indexation, monkeypatch):
    appels = []
    monkeypatch.setattr(
        script_index, "download_and_extract_zip",
        lambda url, rep: appels.append((url, rep)) or True,
    )

    script_index.run_indexing("data/inputs", data_url="https://exemple/inputs.zip")

    assert appels == [("https://exemple/inputs.zip", "data/inputs")]
    assert indexation["store"].documents_construits == DOCUMENTS


def test_sans_url_aucun_telechargement(indexation, monkeypatch):
    def interdit(*a, **k):
        raise AssertionError("téléchargement déclenché sans URL fournie")

    monkeypatch.setattr(script_index, "download_and_extract_zip", interdit)

    script_index.run_indexing("data/inputs")

    assert indexation["store"].documents_construits == DOCUMENTS


# --- L'index final ------------------------------------------------------------------


def test_un_index_vide_apres_construction_est_signale(indexation, caplog):
    """`build_index` peut rendre un index nul — un lot d'embeddings en échec, par
    exemple. Le script doit le dire plutôt que d'annoncer un succès."""
    indexation["store"] = IndexFactice(ntotal=0)

    with caplog.at_level("WARNING"):
        script_index.run_indexing("data/inputs")

    assert any("pas pu être créé" in m for m in caplog.messages)
