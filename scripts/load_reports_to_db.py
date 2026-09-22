# scripts/load_reports_to_db.py
"""Charge les documents qualitatifs (PDF Reddit) dans la table `reports`.

Pendant de load_excel_to_db.py pour l'autre source du projet : un fichier PDF
donne une ligne, validée par Pydantic avant insertion.

Cette table fait volontairement doublon avec l'index FAISS, qui contient les
mêmes textes découpés et vectorisés. C'est FAISS qui sert la recherche
sémantique ; `reports` n'expose les sources que sous forme relationnelle, comme
le demande la modélisation. L'agent n'interroge pas cette table : les questions
d'opinion passent par le RAG vectoriel.

Script indépendant de load_excel_to_db.py : chacun ne vide que ses propres
tables, donc l'ordre de lancement n'a pas d'importance.
"""
import argparse
import logging
import re
import sys
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import INPUT_DIR, NBA_DB_URL
from db_models import Base, Report, creer_engine
from loading.loaders import extract_text_from_pdf
from schemas import ReportRow

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

SOURCE = "Reddit"  # unique provenance à ce jour ; à paramétrer le jour où il y en a d'autres
LONGUEUR_MAX_TITRE = 120

# Ces PDF sont des impressions navigateur d'une page Reddit. Leur en-tête suit
# toujours la même structure : date d'impression, puis le sujet du fil (souvent
# replié sur plusieurs lignes), puis les éléments de navigation du site.
HORODATAGE_IMPRESSION = re.compile(r"^\d{2}/\d{2}/\d{4}")
NAVIGATION = re.compile(r"^(r\W?[Ii]nba|Acc|Rechercher|Se connecter)", re.IGNORECASE)


def deduire_titre(texte: str, secours: str) -> str:
    """Reconstitue le sujet du fil : tout ce qui précède la navigation du site.

    Prendre simplement la première ligne ne marche pas — c'est la date
    d'impression du navigateur. Le titre occupe les lignes suivantes, parfois
    repliées, jusqu'au premier élément de navigation ; on les rejoint.

    Heuristique liée à la mise en page de ces impressions : si elle ne donne
    rien, on retombe sur le nom du fichier plutôt que sur un titre vide, que
    ReportRow refuserait.
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


def lire_documents(dossier: Path) -> list[ReportRow]:
    """Extrait et valide un ReportRow par PDF du dossier.

    Lève RuntimeError si un fichier est illisible : mieux vaut arrêter que
    d'écrire une base silencieusement incomplète.
    """
    lignes = []
    for chemin in sorted(dossier.glob("*.pdf")):
        texte = extract_text_from_pdf(str(chemin))
        if not texte:
            raise RuntimeError(f"Extraction vide pour {chemin.name} : PDF illisible ?")
        try:
            lignes.append(
                ReportRow(
                    title=deduire_titre(texte, chemin.stem),
                    source=SOURCE,
                    file_name=chemin.name,
                    content=texte,
                )
            )
        except ValidationError as e:
            raise RuntimeError(f"{chemin.name} : document rejeté par la validation.\n{e}") from e
        logging.info(f"{chemin.name} : {len(texte)} caractères")
    return lignes


def main(dossier: Path, url: str) -> None:
    lignes = lire_documents(dossier)
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
    parser.add_argument("--dossier", type=Path, default=Path(INPUT_DIR), help="Dossier des PDF")
    parser.add_argument("--url", type=str, default=NBA_DB_URL, help="URL SQLAlchemy de la base")
    args = parser.parse_args()
    main(args.dossier, args.url)
