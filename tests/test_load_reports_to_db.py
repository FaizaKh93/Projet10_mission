# tests/test_load_reports_to_db.py
"""Tests du chargement des documents qualitatifs dans `reports`.

Gratuits, et surtout **sans OCR** : c'est précisément ce que le script évite désormais
en reprenant le texte déjà produit à l'indexation.
"""
import pickle
import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import load_reports_to_db as rapports
from db_models import Base, Report, creer_engine

# Extrait fidèle à la mise en page des PDF : date d'impression, titre du fil, puis la
# navigation du site.
TEXTE = (
    "12/06/2025 13.06\n"
    "Who are teams in the\n"
    "playoffs that have impressed you ?\n"
    "rInba\n"
    "Accéder au contenu principal\n"
    "Wolves by far, they took the champs to the brink.\n" * 4
)


def fragments(source, texte, taille=40, chevauchement=10):
    """Découpe un texte comme le fait l'indexation, chevauchement compris."""
    morceaux, debut = [], 0
    while debut < len(texte):
        morceaux.append({
            "id": f"0_{len(morceaux)}",
            "text": texte[debut:debut + taille],
            "metadata": {"source": source, "start_index": debut},
        })
        debut += taille - chevauchement
    return morceaux


# --- Reconstitution du texte depuis l'index -----------------------------------------


def test_le_texte_est_reconstitue_a_l_identique(tmp_path):
    """Le cœur du dispositif : `start_index` permet de replacer chaque fragment, et le
    chevauchement se recouvre de lui-même. Sans cela, il faudrait refaire l'OCR."""
    chemin = tmp_path / "fragments.pkl"
    chemin.write_bytes(pickle.dumps(fragments("Reddit 1.pdf", TEXTE)))

    textes = rapports.textes_depuis_index(chemin)

    assert textes["Reddit 1.pdf"] == TEXTE


def test_plusieurs_documents_sont_separes(tmp_path):
    chemin = tmp_path / "fragments.pkl"
    chemin.write_bytes(pickle.dumps(
        fragments("Reddit 1.pdf", TEXTE) + fragments("Reddit 2.pdf", "Autre fil de discussion. " * 5)
    ))

    textes = rapports.textes_depuis_index(chemin)

    assert set(textes) == {"Reddit 1.pdf", "Reddit 2.pdf"}
    assert textes["Reddit 1.pdf"] != textes["Reddit 2.pdf"]


def test_index_absent_ne_fait_pas_echouer(tmp_path):
    """Sans index, on rend un dictionnaire vide : l'appelant bascule sur l'OCR."""
    assert rapports.textes_depuis_index(tmp_path / "inexistant.pkl") == {}


# --- Titre --------------------------------------------------------------------------


def test_le_titre_saute_la_date_et_s_arrete_a_la_navigation():
    """La première ligne est la date d'impression du navigateur, pas le sujet. Le titre
    court sur les lignes suivantes, parfois repliées, jusqu'au menu du site."""
    titre = rapports.deduire_titre(TEXTE, "secours")

    assert titre == "Who are teams in the playoffs that have impressed you ?"


def test_titre_tronque_si_trop_long():
    long = "12/06/2025 13.06\n" + "mot " * 200 + "\nrInba"
    assert len(rapports.deduire_titre(long, "secours")) <= rapports.LONGUEUR_MAX_TITRE


def test_titre_de_secours_si_la_mise_en_page_change():
    """L'heuristique dépend d'une mise en page. Si elle échoue, le nom du fichier
    évite un titre vide, que `LigneRapport` refuserait."""
    assert rapports.deduire_titre("rInba\nAccéder au contenu principal", "Reddit 1") == "Reddit 1"


# --- Chargement complet -------------------------------------------------------------


def test_les_documents_sont_inseres(tmp_path, monkeypatch):
    """Chemin complet, sans OCR ni PDF réel : les fichiers servent de noms, le texte
    vient de l'index."""
    dossier = tmp_path / "inputs"
    dossier.mkdir()
    (dossier / "Reddit 1.pdf").write_bytes(b"%PDF-")

    chemin = tmp_path / "fragments.pkl"
    chemin.write_bytes(pickle.dumps(fragments("Reddit 1.pdf", TEXTE)))
    monkeypatch.setattr(rapports, "DOCUMENT_CHUNKS_FILE", str(chemin))

    url = f"sqlite:///{tmp_path / 'test.db'}"
    rapports.main(dossier, url)

    with Session(creer_engine(url)) as s:
        rapport = s.query(Report).one()
        assert rapport.file_name == "Reddit 1.pdf"
        assert rapport.source == "Reddit"
        assert "impressed you" in rapport.title
        assert "Wolves by far" in rapport.content


def test_dossier_sans_pdf_arrete_le_script(tmp_path):
    with pytest.raises(RuntimeError, match="Aucun PDF"):
        rapports.main(tmp_path, "sqlite://")


def test_extraction_vide_arrete_le_script(tmp_path, monkeypatch):
    """Mieux vaut s'arrêter qu'écrire une base silencieusement incomplète : il n'y a
    que quatre documents, chacun compte."""
    dossier = tmp_path / "inputs"
    dossier.mkdir()
    (dossier / "Reddit 9.pdf").write_bytes(b"%PDF-")

    # Absent de l'index, et l'OCR de secours ne rend rien
    monkeypatch.setitem(sys.modules, "loading.loaders",
                        type(sys)("loading.loaders"))
    sys.modules["loading.loaders"].extract_text_from_pdf = lambda p: None

    with pytest.raises(RuntimeError, match="Extraction vide"):
        rapports.lire_documents(dossier, {})
