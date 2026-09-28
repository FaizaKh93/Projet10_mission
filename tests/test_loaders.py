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


# --- PDF, DOCX, ZIP : les formats que le pipeline sait lire --------------------------


def pdf_avec_texte(chemin, texte):
    """Fabrique un vrai PDF contenant du texte, via PyMuPDF."""
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 100), texte)
    doc.save(str(chemin))
    doc.close()
    return str(chemin)


def test_pdf_texte_extrait_sans_ocr(tmp_path, monkeypatch):
    """Un PDF qui porte assez de texte ne doit PAS déclencher l'OCR — sinon chaque
    indexation paierait des dizaines de secondes de modèle pour rien."""
    appels = []
    monkeypatch.setattr(loaders, "extract_text_from_pdf_with_ocr", lambda p: appels.append(p))

    chemin = pdf_avec_texte(tmp_path / "long.pdf", "Cade Cunningham mène les Pistons. " * 10)
    texte = loaders.extract_text_from_pdf(chemin)

    assert "Cade Cunningham" in texte
    assert appels == []


def test_pdf_pauvre_en_texte_bascule_sur_l_ocr(tmp_path, monkeypatch):
    """Sous 100 caractères, le pipeline suppose un PDF scanné et tente l'OCR.

    C'est le cas de nos quatre fils Reddit, qui sont des captures d'écran.
    """
    monkeypatch.setattr(loaders, "extract_text_from_pdf_with_ocr", lambda p: "texte venu de l'OCR")

    chemin = pdf_avec_texte(tmp_path / "court.pdf", "Trop court.")
    assert loaders.extract_text_from_pdf(chemin) == "texte venu de l'OCR"


def test_pdf_illisible_tente_l_ocr_puis_abandonne(tmp_path, monkeypatch):
    """Un fichier corrompu ne doit pas interrompre l'indexation : None, et on passe."""
    monkeypatch.setattr(loaders, "extract_text_from_pdf_with_ocr", lambda p: None)

    fichier = tmp_path / "casse.pdf"
    fichier.write_text("ceci n'est pas un PDF", encoding="utf-8")
    assert loaders.extract_text_from_pdf(str(fichier)) is None


def test_docx_extrait_ses_paragraphes(tmp_path):
    import docx

    d = docx.Document()
    d.add_paragraph("Haliburton mène les Pacers.")
    d.add_paragraph("Deuxième paragraphe.")
    chemin = tmp_path / "note.docx"
    d.save(str(chemin))

    texte = loaders.extract_text_from_docx(str(chemin))
    assert "Haliburton" in texte and "Deuxième paragraphe." in texte


def test_zip_telecharge_et_extrait(tmp_path, monkeypatch):
    """Le téléchargement est simulé : aucun accès réseau dans les tests."""
    import io
    import zipfile

    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as z:
        z.writestr("fil.txt", "contenu du fil")

    class Reponse:
        content = tampon.getvalue()
        def raise_for_status(self): pass

    monkeypatch.setattr(loaders.requests, "get", lambda url, stream=False: Reponse())

    assert loaders.download_and_extract_zip("http://exemple/inputs.zip", str(tmp_path))
    assert (tmp_path / "fil.txt").read_text(encoding="utf-8") == "contenu du fil"


def test_zip_sans_url_refuse(tmp_path):
    assert loaders.download_and_extract_zip("", str(tmp_path)) is False


def test_zip_invalide_refuse(tmp_path, monkeypatch):
    class Reponse:
        content = b"ceci n'est pas une archive"
        def raise_for_status(self): pass

    monkeypatch.setattr(loaders.requests, "get", lambda url, stream=False: Reponse())
    assert loaders.download_and_extract_zip("http://exemple/x.zip", str(tmp_path)) is False


def test_format_non_supporte_ignore(tmp_path):
    """Un format inconnu est sauté avec un avertissement, pas une exception."""
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "bon.txt").write_text(TEXTE_REALISTE, encoding="utf-8")

    docs = load_and_parse_files(str(tmp_path))
    assert [d["metadata"]["filename"] for d in docs] == ["bon.txt"]


def test_repertoire_inexistant_rend_une_liste_vide():
    assert load_and_parse_files("dossier/qui/n/existe/pas") == []


def test_ocr_indisponible_rend_none(monkeypatch):
    """Si easyocr manque, `get_ocr_reader()` doit rendre None et laisser l'appelant
    gérer, plutôt que de faire échouer l'import du module."""
    import builtins

    monkeypatch.setattr(loaders, "_ocr_reader", None)
    vrai_import = builtins.__import__

    def import_sans_easyocr(nom, *a, **k):
        if nom == "easyocr":
            raise ImportError("easyocr non installé")
        return vrai_import(nom, *a, **k)

    monkeypatch.setattr(builtins, "__import__", import_sans_easyocr)
    assert loaders.get_ocr_reader() is None
