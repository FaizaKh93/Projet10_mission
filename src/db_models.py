# src/db_models.py
"""Modèles SQLAlchemy de la base NBA (SQLite).

Deux garde-fous spécifiques à SQLite, sans lesquels le schéma serait décoratif :

- **tables STRICT** : par défaut SQLite n'applique pas les types déclarés. Un
  'N/A' inséré dans une colonne INTEGER est accepté, et fausse ensuite tous les
  SUM/MAX sans le moindre avertissement.
- **PRAGMA foreign_keys = ON** : par défaut SQLite n'applique pas non plus les
  clés étrangères. Le pragma doit être posé à chaque connexion (voir
  `creer_engine`), pas une fois pour toutes dans le fichier.

STRICT n'autorise que INTEGER, REAL, TEXT, BLOB et ANY : on utilise donc Text et
REAL plutôt que String et Float, dont le DDL (VARCHAR, FLOAT) serait refusé.

Conventions de nommage des colonnes de stats, imposées par le fichier source qui
mélange les unités sans le documenter :
    _total     agrégat de la saison      (pts_total = 2485 pour SGA)
    _per_game  moyenne par match         (minutes_per_game = 34.2)
    _pct       pourcentage 0-100         (fg_pct = 51.9)
    sans suffixe : indice avancé déjà normalisé (offrtg, pie, pace)

Les descriptions en commentaire reprennent la feuille « Dictionnaire des données »
du classeur, **corrigée sur trois points vérifiés** :
    - elle annonce « en moyenne par match » pour PTS, FGM, FGA, 3PA, qui sont en
      réalité des totaux de saison (PTS = 2485 pour SGA, soit 32,7 par match) ;
    - elle décrit la colonne à l'en-tête corrompu comme « Minutes jouées après
      15:00 de jeu », une statistique qui n'existe pas — c'est 3PM ;
    - seules Min et +/- sont effectivement des moyennes par match.
"""
from sqlalchemy import REAL, ForeignKey, Integer, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STRICT = {"sqlite_strict": True}


class Base(DeclarativeBase):
    pass


class Team(Base):
    """Les 30 franchises, depuis la feuille « Equipe »."""

    __tablename__ = "teams"
    __table_args__ = STRICT

    code: Mapped[str] = mapped_column(Text, primary_key=True)  # code de l'équipe
    name: Mapped[str] = mapped_column(Text, nullable=False)  # nom complet de l'équipe


class Player(Base):
    """L'identité du joueur, stable d'une saison à l'autre.

    Ni l'équipe ni l'âge ne figurent ici : ils changent d'une saison à l'autre et
    appartiennent donc à `stats`.
    """

    __tablename__ = "players"
    __table_args__ = STRICT

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class Stat(Base):
    """Statistiques d'un joueur sur une saison (feuille « Données NBA »)."""

    __tablename__ = "stats"
    __table_args__ = (
        # Interdit deux lignes pour le même joueur et la même saison
        UniqueConstraint("player_id", "season", name="uq_stats_player_season"),
        STRICT,
    )

    stat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.player_id"), nullable=False)  # -> players
    team_code: Mapped[str] = mapped_column(ForeignKey("teams.code"), nullable=False)  # -> teams, équipe CETTE saison
    season: Mapped[str] = mapped_column(Text, nullable=False)  # '2024-25' (déduit, cf. config.SEASON)
    age: Mapped[int] = mapped_column(Integer, nullable=False)  # âge du joueur
    games_played: Mapped[int] = mapped_column(Integer, nullable=False)  # matchs joués (GP)

    # --- Totaux de la saison ---
    wins_total: Mapped[int] = mapped_column(Integer, nullable=False)  # victoires de l'équipe sur ces matchs (W)
    losses_total: Mapped[int] = mapped_column(Integer, nullable=False)  # défaites (L)
    pts_total: Mapped[int] = mapped_column(Integer, nullable=False)  # points marqués (PTS)
    fgm_total: Mapped[int] = mapped_column(Integer, nullable=False)  # tirs réussis (Field Goals Made)
    fga_total: Mapped[int] = mapped_column(Integer, nullable=False)  # tirs tentés (Field Goals Attempted)
    # En-tête corrompu dans l'Excel : Excel a lu "3PM" comme l'horaire "3 PM" et l'a
    # stocké en datetime.time(15, 0). Le dictionnaire du classeur a hérité du bug et
    # décrit cette colonne comme « Minutes jouées après 15:00 de jeu » — une statistique
    # qui n'existe pas. Seul le nom était perdu : les valeurs sont intactes (vérifié,
    # three_pm_total <= three_pa_total sur 569/569 lignes).
    three_pm_total: Mapped[int] = mapped_column(Integer, nullable=False)  # tirs à 3 points réussis (3PM)
    three_pa_total: Mapped[int] = mapped_column(Integer, nullable=False)  # tirs à 3 points tentés (3PA)
    ftm_total: Mapped[int] = mapped_column(Integer, nullable=False)  # lancers francs réussis (Free Throws Made)
    fta_total: Mapped[int] = mapped_column(Integer, nullable=False)  # lancers francs tentés
    oreb_total: Mapped[int] = mapped_column(Integer, nullable=False)  # rebonds offensifs
    dreb_total: Mapped[int] = mapped_column(Integer, nullable=False)  # rebonds défensifs
    reb_total: Mapped[int] = mapped_column(Integer, nullable=False)  # rebonds totaux (≠ oreb + dreb dans ce fichier)
    ast_total: Mapped[int] = mapped_column(Integer, nullable=False)  # passes décisives (Assists)
    tov_total: Mapped[int] = mapped_column(Integer, nullable=False)  # balles perdues (Turnovers)
    stl_total: Mapped[int] = mapped_column(Integer, nullable=False)  # interceptions (Steals)
    blk_total: Mapped[int] = mapped_column(Integer, nullable=False)  # contres (Blocks)
    pf_total: Mapped[int] = mapped_column(Integer, nullable=False)  # fautes personnelles
    fp_total: Mapped[float] = mapped_column(REAL, nullable=False)  # Fantasy Points
    dd2_total: Mapped[int] = mapped_column(Integer, nullable=False)  # double-doubles (≥10 dans 2 catégories)
    td3_total: Mapped[int] = mapped_column(Integer, nullable=False)  # triple-doubles (≥10 dans 3 catégories)
    poss_total: Mapped[int] = mapped_column(Integer, nullable=False)  # possessions jouées

    # --- Moyennes par match ---
    minutes_per_game: Mapped[float] = mapped_column(REAL, nullable=False)  # minutes jouées par match (Min)
    plus_minus_per_game: Mapped[float] = mapped_column(REAL, nullable=False)  # écart de score quand il est sur le terrain

    # --- Pourcentages (0-100) ---
    # Attention : ce ne sont PAS les ratios des totaux ci-dessus. L'écart diminue
    # quand le volume augmente, signature d'une moyenne des pourcentages match par
    # match. Ne pas recalculer fg_pct comme fgm_total/fga_total : les deux valeurs
    # sont justes, elles ne mesurent simplement pas la même chose.
    fg_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # réussite aux tirs
    three_p_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # réussite à 3 points
    ft_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # réussite aux lancers francs
    efg_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # Effective FG% : pondère les tirs à 3 points
    ts_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # True Shooting % : inclut tirs et lancers francs
    usg_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # Usage Rate : part des actions utilisées par le joueur
    ast_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # implication dans les passes décisives
    oreb_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # part des rebonds offensifs disponibles captés
    dreb_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # idem en défensif
    reb_pct: Mapped[float] = mapped_column(REAL, nullable=False)  # part des rebonds totaux disponibles captés

    # --- Indices avancés, déjà normalisés à la source ---
    offrtg: Mapped[float] = mapped_column(REAL, nullable=False)  # points marqués par 100 possessions
    defrtg: Mapped[float] = mapped_column(REAL, nullable=False)  # points encaissés par 100 possessions
    netrtg: Mapped[float] = mapped_column(REAL, nullable=False)  # offrtg - defrtg
    pace: Mapped[float] = mapped_column(REAL, nullable=False)  # rythme de jeu : possessions par 48 minutes
    pie: Mapped[float] = mapped_column(REAL, nullable=False)  # Player Impact Estimate : impact global du joueur
    ast_to: Mapped[float] = mapped_column(REAL, nullable=False)  # ratio passes décisives / balles perdues
    ast_ratio: Mapped[float] = mapped_column(REAL, nullable=False)  # passes décisives par 100 possessions
    to_ratio: Mapped[float] = mapped_column(REAL, nullable=False)  # balles perdues par 100 possessions


def creer_engine(url: str, echo: bool = False):
    """Crée l'engine et active les clés étrangères sur CHAQUE connexion.

    Sans ce hook, les ForeignKey déclarées plus haut ne sont pas vérifiées par
    SQLite : une ligne référençant une équipe inexistante serait acceptée.
    """
    engine = create_engine(url, echo=echo)

    @event.listens_for(engine, "connect")
    def _activer_cles_etrangeres(dbapi_connection, connection_record):
        curseur = dbapi_connection.cursor()
        curseur.execute("PRAGMA foreign_keys = ON")
        curseur.close()

    return engine
