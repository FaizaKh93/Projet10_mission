# scripts/load_reports_to_db.py
"""Charge les documents qualitatifs (PDF Reddit) dans la table `reports`.

Pendant de `load_excel_to_db.py` pour l'autre source du projet : un PDF donne une
ligne, validée par Pydantic avant insertion.

**Le texte est repris de l'index vectoriel, pas recalculé.** Ces PDF sont des captures
d'écran : les lire demande un OCR d'une dizaine de minutes, déjà payé à l'indexation.
Chaque fragment de l'index porte sa position de départ dans le document, ce qui permet
de reconstituer le texte exact — les chevauchements se recouvrent. L'OCR reste en
recours pour un PDF absent de l'index.

Cette table fait doublon avec l'index FAISS, qui contient les mêmes textes découpés.
C'est volontaire, et c'est FAISS qui sert les questions narratives : une recherche
`LIKE` n'a pas de sens sémantique. `reports` n'expose les sources que sous forme
relationnelle, et **ne figure pas dans `TABLES_INTERROGEABLES`**.

Indépendant de `load_excel_to_db.py` : chacun ne vide que ses propres tables.
"""
import argparse
import logging
import pickle
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pydantic import ValidationError
from sqlalchemy.orm import Session

from config import DOCUMENT_CHUNKS_FILE, INPUT_DIR, NBA_DB_URL
from db_models import Base, Report, creer_engine
from schemas import LigneRapport

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

SOURCE = "Reddit"  # unique provenance à ce jour
LONGUEUR_MAX_TITRE = 120

# Ces PDF sont des impressions navigateur d'une page Reddit : d'abord la date
# d'impression, puis le sujet du fil (parfois replié sur plusieurs lignes), puis la
# navigation du site.
HORODATAGE_IMPRESSION = re.compile(r"^\d{2}/\d{2}/\d{4}")
NAVIGATION = re.compile(r"^(r\W?[Ii]nba|Acc|Rechercher|Se connecter)", re.IGNORECASE)


def deduire_titre(texte: str, secours: str) -> str:
    """Reconstitue le sujet du fil : ce qui précède la navigation du site.

    Prendre la première ligne ne marche pas — c'est la date d'impression. Le titre
    occupe les lignes suivantes, jusqu'au premier élément de navigation.

    Heuristique liée à cette mise en page : si elle ne donne rien, on retombe sur le
    nom du fichier plutôt que sur un titre vide, que `LigneRapport` refuserait.
    """
    lignes = [l.strip() for l in texte.splitlines() if l.strip()]
    if lignes and HORODATAGE_IMPRESSION.match(lignes[0]):
        lignes = lignes[1:]

    titre = []
    for ligne in lignes:
        if NAVIGATION.match(ligne):
            break
        titre.append(ligne)

    return " ".join(titre)[:LONGUEUR_MAX_TITRE] if titre else secours


def textes_depuis_index(chemin_fragments: Path) -> dict[str, str]:
    """Reconstitue le texte de chaque document à partir des fragments indexés.

    `start_index` donne la position du fragment dans le document d'origine : on écrit
    chacun à sa place, et le chevauchement de 150 caractères se recouvre de lui-même.
    Le résultat est le texte exact issu de l'OCR, sans le refaire.
    """
    if not chemin_fragments.exists():
        logging.warning(f"{chemin_fragments} absent : retour à l'OCR.")
        return {}

    fragments = pickle.loads(chemin_fragments.read_bytes())
    par_source: dict[str, list] = {}
    for fragment in fragments:
        par_source.setdefault(fragment["metadata"]["source"], []).append(fragment)

    textes = {}
    for source, morceaux in par_source.items():
        morceaux.sort(key=lambda m: m["metadata"]["start_index"])
        texte = ""
        for morceau in morceaux:
            debut = morceau["metadata"]["start_index"]
            if debut <= len(texte):
                texte = texte[:debut] + morceau["text"]
            else:  # trou improbable, on comble plutôt que de décaler la suite
                texte += " " * (debut - len(texte)) + morceau["text"]
        textes[source] = texte
    return textes


def lire_documents(dossier: Path, textes_indexes: dict[str, str]) -> list[LigneRapport]:
    """Une ligne validée par PDF du dossier.

    Lève si un fichier reste illisible : mieux vaut s'arrêter qu'écrire une base
    silencieusement incomplète — il n'y a que quatre documents.
    """
    lignes = []
    for chemin in sorted(dossier.glob("*.pdf")):
        texte = textes_indexes.get(chemin.name)
        origine = "index"
        if not texte:
            # Import tardif : `loaders` tire EasyOCR, donc PyTorch. Le charger sans
            # nécessité réveillerait le conflit OpenMP avec FAISS.
            from loading.loaders import extract_text_from_pdf

            logging.info(f"{chemin.name} absent de l'index : OCR (plusieurs minutes).")
            texte, origine = extract_text_from_pdf(str(chemin)), "OCR"
        if not texte:
            raise RuntimeError(f"Extraction vide pour {chemin.name} : PDF illisible ?")

        try:
            lignes.append(LigneRapport(
                title=deduire_titre(texte, chemin.stem),
                source=SOURCE,
                file_name=chemin.name,
                content=texte,
            ))
        except ValidationError as e:
            raise RuntimeError(f"{chemin.name} : document rejeté.\n{e}") from e
        logging.info(f"{chemin.name} : {len(texte)} caractères ({origine})")
    return lignes


def main(dossier: Path, url: str) -> None:
    lignes = lire_documents(dossier, textes_depuis_index(Path(DOCUMENT_CHUNKS_FILE)))
    if not lignes:
        raise RuntimeError(f"Aucun PDF trouvé dans {dossier}")

    engine = creer_engine(url)
    # On ne vide que `reports` : les tables NBA viennent de load_excel_to_db.py
    Base.metadata.drop_all(engine, tables=[Report.__table__])
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add_all(Report(**ligne.model_dump()) for ligne in lignes)
        session.commit()

    logging.info(f"{len(lignes)} document(s) écrit(s) dans {url}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Charge les PDF qualitatifs dans la table reports")
    parser.add_argument("--dossier", type=Path, default=Path(INPUT_DIR))
    parser.add_argument("--url", type=str, default=NBA_DB_URL)
    args = parser.parse_args()
    main(args.dossier, args.url)
