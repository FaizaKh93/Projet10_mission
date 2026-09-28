# src/rag/sql_tool.py
"""Exécute sur la base NBA le SQL écrit par le modèle, sans lui faire confiance.

Quatre barrières, dont trois appliquées par SQLite lui-même plutôt que par inspection
du texte de la requête — qu'un commentaire ou une CTE contournerait :

1. connexion `mode=ro`          → aucune écriture possible, quelle que soit la requête
2. autoriseur                   → refuse tout ce qui n'est pas une lecture des tables
                                  autorisées
3. gestionnaire de progression  → interrompt une requête trop longue
4. limites de taille            → bornent les lignes et les valeurs renvoyées

Délai et limites de taille ne se recouvrent pas : le premier protège le temps de
calcul, les secondes la taille du prompt. Un `CROSS JOIN ... LIMIT 50` reste coûteux
malgré son LIMIT, et un LIMIT ne borne pas le contenu d'une cellule.

Ce module décrit aussi ce que la base sait — et ne sait pas — faire. Ce profil est
**dérivé par introspection**, jamais écrit à la main : si une colonne apparaît demain,
le routeur le saura sans qu'on touche au code.
"""
import logging
import sqlite3
import time
from functools import lru_cache
from pathlib import Path

from langchain_community.utilities import SQLDatabase

from config import NBA_DB_URL
from db_models import TABLES_INTERROGEABLES
from schemas import SQLResult

LIMITE_LIGNES = 50          # au-delà, le résultat est tronqué et signalé comme tel
DELAI_MAX_SECONDES = 5.0    # durée maximale d'exécution d'une requête
INSTRUCTIONS_ENTRE_CONTROLES = 1000  # fréquence d'appel du gestionnaire de progression
TAILLE_MAX_VALEUR = 1_000_000        # octets par valeur ; écarte les zeroblob() géants

# SQLITE_FUNCTION est indispensable : sans lui, tout SUM() serait refusé.
# SQLITE_RECURSIVE est exclu volontairement — une CTE récursive brûle du processeur
# sans limite de volume, et aucune question du projet n'en a besoin.
OPERATIONS_AUTORISEES = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
)

# SQLITE_FUNCTION autorise TOUTES les fonctions, pas seulement les agrégats.
# Ces trois-là sont déjà indisponibles ici : défense en profondeur.
FONCTIONS_INTERDITES = frozenset({"load_extension", "readfile", "writefile"})


def _chemin_depuis_url(url: str) -> Path:
    """Extrait le chemin du fichier d'une URL SQLAlchemy.

    Sans cela, le paramètre `url` des fonctions ci-dessous serait décoratif : elles
    liraient la base par défaut quoi qu'on leur passe — et le cache mémoriserait la
    mauvaise réponse sous la bonne clé.
    """
    return Path(url.removeprefix("sqlite:///"))


class RequeteRefusee(Exception):
    """Requête refusée ou en échec. Le message est destiné au modèle, qui peut
    corriger sa requête et réessayer."""


def _autoriseur(action: int, arg1, nom_fonction, nom_base, declencheur) -> int:
    """Filtre posé par SQLite avant toute requête, sur chaque opération élémentaire.

    Liste **blanche** de tables : une table ajoutée demain sera refusée par défaut,
    là où une liste noire l'exposerait. Vérifié sur SQLite 3.53 — un alias de CTE,
    même matérialisée, n'est pas signalé comme une lecture de table, donc la liste
    blanche ne rejette pas les requêtes légitimes.
    """
    if action not in OPERATIONS_AUTORISEES:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and arg1 in FONCTIONS_INTERDITES:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and arg1 and arg1 not in TABLES_INTERROGEABLES:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _connexion_lecture(chemin_base: Path) -> sqlite3.Connection:
    """Connexion en lecture seule, sans autoriseur.

    Réservée à NOS requêtes, fixes et écrites ici : introspection du schéma et
    comptages. `mode=ro` interdit déjà toute écriture ; l'autoriseur, lui, contraint ce
    que le MODÈLE demande, et refuserait `PRAGMA table_info` ou `sqlite_master` — à
    juste titre, mais c'est précisément ce dont l'introspection a besoin.
    """
    if not chemin_base.exists():
        raise RequeteRefusee(
            f"Base introuvable : {chemin_base}. Lancer scripts/load_excel_to_db.py."
        )
    uri = chemin_base.resolve().as_uri()
    return sqlite3.connect(f"{uri}?mode=ro", uri=True)


def _ouvrir_connexion(chemin_base: Path) -> sqlite3.Connection:
    """Connexion destinée au SQL écrit par le modèle : lecture seule, autoriseur posé,
    taille des valeurs bornée."""
    connexion = _connexion_lecture(chemin_base)
    connexion.set_authorizer(_autoriseur)
    connexion.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, TAILLE_MAX_VALEUR)
    return connexion


@lru_cache(maxsize=1)
def profil_capacites(url: str = NBA_DB_URL) -> str:
    """Ce que la base sait faire, et surtout ce qu'elle ne sait pas.

    Entièrement dérivé : tables présentes, colonnes, et faits de cardinalité. C'est
    ce texte qui permet au routeur de décider sans qu'aucune capacité ne soit
    recopiée à la main — la recopie finit toujours par mentir.
    """
    connexion = _connexion_lecture(_chemin_depuis_url(url))
    try:
        colonnes_stats = {
            ligne[1] for ligne in connexion.execute("PRAGMA table_info(stats)")
        }
        saisons = connexion.execute("SELECT COUNT(DISTINCT season) FROM stats").fetchone()[0]
        joueurs = connexion.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        tables = {
            ligne[0]
            for ligne in connexion.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connexion.close()

    limites = []
    if "matches" not in tables:
        limites.append(
            "- aucune granularité par match : pas de date, pas d'adversaire, "
            "pas de distinction domicile/extérieur"
        )
    if saisons <= 1:
        limites.append(
            "- une seule saison en base : aucune comparaison ni évolution entre saisons"
        )
    for colonne, absence in (("played_on", "filtre par date"),
                             ("opponent", "filtre par adversaire"),
                             ("is_home", "filtre domicile/extérieur")):
        if colonne not in colonnes_stats:
            limites.append(f"- pas de {absence}")
    if "reports" in tables:
        limites.append(
            "- les documents Reddit ne sont pas interrogeables ici : ils relèvent de "
            "la recherche vectorielle"
        )

    return (
        f"La base contient les statistiques agrégées de {joueurs} joueurs sur une "
        f"saison régulière, réparties en {len(TABLES_INTERROGEABLES)} tables "
        f"({', '.join(TABLES_INTERROGEABLES)}).\n\n"
        "Elle ne permet pas de répondre aux questions suivantes :\n" + "\n".join(limites)
    )


@lru_cache(maxsize=1)
def decrire_schema(url: str = NBA_DB_URL) -> str:
    """Schéma des tables autorisées, décrit par LangChain.

    LangChain **décrit**, il n'exécute pas : sa chaîne SQL clés en main lancerait le
    SQL du modèle sans passer par les quatre barrières ci-dessus.
    """
    base = SQLDatabase.from_uri(
        url, include_tables=list(TABLES_INTERROGEABLES), sample_rows_in_table_info=2
    )
    return base.get_table_info()


# Exemples montrant les tournures attendues : la jointure obligatoire pour retrouver
# un joueur par son nom, l'agrégation par équipe, le filtre avant classement, et
# l'agrégation multicritère — celle où WHERE et HAVING ne filtrent pas la même chose.
# Tous sont exécutés par les tests : un exemple faux enseignerait du SQL faux.
EXEMPLES_SQL = """Exemples de questions et des requêtes correspondantes :

-- Combien de points Nikola Jokić a-t-il marqués cette saison ?
SELECT s.pts_total FROM stats s
JOIN players p ON p.player_id = s.player_id
WHERE p.full_name = 'Nikola Jokić';

-- Quelle équipe a le total de points cumulé le plus élevé ?
SELECT t.name, SUM(s.pts_total) AS total FROM stats s
JOIN teams t ON t.code = s.team_code
GROUP BY t.code ORDER BY total DESC LIMIT 1;

-- Meilleur pourcentage à 3 points parmi les joueurs ayant tenté plus de 300 tirs ?
SELECT p.full_name, s.three_p_pct, s.three_pa_total FROM stats s
JOIN players p ON p.player_id = s.player_id
WHERE s.three_pa_total > 300
ORDER BY s.three_p_pct DESC LIMIT 1;

-- Parmi les équipes à rotation profonde — au moins 8 joueurs ayant disputé 50 matchs
-- à plus de 20 minutes — laquelle a la meilleure moyenne de points par joueur ?
-- WHERE écarte des JOUEURS, HAVING écarte des ÉQUIPES : les deux sont nécessaires.
SELECT t.name, COUNT(*) AS joueurs_majeurs, ROUND(AVG(s.pts_total), 1) AS pts_moyens
FROM stats s
JOIN teams t ON t.code = s.team_code
WHERE s.games_played >= 50 AND s.minutes_per_game >= 20
GROUP BY t.code
HAVING COUNT(*) >= 8
ORDER BY pts_moyens DESC LIMIT 3;"""


# Pièges que le schéma seul ne révèle pas. Observé sur un appel réel : le modèle a
# sommé `wins_total` par équipe et annoncé 767 victoires — une saison en compte 82. Ces
# colonnes valent pour UN joueur ; les additionner multiplie par l'effectif.
PIEGES_SQL = """Attention en agrégeant :
- `wins_total` et `losses_total` comptent les matchs de l'ÉQUIPE pendant que ce joueur
  jouait. Les sommer par équipe multiplie le total par l'effectif : pour un bilan
  d'équipe, prendre le MAXIMUM, jamais la somme.
- `games_played` suit la même règle.
- Les colonnes en pourcentage sont des moyennes par match, pas les ratios des totaux :
  ne pas recalculer `fg_pct` comme `fgm_total / fga_total`, et ne pas les additionner."""


def description_du_tool(url: str = NBA_DB_URL) -> str:
    """Le texte transmis au modèle : capacités, schéma, pièges, exemples."""
    return (
        f"{profil_capacites(url)}\n\n"
        f"Schéma des tables :\n{decrire_schema(url)}\n\n"
        f"{PIEGES_SQL}\n\n"
        f"{EXEMPLES_SQL}"
    )


def executer_sql(requete: str, url: str = NBA_DB_URL) -> SQLResult:
    """Exécute une requête en lecture seule et rend un résultat structuré.

    Lève `RequeteRefusee` avec un message exploitable : le modèle peut corriger et
    réessayer, ce qui vaut mieux qu'un résultat vide qu'il interpréterait comme « 0 ».
    """
    connexion = _ouvrir_connexion(_chemin_depuis_url(url))
    depart = time.monotonic()

    def _interrompre_si_trop_long() -> int:
        # Rendre une valeur non nulle interrompt la requête en cours.
        return 1 if (time.monotonic() - depart) > DELAI_MAX_SECONDES else 0

    connexion.set_progress_handler(_interrompre_si_trop_long, INSTRUCTIONS_ENTRE_CONTROLES)
    try:
        curseur = connexion.execute(requete)
        lignes = curseur.fetchmany(LIMITE_LIGNES + 1)  # une de plus pour détecter la troncature
        colonnes = [d[0] for d in curseur.description] if curseur.description else []
    except sqlite3.OperationalError as e:
        message = str(e)
        if "interrupted" in message.lower():
            raise RequeteRefusee(
                f"Requête interrompue après {DELAI_MAX_SECONDES} s. Simplifier la requête."
            ) from e
        raise RequeteRefusee(f"Requête refusée : {message}") from e
    except sqlite3.DatabaseError as e:
        raise RequeteRefusee(f"Requête refusée : {e}") from e
    finally:
        connexion.close()

    tronque = len(lignes) > LIMITE_LIGNES
    logging.info(f"SQL exécuté : {len(lignes[:LIMITE_LIGNES])} ligne(s){' (tronqué)' if tronque else ''}")
    return SQLResult(
        requete=requete,
        colonnes=colonnes,
        lignes=[list(ligne) for ligne in lignes[:LIMITE_LIGNES]],
        tronque=tronque,
    )
