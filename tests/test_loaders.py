# tests/test_loaders.py
"""Tests de src/loading/loaders.py : extraction du texte des fichiers d'entrée.

Gratuits (aucun appel API). Ils décrivent ce que le pipeline d'indexation donne
réellement à manger à l'index, puisque c'est ce texte-là qui devient un chunk.
"""
import pandas as pd

from loading.loaders import (
    extract_text_from_csv,
    extract_text_from_excel,
    extract_text_from_txt,
    load_and_parse_files,
)


def test_excel_une_feuille_rend_du_texte_brut(tmp_path):
    """Une seule feuille : le texte est rendu directement, pas dans un dictionnaire.

    Le tableau passe par `DataFrame.to_string()` : les valeurs survivent, mais la
    structure ligne/colonne devient une mise en page à espaces, sans typage.
    """
    fichier = tmp_path / "stats.xlsx"
    pd.DataFrame({"Player": ["Nikola Jokic"], "PTS": [2072]}).to_excel(fichier, index=False)

    texte = extract_text_from_excel(str(fichier))

    assert isinstance(texte, str)
    assert "Nikola Jokic" in texte
    assert "2072" in texte


def test_excel_plusieurs_feuilles_rend_un_dictionnaire(tmp_path):
    """Plusieurs feuilles : un dictionnaire feuille -> texte, une entrée par onglet."""
    fichier = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(fichier) as writer:
        pd.DataFrame({"PTS": [2072]}).to_excel(writer, sheet_name="Joueurs", index=False)
        pd.DataFrame({"W": [64]}).to_excel(writer, sheet_name="Equipes", index=False)

    feuilles = extract_text_from_excel(str(fichier))

    assert set(feuilles) == {"Joueurs", "Equipes"}
    assert "2072" in feuilles["Joueurs"]


def test_fichier_illisible_rend_none(tmp_path):
    """Un fichier corrompu ne doit pas interrompre l'indexation : None, et on passe."""
    fichier = tmp_path / "casse.xlsx"
    fichier.write_text("ceci n'est pas un classeur", encoding="utf-8")

    assert extract_text_from_excel(str(fichier)) is None


def test_txt_et_csv_rendent_leur_contenu(tmp_path):
    """Les deux autres formats textuels du pipeline, pour couvrir leur chemin."""
    txt = tmp_path / "note.txt"
    txt.write_text("Haliburton mène les Pacers.", encoding="utf-8")
    assert "Haliburton" in extract_text_from_txt(str(txt))

    csv = tmp_path / "stats.csv"
    csv.write_text("Player,PTS\nNikola Jokic,2072\n", encoding="utf-8")
    assert "2072" in extract_text_from_csv(str(csv))


def test_extensions_restreint_le_perimetre(tmp_path):
    """Le filtre écarte les fichiers hors périmètre AVANT de les ouvrir."""
    (tmp_path / "fil.txt").write_text("Haliburton mène les Pacers.", encoding="utf-8")
    pd.DataFrame({"PTS": [2072]}).to_excel(tmp_path / "stats.xlsx", index=False)

    tout = load_and_parse_files(str(tmp_path))
    assert {d["metadata"]["filename"] for d in tout} == {"fil.txt", "stats.xlsx"}

    txt_seul = load_and_parse_files(str(tmp_path), extensions={".txt"})
    assert {d["metadata"]["filename"] for d in txt_seul} == {"fil.txt"}


def test_extensions_none_garde_tout(tmp_path):
    """Sans filtre, le comportement d'origine est inchangé."""
    (tmp_path / "fil.txt").write_text("texte", encoding="utf-8")
    assert len(load_and_parse_files(str(tmp_path), extensions=None)) == 1
