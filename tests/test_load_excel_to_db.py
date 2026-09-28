# tests/test_load_excel_to_db.py
"""Tests du pipeline d'ingestion du classeur.

Gratuits : un classeur minimal est fabriqué à la volée, la base vit en mémoire.

Ce qui est vérifié ici, c'est ce qui casserait en silence : un en-tête de classeur qui
change, une ligne aberrante insérée au lieu d'être écartée, une clé étrangère qui ne
protège rien.
"""
import datetime
import sys
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import load_excel_to_db as ingestion
from db_models import Base, Player, Stat, Team, creer_engine

# Une ligne complète et valide, indexée par le nom de colonne du classeur
LIGNE = {
    "Player": "Nikola Jokic", "Team": "DEN", "Age": 29, "GP": 70, "W": 40, "L": 30,
    "Min": 36.7, "PTS": 2072, "FGM": 800, "FGA": 1400, "FG%": 57.6,
    "3PA": 200, "3P%": 41.7, "FTM": 400, "FTA": 500, "FT%": 80.0,
    "OREB": 200, "DREB": 600, "REB": 780, "AST": 700, "TOV": 250, "STL": 100,
    "BLK": 50, "PF": 180, "FP": 3500.0, "DD2": 50, "TD3": 25,
    "+/-": 8.5, "OFFRTG": 120.0, "DEFRTG": 112.0, "NETRTG": 8.0,
    "AST%": 40.0, "AST/TO": 2.8, "AST RATIO": 30.0,
    "OREB%": 7.0, "DREB%": 25.0, "REB%": 16.0, "TO RATIO": 12.0,
    "EFG%": 60.0, "TS%": 65.0, "USG%": 29.0, "PACE": 98.0, "PIE": 20.0, "POSS": 5000,
}
COLONNE_3PM = datetime.time(15, 0)  # l'en-tête que le classeur porte réellement


def ecrire_classeur(chemin, lignes, equipes=(("DEN", "Denver Nuggets"),)):
    """Reproduit la structure du vrai classeur : en-tête en deuxième ligne, colonne
    `3PM` nommée par un horaire, et colonnes `Unnamed` vides."""
    colonnes = list(LIGNE)
    colonnes.insert(colonnes.index("3PA"), COLONNE_3PM)
    donnees = [{**l, COLONNE_3PM: l.get("3PM", 100)} for l in lignes]
    df = pd.DataFrame(donnees)[colonnes]
    df["Unnamed: 45"] = None  # colonnes vides présentes dans le vrai fichier

    with pd.ExcelWriter(chemin) as writer:
        # startrow=1 : le vrai en-tête est en deuxième ligne
        df.to_excel(writer, sheet_name="Données NBA", index=False, startrow=1)
        pd.DataFrame(equipes, columns=["Code", "Nom"]).to_excel(
            writer, sheet_name="Equipe", index=False
        )
    return chemin


@pytest.fixture
def base():
    engine = creer_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


# --- Lecture du classeur ------------------------------------------------------------


def test_len_tete_corrompu_est_repare(tmp_path):
    """La colonne `3PM` arrive nommée `15:00:00`. Sans réparation, la correspondance
    des colonnes échouerait — et `15_00_00` ne serait pas un identifiant SQL valide."""
    df = ingestion.lire_donnees_nba(ecrire_classeur(tmp_path / "c.xlsx", [LIGNE]))

    assert "3PM" in df.columns
    assert not any(isinstance(c, datetime.time) for c in df.columns)
    assert not any(str(c).startswith("Unnamed") for c in df.columns)


def test_structure_inattendue_arrete_l_ingestion(tmp_path):
    """Si le classeur change, mieux vaut s'arrêter qu'ingérer des colonnes mal
    appariées : l'erreur serait alors invisible jusqu'à une réponse fausse."""
    df = pd.DataFrame([{**LIGNE, "3PM": 100}])
    chemin = tmp_path / "sansbug.xlsx"
    with pd.ExcelWriter(chemin) as w:
        df.to_excel(w, sheet_name="Données NBA", index=False, startrow=1)

    with pytest.raises(RuntimeError, match="en-tête corrompu"):
        ingestion.lire_donnees_nba(chemin)


def test_colonne_manquante_arrete_l_ingestion(tmp_path):
    ligne = {k: v for k, v in LIGNE.items() if k != "PTS"}
    chemin = tmp_path / "incomplet.xlsx"
    colonnes = list(ligne)
    colonnes.insert(colonnes.index("3PA"), COLONNE_3PM)
    df = pd.DataFrame([{**ligne, COLONNE_3PM: 100}])[colonnes]
    with pd.ExcelWriter(chemin) as w:
        df.to_excel(w, sheet_name="Données NBA", index=False, startrow=1)

    with pytest.raises(RuntimeError, match="Colonnes attendues absentes"):
        ingestion.lire_donnees_nba(chemin)


# --- Insertion ----------------------------------------------------------------------


def test_une_ligne_valide_est_inseree(tmp_path, base):
    chemin = ecrire_classeur(tmp_path / "c.xlsx", [LIGNE])
    df = ingestion.lire_donnees_nba(chemin)

    with Session(base) as s:
        ingestion.charger_equipes(chemin, s)
        s.flush()
        inserees, ecartees = ingestion.charger_joueurs_et_stats(df, s, "2024-25")
        s.commit()

        assert (inserees, ecartees) == (1, 0)
        stat = s.query(Stat).one()
        assert stat.pts_total == 2072 and stat.three_pm_total == 100
        assert s.query(Player).one().full_name == "Nikola Jokic"


def test_ligne_aberrante_ecartee_les_autres_passent(tmp_path, base):
    """Plus de tirs réussis que tentés : la ligne part, le reste est inséré."""
    aberrante = {**LIGNE, "Player": "Joueur Impossible", "FGM": 1500, "FGA": 1400}
    chemin = ecrire_classeur(tmp_path / "c.xlsx", [LIGNE, aberrante])
    df = ingestion.lire_donnees_nba(chemin)

    with Session(base) as s:
        ingestion.charger_equipes(chemin, s)
        s.flush()
        inserees, ecartees = ingestion.charger_joueurs_et_stats(df, s, "2024-25")
        s.commit()

    assert (inserees, ecartees) == (1, 1)
    with Session(base) as s:
        assert [p.full_name for p in s.query(Player)] == ["Nikola Jokic"]


def test_les_compteurs_sont_stockes_en_entiers(tmp_path, base):
    """Les tables STRICT refusent un flottant NON entier dans une colonne INTEGER.

    pandas rend ces colonnes en `numpy.int64`, que SQLAlchemy convertit correctement —
    vérifié en comparant deux bases construites avec et sans conversion explicite :
    valeurs et types identiques. Ce test garde la propriété, pas le moyen.
    """
    chemin = ecrire_classeur(tmp_path / "c.xlsx", [LIGNE])
    df = ingestion.lire_donnees_nba(chemin)

    with Session(base) as s:
        ingestion.charger_equipes(chemin, s)
        s.flush()
        ingestion.charger_joueurs_et_stats(df, s, "2024-25")
        s.commit()

    with base.connect() as c:
        valeur = c.execute(text("SELECT typeof(pts_total) FROM stats")).scalar()
    assert valeur == "integer"


def test_equipe_inconnue_refusee(base):
    """La clé étrangère doit être vérifiée — SQLite ne le fait pas sans le PRAGMA."""
    from sqlalchemy.exc import IntegrityError

    with Session(base) as s:
        s.add(Team(code="DEN", name="Denver Nuggets"))
        joueur = Player(full_name="Test")
        s.add(joueur)
        s.flush()
        s.add(Stat(player_id=joueur.player_id, team_code="ZZZ", season="2024-25",
                   age=25, games_played=1,
                   **{c: 0 for c in (
                       "wins_total", "losses_total", "pts_total", "fgm_total", "fga_total",
                       "three_pm_total", "three_pa_total", "ftm_total", "fta_total",
                       "oreb_total", "dreb_total", "reb_total", "ast_total", "tov_total",
                       "stl_total", "blk_total", "pf_total", "dd2_total", "td3_total",
                       "poss_total")},
                   **{c: 0.0 for c in (
                       "minutes_per_game", "plus_minus_per_game", "fg_pct", "three_p_pct",
                       "ft_pct", "efg_pct", "ts_pct", "usg_pct", "ast_pct", "oreb_pct",
                       "dreb_pct", "reb_pct", "offrtg", "defrtg", "netrtg", "pace", "pie",
                       "ast_to", "ast_ratio", "to_ratio", "fp_total")}))
        with pytest.raises(IntegrityError):
            s.commit()
