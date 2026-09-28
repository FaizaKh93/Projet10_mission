# src/db_models.py
"""Modèle relationnel des données NBA — SQLAlchemy 2.0, SQLite.

Quatre tables : `teams`, `players`, `stats`, `reports`. Le classeur ne contient que des
agrégats de saison par joueur, sans date ni adversaire : **aucune table `matches` n'est
créée**. Une table vide interrogée par un modèle qui écrit du SQL renvoie `NULL`, qu'il
présenterait volontiers comme « 0 point » — l'absence de table, elle, se voit.

Chaque colonne porte en bout de ligne sa description et son nom d'origine dans le
classeur — `pts_total` vient de `PTS`, `three_pm_total` de la colonne `3PM` que le
classeur nomme `15:00:00`. C'est cette correspondance dont le script d'ingestion a
besoin, et qu'un lecteur cherche en premier.
"""
from sqlalchemy import REAL, ForeignKey, Integer, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Tables STRICT : SQLite refuse alors une valeur dont le type ne correspond pas à la
# colonne, au lieu de la convertir en silence.
STRICT = {"sqlite_strict": True}


class Base(DeclarativeBase):
    pass


class Team(Base):
    """Les 30 franchises, depuis la feuille « Equipe »."""

    __tablename__ = "teams"
    __table_args__ = STRICT

    code: Mapped[str] = mapped_column(Text, primary_key=True)  # code de l'équipe (Code)
    name: Mapped[str] = mapped_column(Text, nullable=False)  # nom complet (Nom complet de l'équipe)


class Player(Base):
    """L'identité du joueur, stable d'une saison à l'autre.

    Ni l'équipe ni l'âge n'y figurent : ils changent d'une saison à l'autre et
    appartiennent donc à `stats`.
    """

    __tablename__ = "players"
    __table_args__ = STRICT

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # identifiant du joueur
    full_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True  # nom complet du joueur (Player)
    )


class Stat(Base):
    """Statistiques d'un joueur sur une saison, depuis la feuille « Données NBA ».

    569 lignes, une par joueur. Deux pièges documentés sur les colonnes concernées :
    la colonne `3PM` arrive corrompue du classeur, et les pourcentages ne sont pas les
    ratios des totaux voisins.
    """

    __tablename__ = "stats"
    __table_args__ = (
        UniqueConstraint("player_id", "season", name="uq_stats_player_season"),
        STRICT,
    )

    stat_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # identifiant technique
    player_id: Mapped[int] = mapped_column(ForeignKey("players.player_id"), nullable=False)  # identifiant du joueur
    team_code: Mapped[str] = mapped_column(ForeignKey("teams.code"), nullable=False)  # équipe CETTE saison (Team)
    season: Mapped[str] = mapped_column(Text, nullable=False)  # ex. 2024-25 — déduite, aucune date au classeur
    age: Mapped[int] = mapped_column(Integer, nullable=False)  # âge (Age)
    games_played: Mapped[int] = mapped_column(Integer, nullable=False)  # matchs joués (GP)

    # --- Totaux de la saison ---
    wins_total: Mapped[int] = mapped_column(Integer, nullable=False)  # victoires de l'équipe sur ces matchs (W)
    losses_total: Mapped[int] = mapped_column(Integer, nullable=False)  # défaites (L)
    pts_total: Mapped[int] = mapped_column(Integer, nullable=False)  # points marqués (PTS)
    fgm_total: Mapped[int] = mapped_column(Integer, nullable=False)  # tirs réussis (FGM)
    fga_total: Mapped[int] = mapped_column(Integer, nullable=False)  # tirs tentés (FGA)
    # En-tête corrompu à la source : Excel a lu « 3PM » comme l'horaire « 3 PM » et l'a
    # stocké en datetime.time(15, 0). Le dictionnaire du classeur a hérité du bug et
    # décrit cette colonne comme « minutes jouées après 15:00 » — statistique qui
    # n'existe pas. Seul le nom était perdu : les valeurs sont intactes.
    three_pm_total: Mapped[int] = mapped_column(Integer, nullable=False)  # tirs à 3 pts réussis — en-tête corrompu en 15:00:00
    three_pa_total: Mapped[int] = mapped_column(Integer, nullable=False)  # tirs à 3 pts tentés (3PA)
    ftm_total: Mapped[int] = mapped_column(Integer, nullable=False)  # lancers francs réussis (FTM)
    fta_total: Mapped[int] = mapped_column(Integer, nullable=False)  # lancers francs tentés (FTA)
    oreb_total: Mapped[int] = mapped_column(Integer, nullable=False)  # rebonds offensifs (OREB)
    dreb_total: Mapped[int] = mapped_column(Integer, nullable=False)  # rebonds défensifs (DREB)
    reb_total: Mapped[int] = mapped_column(Integer, nullable=False)  # rebonds totaux (REB)
    ast_total: Mapped[int] = mapped_column(Integer, nullable=False)  # passes décisives (AST)
    tov_total: Mapped[int] = mapped_column(Integer, nullable=False)  # balles perdues (TOV)
    stl_total: Mapped[int] = mapped_column(Integer, nullable=False)  # interceptions (STL)
    blk_total: Mapped[int] = mapped_column(Integer, nullable=False)  # contres (BLK)
    pf_total: Mapped[int] = mapped_column(Integer, nullable=False)  # fautes personnelles (PF)
    fp_total: Mapped[float] = mapped_column(REAL, nullable=False)  # Fantasy Points (FP)
    dd2_total: Mapped[int] = mapped_column(Integer, nullable=False)  # double-doubles (DD2)
    td3_total: Mapped[int] = mapped_column(Integer, nullable=False)  # triple-doubles (TD3)
    poss_total: Mapped[int] = mapped_column(Integer, nullable=False)  # possessions jouées (POSS)

    # --- Moyennes par match ---
    minutes_per_game: Mapped[float] = mapped_column(REAL, nullable=False)  # minutes par match (Min)
    plus_minus_per_game: Mapped[float] = mapped_column(REAL, nullable=False  # écart de score sur le terrain (+/-)
    )

    # --- Pourcentages (0-100) ---
    # Ce ne sont PAS les ratios des totaux ci-dessus : l'écart diminue quand le volume
    # augmente, signature d'une moyenne des pourcentages match par match. Ne pas
    # recalculer fg_pct comme fgm_total / fga_total — les deux valeurs sont justes,
    # elles ne mesurent pas la même chose.
    fg_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # réussite aux tirs, % (FG%)
    three_p_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # réussite à 3 pts, % (3P%)
    ft_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # réussite aux lancers francs, % (FT%)
    efg_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # tirs pondérés par les 3 pts, % (EFG%)
    ts_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # tirs et lancers francs, % (TS%)
    usg_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # part des actions utilisées, % (USG%)
    ast_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # implication dans les passes, % (AST%)
    oreb_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # rebonds offensifs captés, % (OREB%)
    dreb_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # rebonds défensifs captés, % (DREB%)
    reb_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # rebonds totaux captés, % (REB%)

    # --- Indices avancés, déjà normalisés à la source ---
    offrtg: Mapped[float] = mapped_column(REAL, nullable=False)  # points marqués / 100 possessions (OFFRTG)
    defrtg: Mapped[float] = mapped_column(REAL, nullable=False)  # points encaissés / 100 possessions (DEFRTG)
    netrtg: Mapped[float] = mapped_column(REAL, nullable=False)  # offrtg - defrtg (NETRTG)
    pace: Mapped[float] = mapped_column(REAL, nullable=False)  # possessions par 48 min (PACE)
    pie: Mapped[float] = mapped_column(REAL, nullable=False)  # impact global du joueur (PIE)
    ast_to: Mapped[float] = mapped_column(REAL, nullable=False)  # passes / balles perdues (AST/TO)
    ast_ratio: Mapped[float] = mapped_column(REAL, nullable=False)  # passes / 100 possessions (AST RATIO)
    to_ratio: Mapped[float] = mapped_column(REAL, nullable=False)  # balles perdues / 100 possessions (TO RATIO)


class Report(Base):
    """Un fil de discussion Reddit, un par fichier PDF.

    Vue relationnelle des sources qualitatives, demandée par le cahier des charges.
    Elle fait doublon avec l'index FAISS, qui contient les mêmes textes découpés et
    vectorisés — et c'est FAISS qui doit servir les questions narratives : une
    recherche `LIKE` n'a pas de sens sémantique. Cette table n'est donc pas destinée à
    l'outil SQL, et cette exclusion doit être déclarée, pas supposée.
    """

    __tablename__ = "reports"
    __table_args__ = STRICT

    report_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # identifiant du document
    title: Mapped[str] = mapped_column(Text, nullable=False)  # sujet du fil
    source: Mapped[str] = mapped_column(Text, nullable=False)  # provenance, ex. Reddit
    file_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # fichier d'origine
    content: Mapped[str] = mapped_column(Text, nullable=False)  # texte intégral extrait


# Tables que l'outil SQL aura le droit d'interroger. Déclarée ici, et non laissée à
# l'appréciation d'un prompt : `reports` double l'index vectoriel, l'exposer offrirait
# un second chemin, inférieur, vers le même contenu.
TABLES_INTERROGEABLES = ("teams", "players", "stats")


def creer_engine(url: str, echo: bool = False):
    """Crée l'engine et active les clés étrangères sur CHAQUE connexion.

    Sans ce hook, SQLite ignore les `ForeignKey` déclarées plus haut : une ligne
    référençant une équipe inexistante serait acceptée.
    """
    engine = create_engine(url, echo=echo)

    @event.listens_for(engine, "connect")
    def _activer_cles_etrangeres(dbapi_connection, connection_record):
        curseur = dbapi_connection.cursor()
        curseur.execute("PRAGMA foreign_keys = ON")
        curseur.close()

    return engine
