# tests/test_numeroter_readme.py
"""Tests de la numérotation du README (`scripts/numeroter_readme.py`).

Deux propriétés valent d'être figées, parce que leur rupture serait **silencieuse** :
l'idempotence — sans elle, les numéros s'accumulent à chaque passage — et l'immunité
aux blocs de code, où un `## titre` d'exemple ne doit pas devenir une section.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import numeroter_readme as script
from numeroter_readme import numeroter


# --- La numérotation ----------------------------------------------------------------


def test_les_niveaux_s_emboitent():
    lignes, _ = numeroter(
        ["## Première", "### Une sous-partie", "#### Un détail", "### Une autre", "## Deuxième"]
    )

    assert lignes == [
        "## 1 Première",
        "### 1.1 Une sous-partie",
        "#### 1.1.1 Un détail",
        "### 1.2 Une autre",
        "## 2 Deuxième",
    ]


def test_une_nouvelle_section_remet_les_compteurs_a_zero():
    """Sans cette remise à zéro, la deuxième section commencerait à 1.3."""
    lignes, _ = numeroter(["## Première", "### A", "### B", "## Deuxième", "### C"])

    assert lignes[-1] == "### 2.1 C"


def test_relancer_ne_reempile_pas_les_numeros():
    """La propriété qui rend la numérotation tenable : on déplace une section, on
    relance, c'est tout. Sans elle, on obtiendrait « ## 1 1 Première »."""
    une_fois, _ = numeroter(["## Première", "### Une sous-partie"])
    deux_fois, _ = numeroter(une_fois)

    assert une_fois == deux_fois == ["## 1 Première", "### 1.1 Une sous-partie"]


def test_un_titre_dans_un_bloc_de_code_est_ignore():
    """Un exemple Markdown contient des `##` qui ne sont pas des sections — et un
    script Python y contient des commentaires `#`.

    Le contenu du bloc ressort **intact**, sans numéro ajouté.
    """
    entree = [
        "## Vraie section",
        "",
        "```markdown",
        "## Pas une section",
        "### Pas une sous-section",
        "```",
        "",
        "## Autre vraie section",
    ]

    lignes, reconnus = numeroter(entree)

    assert [t[2] for t in reconnus] == ["Vraie section", "Autre vraie section"]
    assert "## Pas une section" in lignes
    assert "### Pas une sous-section" in lignes


def test_une_section_hors_plan_n_est_pas_numerotee():
    """« Structure du dépôt » décrit le dépôt, elle ne fait pas partie de la
    progression du document — elle ne doit ni porter de numéro, ni en consommer un."""
    lignes, _ = numeroter(["## Structure du dépôt", "## Une vraie section"])

    assert lignes == ["## Structure du dépôt", "## 1 Une vraie section"]


# --- Le passage complet sur un fichier ----------------------------------------------


def test_relancer_le_script_rend_le_meme_fichier(tmp_path, monkeypatch):
    """Vérifié de bout en bout, écriture comprise : c'est ce passage-là qu'on relance
    après chaque remaniement du plan."""
    fichier = tmp_path / "README.md"
    fichier.write_text(
        "# Titre\n\nUne intro.\n\n## Une section\n\n### Une sous-partie\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(script, "README", fichier)

    script.main()
    apres_un = fichier.read_text(encoding="utf-8")
    script.main()

    assert "## 1 Une section" in apres_un
    assert "### 1.1 Une sous-partie" in apres_un
    assert fichier.read_text(encoding="utf-8") == apres_un
