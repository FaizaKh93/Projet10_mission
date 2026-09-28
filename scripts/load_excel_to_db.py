# scripts/load_excel_to_db.py
"""Charge le classeur NBA dans SQLite : lecture, validation Pydantic, insertion.

Trois étapes, dans cet ordre : pandas lit le classeur, `LigneStats` valide chaque
ligne, SQLAlchemy insère. La validation n'est pas décorative — les tables sont
déclarées STRICT, et une ligne aberrante bloquerait l'insertion avec un message
illisible au lieu d'être écartée avec sa raison.

Une ligne fautive est **écartée**, les autres continuent : une anomalie ponctuelle ne
doit pas priver la base des 568 autres joueurs.
"""
import argparse
import datetime
import logging
import re
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import pandas as pd
from pydantic import ValidationError
from sqlalchemy.orm import Session

from db_models import Base, Player, Report, Stat, Team, creer_engine
from schemas import LigneEquipe, LigneStats

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Un en-tete lu comme un horaire : HH:MM ou HH:MM:SS
HORAIRE = re.compile(r"\d{1,2}:\d{2}(:\d{2})?")

# Correspondance colonne du classeur -> champ de la table `stats`. Elle est aussi
# rappelée en bout de chaque ligne de src/db_models.py.
COLONNES = {
    "Age": "age", "GP": "games_played", "W": "wins_total", "L": "losses_total",
    "Min": "minutes_per_game", "PTS": "pts_total", "FGM": "fgm_total", "FGA": "fga_total",
    "FG%": "fg_pct", "3PM": "three_pm_total", "3PA": "three_pa_total", "3P%": "three_p_pct",
    "FTM": "ftm_total", "FTA": "fta_total", "FT%": "ft_pct",
    "OREB": "oreb_total", "DREB": "dreb_total", "REB": "reb_total",
    "AST": "ast_total", "TOV": "tov_total", "STL": "stl_total", "BLK": "blk_total",
    "PF": "pf_total", "FP": "fp_total", "DD2": "dd2_total", "TD3": "td3_total",
    "+/-": "plus_minus_per_game", "OFFRTG": "offrtg", "DEFRTG": "defrtg", "NETRTG": "netrtg",
    "AST%": "ast_pct", "AST/TO": "ast_to", "AST RATIO": "ast_ratio",
    "OREB%": "oreb_pct", "DREB%": "dreb_pct", "REB%": "reb_pct", "TO RATIO": "to_ratio",
    "EFG%": "efg_pct", "TS%": "ts_pct", "USG%": "usg_pct",
    "PACE": "pace", "PIE": "pie", "POSS": "poss_total",
}

def lire_donnees_nba(chemin: Path) -> pd.DataFrame:
    """Lit la feuille « Données NBA » et répare l'en-tête corrompu.

    Le vrai en-tête est en deuxième ligne. Excel a lu « 3PM » comme l'horaire « 3 PM »
    et l'a stocké en `datetime.time(15, 0)` : seul le NOM est perdu, les valeurs sont
    intactes. Huit colonnes `Unnamed` entièrement vides sont écartées au passage.

    Si la structure du classeur change, on s'arrête ici plutôt que d'ingérer des
    colonnes mal appariées.
    """
    # Seules « Données NBA » et « Equipe » portent des données primaires. Les trois
    # autres feuilles sont écartées : « Analyse » est une synthèse recalculable,
    # « Analyse Vide » est son gabarit sans valeurs, et « Dictionnaire des données »
    # décrit les colonnes — dont celle que le bug d'Excel a rendue fausse.
    df = pd.read_excel(chemin, sheet_name="Données NBA", header=1)
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]

    # Deux formes possibles selon l'outil qui a écrit le fichier : un vrai objet
    # `datetime.time` dans le classeur d'origine, la chaîne « 15:00:00 » après un
    # réenregistrement. Ne détecter que la première produirait une fausse alerte
    # « structure changée » sur un fichier pourtant identique.
    corrompues = [
        c for c in df.columns
        if isinstance(c, datetime.time) or HORAIRE.fullmatch(str(c))
    ]
    if len(corrompues) != 1:
        raise RuntimeError(
            f"Attendu exactement 1 en-tête corrompu (3PM lu comme un horaire), "
            f"trouvé {len(corrompues)}. La structure du fichier a changé."
        )
    df = df.rename(columns={corrompues[0]: "3PM"})

    manquantes = set(COLONNES) - set(df.columns)
    if manquantes:
        raise RuntimeError(f"Colonnes attendues absentes du classeur : {sorted(manquantes)}")
    return df


def charger_equipes(chemin: Path, session: Session) -> int:
    """Insère les 30 franchises depuis la feuille « Equipe »."""
    df = pd.read_excel(chemin, sheet_name="Equipe")
    df.columns = ["code", "nom"]

    for _, ligne in df.iterrows():
        equipe = LigneEquipe(code=ligne["code"], name=ligne["nom"])
        session.add(Team(code=equipe.code, name=equipe.name))

    logging.info(f"{len(df)} équipes insérées.")
    return len(df)


def charger_joueurs_et_stats(df: pd.DataFrame, session: Session, saison: str) -> tuple[int, int]:
    """Insère un joueur et sa ligne de statistiques par ligne du classeur."""
    inserees, ecartees = 0, 0

    for i, ligne in df.iterrows():
        valeurs = {champ: ligne[colonne] for colonne, champ in COLONNES.items()}

        try:
            # Les 43 champs passent le contrat, pas seulement ceux porteurs d'un
            # invariant : `extra="forbid"` garantit en prime qu'aucune colonne
            # inattendue ne se glisse dans l'insertion.
            LigneStats(full_name=ligne["Player"], team_code=ligne["Team"], **valeurs)
        except ValidationError as e:
            logging.warning(f"Ligne {i} écartée — {ligne.get('Player', '?')} : {e.errors()[0]['msg']}")
            ecartees += 1
            continue

        joueur = Player(full_name=str(ligne["Player"]).strip())
        session.add(joueur)
        session.flush()  # attribue player_id avant d'insérer la ligne de statistiques
        session.add(Stat(player_id=joueur.player_id, team_code=str(ligne["Team"]).strip(),
                         season=saison, **valeurs))
        inserees += 1

    logging.info(f"{inserees} joueurs et lignes de statistiques insérés, {ecartees} écartés.")
    return inserees, ecartees


def main(excel: Path, url: str, saison: str) -> None:
    engine = creer_engine(url)
    # Base repartie de zéro : l'ingestion est rejouable, et un chargement partiel
    # laisserait une base à moitié peuplée plus difficile à diagnostiquer.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    logging.info(f"Schéma créé dans {url}")

    df = lire_donnees_nba(excel)
    logging.info(f"{len(df)} lignes lues dans {excel.name}")

    with Session(engine) as session:
        charger_equipes(excel, session)
        # Les équipes d'abord : `stats.team_code` les référence, et les clés étrangères
        # sont vérifiées à l'insertion.
        session.flush()
        inserees, ecartees = charger_joueurs_et_stats(df, session, saison)
        session.commit()

    if ecartees:
        logging.warning(f"{ecartees} ligne(s) écartée(s) — voir les messages ci-dessus.")
    logging.info(f"Terminé : {inserees} lignes de statistiques dans la base.")


if __name__ == "__main__":
    RACINE = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Charge le classeur NBA dans SQLite")
    parser.add_argument("--excel", type=Path, default=RACINE / "data" / "inputs" / "regular NBA.xlsx")
    parser.add_argument("--db", type=str, default=f"sqlite:///{RACINE / 'data' / 'nba.db'}")
    # Le classeur ne porte aucune date : la saison est déclarée ici, pas devinée.
    parser.add_argument("--saison", type=str, default="2024-25")
    args = parser.parse_args()
    main(args.excel, args.db, args.saison)
