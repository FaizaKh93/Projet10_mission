# scripts/load_excel_to_db.py
"""Ingestion : fichier Excel -> validation Pydantic -> base SQLite.

Lance-le avec :  uv run python scripts/load_excel_to_db.py

La base est entièrement régénérable depuis l'Excel : elle est donc gitignorée,
comme l'index vectoriel. Le script repart de zéro à chaque exécution.
"""
import argparse
import datetime
import logging
import sys
from pathlib import Path

import pandas as pd
from pydantic import ValidationError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import EXCEL_FILE, NBA_DB_URL, SEASON
from db_models import Base, Player, Stat, Team, creer_engine
from schemas import PlayerRow, TeamRow

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Correspondance colonne Excel -> colonne de la table `stats`.
# Les suffixes portent l'unité car le fichier source les mélange sans le dire :
# PTS est un total de saison, Min une moyenne par match, FG% un pourcentage.
COLONNES = {
    "Age": "age",  # âge du joueur
    "GP": "games_played",  # matchs joués (Games Played)
    # Totaux de la saison. Le dictionnaire du classeur annonce « par match » pour
    # PTS, FGM, FGA et 3PA : c'est faux, ce sont des totaux (PTS = 2485 pour SGA,
    # soit 32,7 par match sur 76 matchs).
    "W": "wins_total",  # victoires de l'équipe sur ces matchs
    "L": "losses_total",  # défaites
    "PTS": "pts_total",  # points marqués
    "FGM": "fgm_total",  # tirs réussis (Field Goals Made)
    "FGA": "fga_total",  # tirs tentés (Field Goals Attempted)
    # Nom rétabli par lire_donnees_nba() : dans le fichier, cet en-tête est un
    # datetime.time(15, 0) — Excel a lu « 3PM » comme l'horaire « 3 PM ».
    "3PM": "three_pm_total",  # tirs à 3 points réussis
    "3PA": "three_pa_total",  # tirs à 3 points tentés
    "FTM": "ftm_total",  # lancers francs réussis (Free Throws Made)
    "FTA": "fta_total",  # lancers francs tentés
    "OREB": "oreb_total",  # rebonds offensifs
    "DREB": "dreb_total",  # rebonds défensifs
    "REB": "reb_total",  # rebonds totaux (≠ oreb + dreb dans ce fichier)
    "AST": "ast_total",  # passes décisives (Assists)
    "TOV": "tov_total",  # balles perdues (Turnovers)
    "STL": "stl_total",  # interceptions (Steals)
    "BLK": "blk_total",  # contres (Blocks)
    "PF": "pf_total",  # fautes personnelles
    "FP": "fp_total",  # Fantasy Points
    "DD2": "dd2_total",  # double-doubles (≥10 dans 2 catégories)
    "TD3": "td3_total",  # triple-doubles (≥10 dans 3 catégories)
    "POSS": "poss_total",  # possessions jouées
    # Les deux seules vraies moyennes par match du fichier
    "Min": "minutes_per_game",  # minutes jouées par match
    "+/-": "plus_minus_per_game",  # écart de score quand le joueur est sur le terrain
    # Pourcentages 0-100. Ce ne sont PAS les ratios des totaux ci-dessus : l'écart
    # se réduit quand le volume augmente, signature d'une moyenne des pourcentages
    # match par match.
    "FG%": "fg_pct",  # réussite aux tirs
    "3P%": "three_p_pct",  # réussite à 3 points
    "FT%": "ft_pct",  # réussite aux lancers francs
    "EFG%": "efg_pct",  # Effective FG% : pondère les tirs à 3 points
    "TS%": "ts_pct",  # True Shooting % : inclut tirs et lancers francs
    "USG%": "usg_pct",  # Usage Rate : part des actions utilisées par le joueur
    "AST%": "ast_pct",  # implication dans les passes décisives
    "OREB%": "oreb_pct",  # part des rebonds offensifs disponibles captés
    "DREB%": "dreb_pct",  # idem en défensif
    "REB%": "reb_pct",  # part des rebonds totaux disponibles captés
    # Indices avancés, déjà normalisés à la source
    "OFFRTG": "offrtg",  # points marqués par 100 possessions
    "DEFRTG": "defrtg",  # points encaissés par 100 possessions
    "NETRTG": "netrtg",  # offrtg - defrtg
    "PACE": "pace",  # rythme de jeu : possessions par 48 minutes
    "PIE": "pie",  # Player Impact Estimate : impact global du joueur
    "AST/TO": "ast_to",  # ratio passes décisives / balles perdues
    "AST RATIO": "ast_ratio",  # passes décisives par 100 possessions
    "TO RATIO": "to_ratio",  # balles perdues par 100 possessions
}

# Colonnes entières : converties explicitement, car les tables STRICT refusent
# un flottant là où un INTEGER est déclaré (pandas lit tout en float64).
ENTIERS = {
    "age", "games_played", "wins_total", "losses_total", "pts_total", "fgm_total",
    "fga_total", "three_pm_total", "three_pa_total", "ftm_total", "fta_total",
    "oreb_total", "dreb_total", "reb_total", "ast_total", "tov_total", "stl_total",
    "blk_total", "pf_total", "dd2_total", "td3_total", "poss_total",
}


def lire_donnees_nba(chemin: Path) -> pd.DataFrame:
    """Lit la feuille « Données NBA » et corrige l'en-tête corrompu.

    Excel a interprété l'en-tête « 3PM » comme l'horaire « 3 PM » et l'a stocké
    en datetime.time(15, 0). Seul le NOM était perdu : les valeurs sont intactes
    (vérifié par l'invariant three_pm <= three_pa, contrôlé plus bas). Ce cas ne
    peut pas passer par la règle de renommage générale, qui produirait
    « 15_00_00 » — un identifiant SQL invalide.
    """
    df = pd.read_excel(chemin, sheet_name="Données NBA", header=1)
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]

    corrompues = [c for c in df.columns if isinstance(c, datetime.time)]
    if len(corrompues) != 1:
        raise RuntimeError(
            f"Attendu exactement 1 en-tête corrompu (3PM lu comme un horaire), trouvé {len(corrompues)}. "
            "La structure du fichier a changé : vérifier avant d'ingérer."
        )
    return df.rename(columns={corrompues[0]: "3PM"})


def charger_equipes(df_equipe: pd.DataFrame, session: Session) -> None:
    """Insère les 30 franchises, validées par TeamRow."""
    for _, ligne in df_equipe.iterrows():
        equipe = TeamRow(code=ligne["code"], name=ligne["nom"])
        session.add(Team(code=equipe.code, name=equipe.name))
    logging.info(f"{len(df_equipe)} équipes insérées.")


def charger_joueurs_et_stats(df: pd.DataFrame, session: Session, season: str) -> int:
    """Insère un joueur et sa ligne de stats par ligne du fichier.

    Une ligne invalide est écartée avec un message explicite plutôt que de faire
    échouer toute l'ingestion : même politique que le pipeline de chunking.
    """
    inserees = 0
    for i, ligne in df.iterrows():
        valeurs = {db: ligne[xl] for xl, db in COLONNES.items()}

        try:
            joueur = PlayerRow(
                full_name=ligne["Player"],
                team_code=ligne["Team"],
                # Les compteurs soumis aux invariants « réussis <= tentés » et
                # « victoires + défaites == matchs joués »
                games_played=valeurs["games_played"],
                wins_total=valeurs["wins_total"],
                losses_total=valeurs["losses_total"],
                fgm_total=valeurs["fgm_total"],
                fga_total=valeurs["fga_total"],
                three_pm_total=valeurs["three_pm_total"],
                three_pa_total=valeurs["three_pa_total"],
                ftm_total=valeurs["ftm_total"],
                fta_total=valeurs["fta_total"],
            )
        except ValidationError as e:
            logging.warning(f"Ligne {i} écartée ({ligne.get('Player', '?')}) : {e.errors()[0]['msg']}")
            continue

        # STRICT refuse un float dans une colonne INTEGER, or pandas lit tout en float64
        for champ in ENTIERS:
            valeurs[champ] = int(valeurs[champ])

        p = Player(full_name=joueur.full_name)
        session.add(p)
        session.flush()  # attribue player_id avant d'insérer la ligne de stats
        session.add(Stat(player_id=p.player_id, team_code=joueur.team_code, season=season, **valeurs))
        inserees += 1

    logging.info(f"{inserees} joueurs et lignes de stats insérés.")
    return inserees


def main(excel: Path, url: str, season: str) -> None:
    logging.info(f"Lecture de {excel}")
    df = lire_donnees_nba(excel)
    df_equipe = pd.read_excel(excel, sheet_name="Equipe", header=0)
    df_equipe.columns = ["code", "nom"]

    engine = creer_engine(url)
    Base.metadata.drop_all(engine)  # régénération complète : la base est dérivée de l'Excel
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        charger_equipes(df_equipe, session)
        session.flush()  # les équipes doivent exister avant les stats qui les référencent
        charger_joueurs_et_stats(df, session, season)
        session.commit()

    logging.info(f"Base écrite : {url}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Charge le fichier Excel NBA dans une base SQLite")
    parser.add_argument("--excel", type=Path, default=EXCEL_FILE, help="Fichier Excel source")
    parser.add_argument("--url", type=str, default=NBA_DB_URL, help="URL SQLAlchemy de la base")
    parser.add_argument("--season", type=str, default=SEASON, help="Saison associée aux lignes de stats")
    args = parser.parse_args()
    main(args.excel, args.url, args.season)
