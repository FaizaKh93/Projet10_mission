# src/rag/sql_tool.py
"""Exécute sur la base NBA le SQL écrit par le LLM, sans lui faire confiance.

Quatre barrières, dont trois appliquées par SQLite lui-même plutôt que par une
inspection du texte de la requête (contournable par un commentaire ou une CTE) :

1. connexion `mode=ro`          -> aucune écriture possible, quelle que soit la requête
2. autoriseur                   -> refuse tout ce qui n'est pas une lecture
3. gestionnaire de progression  -> interrompt une requête trop longue
4. limites de taille            -> bornent les lignes et les valeurs renvoyées

Délai et limites de taille ne se recouvrent pas : le premier protège le temps de
calcul, les secondes la taille du prompt. Un `CROSS JOIN ... LIMIT 50` reste
coûteux malgré son LIMIT, et un LIMIT ne borne pas le contenu d'une cellule.
"""
import logging
import sqlite3
import time
from functools import lru_cache
from pathlib import Path

from langchain_community.utilities import SQLDatabase

from config import NBA_DB_FILE, NBA_DB_URL
from schemas import SQLResult

LIMITE_LIGNES = 50  # au-delà, le résultat est tronqué et signalé comme tel
DELAI_MAX_SECONDES = 5.0  # durée maximale d'exécution d'une requête
INSTRUCTIONS_ENTRE_VERIFICATIONS = 1000  # fréquence d'appel du gestionnaire de progression
TAILLE_MAX_VALEUR = 1_000_000  # octets par valeur ; écarte les zeroblob() géants

# SQLITE_FUNCTION est indispensable : sans lui, tout SUM() serait refusé.
# SQLITE_RECURSIVE est exclu volontairement : une CTE récursive brûle du CPU sans
# limite quel que soit le volume, et aucune question du projet n'en a besoin.
OPERATIONS_AUTORISEES = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
)

# SQLITE_FUNCTION autorise TOUTES les fonctions, pas seulement les agrégats.
# Ces trois-là sont déjà indisponibles ici : défense en profondeur.
FONCTIONS_INTERDITES = frozenset({"load_extension", "readfile", "writefile"})


class RequeteRefusee(Exception):
    """Requête refusée ou en échec. Le message est destiné à l'agent, qui peut
    corriger sa requête et réessayer."""


def _autoriseur(action: int, arg1, nom_fonction, nom_base, declencheur) -> int:
    """Décide, opération par opération, si SQLite a le droit de poursuivre.

    Appelée par SQLite pendant la compilation de la requête, une fois par
    opération élémentaire. Renvoyer SQLITE_DENY fait échouer la requête entière
    avec `DatabaseError("not authorized")`.
    """
    if action not in OPERATIONS_AUTORISEES:
        return sqlite3.SQLITE_DENY  # écriture, PRAGMA, ATTACH, CTE récursive...
    if action == sqlite3.SQLITE_FUNCTION:
        # SQLite transmet le nom *enregistré*, donc déjà en minuscules
        if (nom_fonction or "").casefold() in FONCTIONS_INTERDITES:
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _ouvrir_connexion(chemin_base: Path) -> sqlite3.Connection:
    """Ouvre la base en lecture seule, autoriseur et limite de taille déjà posés.

    Lève RequeteRefusee si le fichier de base n'existe pas.
    """
    if not chemin_base.exists():
        raise RequeteRefusee(
            f"Base introuvable : {chemin_base}. Lancer d'abord scripts/load_excel_to_db.py."
        )
    uri = chemin_base.resolve().as_uri()  # resolve() : as_uri() refuse un chemin relatif
    connexion = sqlite3.connect(f"{uri}?mode=ro", uri=True)  # mode=ro : écritures impossibles
    connexion.row_factory = sqlite3.Row  # lignes accessibles par nom de colonne
    connexion.set_authorizer(_autoriseur)  # filtre posé avant toute requête
    connexion.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, TAILLE_MAX_VALEUR)  # borne chaque valeur
    return connexion


def decrire_schema(url: str = NBA_DB_URL) -> str:
    """Construit le texte de schéma à injecter dans le prompt de l'agent.

    Assemble le DDL des tables et deux lignes d'exemple de chacune, puis y ajoute
    à la main ce que le DDL ne dit pas : l'unité de chaque famille de colonnes,
    l'écart entre les colonnes _pct et les totaux, et l'absence de toute donnée
    match par match.
    """
    base = SQLDatabase.from_uri(url, sample_rows_in_table_info=2)  # DDL + 2 lignes par table
    return (
        base.get_table_info()
        + "\n\n"
        + "Conventions de nommage des colonnes de `stats` :\n"
        "- suffixe _total     : agrégat de la SAISON (pts_total = 2485 pour SGA)\n"
        "- suffixe _per_game  : moyenne PAR MATCH (minutes_per_game = 34.2)\n"
        "- suffixe _pct       : pourcentage de 0 à 100 (fg_pct = 51.9)\n"
        "- sans suffixe       : indice déjà normalisé (offrtg, pie, pace)\n"
        "\n"
        "Attention : les colonnes _pct ne coïncident pas exactement avec le ratio "
        "des totaux stockés. Sur les joueurs à gros volume l'écart relève de "
        "l'arrondi (moins de 0,8 point au-delà de 600 tirs tentés), mais il atteint "
        "plusieurs points sur les joueurs à faible temps de jeu, pour lesquels la "
        "source est incohérente avec elle-même. Utiliser la colonne _pct telle "
        "quelle : c'est le pourcentage officiel publié. Ne jamais le recalculer "
        "à partir des totaux.\n"
        "\n"
        "Les données ne contiennent aucune granularité par match : ni date, ni "
        "adversaire, ni indicateur domicile/extérieur. Les questions portant sur "
        "un match précis ou sur les N derniers matchs sont sans réponse possible."
    )


# Exemples few-shot montrant les tournures attendues : jointure obligatoire pour
# obtenir un nom, LIMIT sur les classements, et lecture directe des colonnes _pct.
EXEMPLES_SQL = """Exemples de questions et des requêtes correspondantes :

Q : Combien de points Nikola Jokić a-t-il marqués cette saison ?
SQL : SELECT s.pts_total FROM stats s JOIN players p USING (player_id)
      WHERE p.full_name = 'Nikola Jokić';

Q : Qui sont les 5 meilleurs marqueurs ?
SQL : SELECT p.full_name, s.pts_total FROM stats s JOIN players p USING (player_id)
      ORDER BY s.pts_total DESC LIMIT 5;

Q : Combien de points les Detroit Pistons ont-ils marqués au total ?
SQL : SELECT SUM(s.pts_total) AS points FROM stats s
      JOIN teams t ON t.code = s.team_code WHERE t.name = 'Detroit Pistons';

Q : Quel est le pourcentage au tir de Zach LaVine ?
SQL : SELECT s.fg_pct FROM stats s JOIN players p USING (player_id)
      WHERE p.full_name = 'Zach LaVine';
      -- on lit fg_pct ; ne jamais le recalculer depuis fgm_total / fga_total"""


@lru_cache(maxsize=1)
def description_du_tool(url: str = NBA_DB_URL) -> str:
    """Assemble la description du tool exposée au modèle : rôle, schéma, exemples.

    Mise en cache et appelée au premier run, pas à l'import : la construire ouvre
    la base, et `data/nba.db` peut ne pas exister au moment où le module est chargé.
    """
    return (
        "Exécute une requête SQL de lecture sur la base NBA et renvoie ses lignes.\n"
        "À utiliser pour TOUTE question chiffrée (total, moyenne, classement, "
        "comparaison entre joueurs ou équipes) plutôt que de répondre de mémoire.\n\n"
        + decrire_schema(url)
        + "\n\n"
        + EXEMPLES_SQL
    )


def executer_sql(
    requete: str,
    chemin_base: Path = NBA_DB_FILE,
    limite: int = LIMITE_LIGNES,
    delai_max: float = DELAI_MAX_SECONDES,
) -> SQLResult:
    """Exécute une requête de lecture et renvoie ses lignes dans un SQLResult.

    Le résultat est coupé à `limite` lignes, `truncated` signalant la coupure.
    Lève RequeteRefusee si la requête est refusée, invalide, trop longue ou trop
    volumineuse — chaque message disant lequel de ces cas s'est produit, pour que
    l'agent puisse corriger lui-même sa requête.
    """
    connexion = _ouvrir_connexion(chemin_base)
    debut = time.perf_counter()

    # Renvoyer 1 interrompt la requête. SQLite appelle ce test toutes les N
    # instructions : l'arrêt a lieu à la première vérification après le seuil.
    connexion.set_progress_handler(
        lambda: 1 if time.perf_counter() - debut > delai_max else 0,
        INSTRUCTIONS_ENTRE_VERIFICATIONS,
    )

    try:
        curseur = connexion.execute(requete)  # execute() refuse les requêtes multiples
        lignes = curseur.fetchmany(limite + 1)  # +1 : la ligne en trop révèle la troncature
        colonnes = [d[0] for d in curseur.description] if curseur.description else []

    # DatabaseError est la classe mère des trois suivantes : la placer avant elles
    # les rendrait inatteignables et l'agent recevrait un message trompeur.
    except sqlite3.OperationalError as e:
        message = str(e)
        if "interrupted" in message:  # posé par le gestionnaire de progression
            raise RequeteRefusee(
                f"Requête interrompue : exécution supérieure à {delai_max} s. "
                "La simplifier, par exemple en évitant de croiser plusieurs tables."
            ) from e
        raise RequeteRefusee(f"Requête invalide : {message}") from e
    except sqlite3.ProgrammingError as e:  # « SELECT 1; DROP TABLE ... »
        raise RequeteRefusee(f"Une seule requête à la fois. Détail : {e}") from e
    except sqlite3.DataError as e:  # dépassement de TAILLE_MAX_VALEUR
        raise RequeteRefusee(
            f"Valeur trop volumineuse (limite {TAILLE_MAX_VALEUR} octets). Détail : {e}"
        ) from e
    except sqlite3.DatabaseError as e:  # refus de l'autoriseur : « not authorized »
        raise RequeteRefusee(
            f"Requête refusée : seules les lectures (SELECT) sont autorisées. Détail : {e}"
        ) from e
    finally:
        connexion.close()

    tronque = len(lignes) > limite
    lignes = lignes[:limite]  # on jette la ligne supplémentaire lue pour le test
    duree_ms = (time.perf_counter() - debut) * 1000

    if tronque:
        logging.info(f"Résultat tronqué à {limite} lignes : {requete[:80]}")

    return SQLResult(
        query=requete,
        columns=colonnes,
        rows=[dict(ligne) for ligne in lignes],  # sqlite3.Row -> dict, pour Pydantic
        row_count=len(lignes),
        truncated=tronque,
        execution_ms=round(duree_ms, 1),
    )
