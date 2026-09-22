# tests/test_load_reports_to_db.py
"""Tests de l'ingestion des documents qualitatifs (scripts/load_reports_to_db.py).

Gratuits et rapides : l'extraction PDF elle-même est déjà couverte par
test_loaders.py, on la remplace donc ici par un texte fourni. Ce qui est testé,
c'est ce que ce script ajoute — déduction du titre, validation, insertion, et le
fait que les deux scripts d'ingestion ne s'effacent pas mutuellement.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import load_reports_to_db
from load_reports_to_db import deduire_titre, lire_documents, main
from test_load_excel_to_db import construire_excel

# Reproduit la mise en page réelle : horodatage d'impression, sujet du fil replié
# sur deux lignes, puis la navigation du site.
TEXTE = (
    "12/06/2025 13.06\n"
    "Who are teams in the\n"
    "playoffs that have impressed you ?\n"
    "rInba\n"
    "Accéder au contenu principal\n"
    "Les Pacers ont surpris tout le monde cette saison, personne ne les voyait aussi loin "
    "apres un debut de saison aussi difficile."
)


@pytest.fixture
def dossier_pdf(tmp_path, monkeypatch):
    """Deux fichiers .pdf vides + une extraction simulée : le script lit le dossier
    réellement, mais sans dépendre d'un vrai PDF ni de l'OCR."""
    for nom in ("Reddit 1.pdf", "Reddit 2.pdf"):
        (tmp_path / nom).touch()
    monkeypatch.setattr(load_reports_to_db, "extract_text_from_pdf", lambda chemin: TEXTE)
    return tmp_path


# --- Déduction du titre ---


def test_titre_ignore_l_horodatage_d_impression():
    """Le piège de ces PDF : la première ligne n'est pas le sujet du fil mais la
    date d'impression du navigateur. La prendre donnerait des titres du type
    « 12/06/2025 13.06 » — ce qui était le cas avant cette règle."""
    assert deduire_titre(TEXTE, "secours").startswith("Who are teams in the playoffs")


def test_titre_rassemble_les_lignes_repliees():
    """Le sujet est souvent coupé sur plusieurs lignes par la mise en page : il
    faut les rejoindre, sinon le titre est tronqué au milieu d'une phrase."""
    assert deduire_titre(TEXTE, "secours") == "Who are teams in the playoffs that have impressed you ?"


def test_titre_s_arrete_a_la_navigation():
    """Sans cette borne, le titre absorberait tout le corps du document."""
    assert "Pacers" not in deduire_titre(TEXTE, "secours")


def test_titre_retombe_sur_le_nom_de_fichier():
    """Sans en-tête exploitable, un titre vide serait refusé par ReportRow : on
    préfère le nom du fichier à un échec d'ingestion."""
    assert deduire_titre("12/06/2025 13.06\nrInba\n", "Reddit 3") == "Reddit 3"


# --- Lecture et validation ---


def test_lecture_produit_une_ligne_par_pdf(dossier_pdf):
    lignes = lire_documents(dossier_pdf)
    assert [l.file_name for l in lignes] == ["Reddit 1.pdf", "Reddit 2.pdf"]
    assert lignes[0].title.startswith("Who are teams in the playoffs")
    assert lignes[0].source == "Reddit"


def test_extraction_vide_arrete_l_ingestion(tmp_path, monkeypatch):
    """Un PDF illisible doit arrêter le script, pas produire une base incomplète
    sans le signaler."""
    (tmp_path / "casse.pdf").touch()
    monkeypatch.setattr(load_reports_to_db, "extract_text_from_pdf", lambda chemin: None)
    with pytest.raises(RuntimeError, match="Extraction vide"):
        lire_documents(tmp_path)


def test_extraction_residuelle_rejetee(tmp_path, monkeypatch):
    """Cas réel d'un OCR raté : quelques caractères résiduels que `if not texte`
    laisse passer. ReportRow applique le même seuil que SourceDocument."""
    (tmp_path / "scan.pdf").touch()
    monkeypatch.setattr(load_reports_to_db, "extract_text_from_pdf", lambda chemin: "\nPage 1\n")
    with pytest.raises(RuntimeError, match="rejeté par la validation"):
        lire_documents(tmp_path)


# --- Insertion ---


def test_ingestion_ecrit_les_documents(dossier_pdf, tmp_path):
    db = tmp_path / "test.db"
    main(dossier=dossier_pdf, url=f"sqlite:///{db}")
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 2
    titre, contenu = con.execute("SELECT title, content FROM reports LIMIT 1").fetchone()
    assert titre.startswith("Who are teams in the playoffs")
    assert "Pacers" in contenu


def test_ingestion_rejouable(dossier_pdf, tmp_path):
    """Relancer ne doit pas dupliquer : le script repart de sa table vidée."""
    db = tmp_path / "test.db"
    main(dossier=dossier_pdf, url=f"sqlite:///{db}")
    main(dossier=dossier_pdf, url=f"sqlite:///{db}")
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 2


# --- Indépendance des deux scripts d'ingestion ---


def test_reingestion_excel_ne_supprime_pas_les_reports(dossier_pdf, tmp_path):
    """LE test de non-régression de ce commit.

    load_excel_to_db.py faisait `drop_all()`, ce qui effaçait TOUTES les tables.
    Depuis que `reports` vient d'une autre source et d'un autre script, il ne doit
    vider que les tables dérivées de l'Excel — sinon recharger l'Excel détruirait
    silencieusement les documents qualitatifs.
    """
    from load_excel_to_db import main as charger_excel

    db = tmp_path / "test.db"
    url = f"sqlite:///{db}"

    main(dossier=dossier_pdf, url=url)  # d'abord les reports
    charger_excel(excel=construire_excel(tmp_path / "test.xlsx"), url=url, season="2024-25")

    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 2, "reports effacée"
    assert con.execute("SELECT COUNT(*) FROM stats").fetchone()[0] == 2


def test_table_matches_creee_mais_vide(dossier_pdf, tmp_path):
    """`matches` est modélisée pour répondre à la consigne, mais aucune source ne
    permet de l'alimenter : elle doit exister et rester vide."""
    db = tmp_path / "test.db"
    main(dossier=dossier_pdf, url=f"sqlite:///{db}")
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
    colonnes = {r[1] for r in con.execute("PRAGMA table_info(matches)")}
    assert {"home_team_code", "away_team_code", "played_on"} <= colonnes
