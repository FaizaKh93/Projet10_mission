# tests/test_preparer_docs.py
"""Tests de la préparation du site (`scripts/preparer_docs.py`).

Deux propriétés valent d'être figées :

1. **les figures sont calculées depuis `eval/results/`**, jamais recopiées — sinon
   elles finiraient par contredire les tableaux qu'elles illustrent ;
2. **un notebook absent n'interrompt pas la construction** — mieux vaut un site sans
   sa page d'annexe qu'un échec au moment de publier.

Gratuits : aucun appel réseau, et les figures sont écrites dans un dossier temporaire.
"""
import json
import statistics as st
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import preparer_docs as prep


@pytest.fixture
def sortie_temporaire(tmp_path, monkeypatch):
    """Redirige les écritures vers tmp_path, jamais vers docs/."""
    figures = tmp_path / "assets"
    monkeypatch.setattr(prep, "FIGURES", figures)
    monkeypatch.setattr(prep, "NOTEBOOK_COPIE", tmp_path / "notebook.ipynb")
    return tmp_path


# --- Les figures viennent des résultats ---------------------------------------------


def test_les_moyennes_viennent_des_fichiers_de_resultats():
    """Le chiffre tracé doit être celui du fichier, pas une valeur écrite à la main."""
    donnees = prep.charger_runs()

    brut = json.loads((prep.RESULTATS / "sql_routing.json").read_text(encoding="utf-8"))
    attendu = st.mean(
        c["answer_correctness"] for c in brut
        if "answer_correctness" in c and c["modalite"] == "excel"
    )

    assert prep.moyenne(donnees["sql_routing"], "answer_correctness", "excel") == attendu


def test_un_cas_non_note_ne_fausse_pas_la_moyenne():
    """Un run interrompu laisse des cas sans score. Les compter comme des zéros
    ferait chuter la moyenne sans qu'aucune réponse n'ait changé."""
    donnees = prep.charger_runs()

    for nom, cas in donnees.items():
        assert all("answer_correctness" in c for c in cas), f"{nom} contient un cas non noté"


def test_les_quatre_runs_sont_charges():
    donnees = prep.charger_runs()

    assert list(donnees) == ["baseline", "reddit_only", "pydantic_contracts", "sql_routing"]
    assert all(len(cas) > 0 for cas in donnees.values())


def test_six_figures_ecrites_deux_par_theme(sortie_temporaire):
    """Trois figures × deux thèmes : une image à fond blanc sur une page sombre se lit
    mal, et Material choisit la variante par le suffixe du lien."""
    fichiers = prep.generer_figures()

    noms = sorted(f.name for f in fichiers)
    assert noms == [
        "metriques-dark.png", "metriques-light.png",
        "metriques-par-modalite-dark.png", "metriques-par-modalite-light.png",
        "modalites-dark.png", "modalites-light.png",
    ]
    assert all(f.exists() and f.stat().st_size > 0 for f in fichiers)


def test_chaque_case_de_la_grille_a_des_donnees():
    """La grille de contrôle croise 4 runs × 4 métriques × 3 modalités.

    Une case vide ferait échouer `st.mean` à la construction de la figure — donc au
    `mkdocs build`. Et un score hors de [0, 1] invaliderait l'axe commun, qui est ce
    qui rend les quatre panneaux comparables.
    """
    donnees = prep.charger_runs()

    for nom in prep.RUNS:
        for metrique in (m for ligne in prep.GRILLE for m in ligne):
            for modalite in prep.ORDRE_MOD:
                score = prep.moyenne(donnees[nom], metrique, modalite)
                assert 0 <= score <= 1, f"{nom}/{metrique}/{modalite} vaut {score}"


# --- Le notebook ---------------------------------------------------------------------


def test_le_notebook_est_copie(sortie_temporaire):
    assert prep.copier_notebook() is True
    assert prep.NOTEBOOK_COPIE.exists()

    original = prep.NOTEBOOK_ORIGINAL.read_bytes()
    assert prep.NOTEBOOK_COPIE.read_bytes() == original, "la copie doit être fidèle"


def test_un_notebook_absent_n_interrompt_pas_la_construction(tmp_path, monkeypatch, caplog):
    """Mieux vaut publier un site sans sa page d'annexe qu'échouer au moment de publier."""
    monkeypatch.setattr(prep, "NOTEBOOK_ORIGINAL", tmp_path / "introuvable.ipynb")
    monkeypatch.setattr(prep, "NOTEBOOK_COPIE", tmp_path / "copie.ipynb")

    with caplog.at_level("WARNING"):
        assert prep.copier_notebook() is False

    assert any("introuvable" in m for m in caplog.messages)
    assert not (tmp_path / "copie.ipynb").exists()


# --- Le crochet MkDocs ---------------------------------------------------------------


def test_le_crochet_fait_les_deux(sortie_temporaire):
    """`on_pre_build` est le seul point que MkDocs appelle : il doit tout déclencher."""
    prep.on_pre_build(config={})

    assert prep.NOTEBOOK_COPIE.exists()
    assert len(list(prep.FIGURES.glob("*.png"))) == 6


def test_matplotlib_absent_ne_fait_pas_echouer_la_construction(sortie_temporaire, monkeypatch, caplog):
    """Le groupe `docs` est optionnel : sans lui, le site se bâtit sans ses figures."""
    def refuser(*a, **k):
        raise ImportError("No module named 'matplotlib'")

    monkeypatch.setattr(prep, "generer_figures", refuser)

    with caplog.at_level("WARNING"):
        prep.on_pre_build(config={})

    assert any("Figures non générées" in m for m in caplog.messages)
    assert prep.NOTEBOOK_COPIE.exists(), "le notebook doit être copié malgré tout"
