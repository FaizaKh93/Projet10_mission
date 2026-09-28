# tests/test_loaders.py
"""Tests de src/loading/loaders.py : extraction du texte des fichiers d'entrée.

Gratuits (aucun appel API). Ils décrivent ce que le pipeline d'indexation donne
réellement à manger à l'index, puisque c'est ce texte-là qui devient un chunk.
"""
import subprocess
import sys
from pathlib import Path

import pandas as pd

from loading import loaders
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


# Le contrat SourceDocument écarte les extractions résiduelles : les fixtures ci-dessous
# doivent dépasser MIN_CARACTERES_DOCUMENT pour tester le filtre d'extensions, et non
# la longueur.
TEXTE_REALISTE = "Haliburton mène les Pacers, et le fil de discussion en parle longuement."


def test_extensions_restreint_le_perimetre(tmp_path):
    """Le filtre écarte les fichiers hors périmètre AVANT de les ouvrir."""
    (tmp_path / "fil.txt").write_text(TEXTE_REALISTE, encoding="utf-8")
    pd.DataFrame({"Joueur": ["Tyrese Haliburton"] * 5, "PTS": [2072] * 5}).to_excel(
        tmp_path / "stats.xlsx", index=False
    )

    tout = load_and_parse_files(str(tmp_path))
    assert {d["metadata"]["filename"] for d in tout} == {"fil.txt", "stats.xlsx"}

    txt_seul = load_and_parse_files(str(tmp_path), extensions={".txt"})
    assert {d["metadata"]["filename"] for d in txt_seul} == {"fil.txt"}


def test_extensions_none_garde_tout(tmp_path):
    """Sans filtre, le comportement d'origine est inchangé."""
    (tmp_path / "fil.txt").write_text(TEXTE_REALISTE, encoding="utf-8")
    assert len(load_and_parse_files(str(tmp_path), extensions=None)) == 1


def test_import_ne_charge_pas_torch():
    """Invariant bloquant, vérifié dans un processus neuf.

    FAISS et PyTorch embarquent chacun leur runtime OpenMP. Chargés ensemble, OpenMP
    avorte l'interpréteur (« OMP: Error #15 », puis « Fatal Python error: Aborted »).
    Comme pytest importe tous les modules de test à la collecte, un `import easyocr` au
    niveau de loaders.py suffisait à faire planter `pytest -m api`.

    Le test tourne dans un sous-processus : `sys.modules` est partagé par toute une
    session pytest, donc l'interroger ici ne prouverait rien sur ce que CE module tire.
    """
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "import loading.loaders;"
        "print('torch' in sys.modules or 'easyocr' in sys.modules)"
    )
    sortie = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert sortie.stdout.strip().endswith("False"), sortie.stdout + sortie.stderr


def test_le_lecteur_ocr_est_paresseux():
    """Le modèle n'est pas construit tant qu'aucun PDF n'est traité."""
    assert loaders._ocr_reader is None


def test_excel_ne_declenche_pas_ocr(tmp_path, monkeypatch):
    """Traiter un classeur ne doit jamais toucher à l'OCR."""
    appels = []
    monkeypatch.setattr(loaders, "get_ocr_reader", lambda: appels.append(1))

    fichier = tmp_path / "test.xlsx"
    pd.DataFrame({"Joueur": ["Nikola Jokic"] * 5, "PTS": [2072] * 5}).to_excel(fichier, index=False)

    texte = extract_text_from_excel(str(fichier))
    assert "2072" in texte
    assert appels == []  # OCR jamais sollicité
