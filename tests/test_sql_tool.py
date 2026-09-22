# tests/test_sql_tool.py
"""Tests du tool SQL (src/rag/sql_tool.py).

Entièrement gratuits : requêtes écrites à la main, aucun LLM, aucun appel réseau.
C'est volontaire — la partie la plus sensible du projet (exécuter du SQL écrit par
un modèle) doit être vérifiable sans dépendre d'un modèle.

Les tests utilisent une base temporaire réduite, pour ne pas dépendre de la
présence de data/nba.db ni de son contenu exact.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

from rag.sql_tool import (
    FONCTIONS_INTERDITES,
    TAILLE_MAX_VALEUR,
    RequeteRefusee,
    executer_sql,
)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """Une base minimale au même schéma que la vraie, remplie de 3 joueurs."""
    chemin = tmp_path_factory.mktemp("db") / "test.db"
    con = sqlite3.connect(chemin)
    con.executescript(
        """
        CREATE TABLE teams (code TEXT PRIMARY KEY, name TEXT NOT NULL) STRICT;
        CREATE TABLE players (player_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL) STRICT;
        CREATE TABLE stats (
            stat_id INTEGER PRIMARY KEY, player_id INTEGER, team_code TEXT,
            pts_total INTEGER, reb_total INTEGER, fg_pct REAL
        ) STRICT;
        INSERT INTO teams VALUES ('OKC','Oklahoma City Thunder'), ('DEN','Denver Nuggets');
        INSERT INTO players VALUES (1,'Shai Gilgeous-Alexander'), (2,'Nikola Jokic'), (3,'Jamal Murray');
        INSERT INTO stats VALUES (1,1,'OKC',2485,380,51.9), (2,2,'DEN',2072,889,57.6), (3,3,'DEN',1200,250,47.4);
        """
    )
    con.commit()
    con.close()
    return chemin


@pytest.fixture(scope="module")
def base_volumineuse(tmp_path_factory):
    """Une base d'un demi-millier de lignes, pour tester l'interruption.

    La vraie base contient ~570 joueurs : un produit cartésien à trois tables y
    coûte des centaines de millions de combinaisons. Sur les 3 lignes de la base
    précédente il s'exécuterait instantanément, et le test ne prouverait rien.
    """
    chemin = tmp_path_factory.mktemp("db_volumineuse") / "test.db"
    con = sqlite3.connect(chemin)
    con.execute("CREATE TABLE stats (stat_id INTEGER PRIMARY KEY, pts_total INTEGER) STRICT")
    con.executemany("INSERT INTO stats VALUES (?, ?)", [(i, i * 3) for i in range(1, 501)])
    con.commit()
    con.close()
    return chemin


# --- Lectures légitimes ---


def test_lecture_simple(base):
    resultat = executer_sql("SELECT full_name FROM players ORDER BY player_id", base)
    assert resultat.row_count == 3
    assert resultat.columns == ["full_name"]
    assert resultat.rows[0] == {"full_name": "Shai Gilgeous-Alexander"}
    assert resultat.truncated is False


def test_jointure(base):
    resultat = executer_sql(
        "SELECT p.full_name, s.pts_total FROM stats s "
        "JOIN players p USING(player_id) ORDER BY s.pts_total DESC LIMIT 1",
        base,
    )
    assert resultat.rows == [{"full_name": "Shai Gilgeous-Alexander", "pts_total": 2485}]


def test_agregation(base):
    """Les fonctions d'agrégation déclenchent SQLITE_FUNCTION : si cette opération
    n'était pas autorisée, toutes les questions de totaux échoueraient."""
    resultat = executer_sql(
        "SELECT t.name, SUM(s.pts_total) AS points FROM stats s "
        "JOIN teams t ON t.code = s.team_code GROUP BY t.name ORDER BY points DESC",
        base,
    )
    assert resultat.rows[0] == {"name": "Denver Nuggets", "points": 3272}


def test_cte(base):
    resultat = executer_sql(
        "WITH top AS (SELECT * FROM stats ORDER BY pts_total DESC LIMIT 2) "
        "SELECT COUNT(*) AS n FROM top",
        base,
    )
    assert resultat.rows == [{"n": 2}]


def test_sous_requete(base):
    resultat = executer_sql(
        "SELECT full_name FROM players WHERE player_id IN "
        "(SELECT player_id FROM stats WHERE pts_total > 2000)",
        base,
    )
    assert resultat.row_count == 2


# --- Requêtes refusées ---


@pytest.mark.parametrize(
    "requete",
    [
        "DELETE FROM players",
        "UPDATE stats SET pts_total = 0",
        "INSERT INTO teams VALUES ('XXX','Test')",
        "DROP TABLE teams",
        "CREATE TABLE evil (x INTEGER)",
    ],
)
def test_ecriture_refusee(base, requete):
    """Première barrière : la connexion est ouverte en lecture seule, donc SQLite
    refuse toute écriture quelle que soit la requête."""
    with pytest.raises(RequeteRefusee):
        executer_sql(requete, base)


@pytest.mark.parametrize("requete", ["PRAGMA table_info(stats)", "ATTACH DATABASE ':memory:' AS x"])
def test_operations_hors_lecture_refusees(base, requete):
    """Deuxième barrière : l'autoriseur refuse tout ce qui n'est pas une lecture,
    même si ce n'est pas une écriture à proprement parler."""
    with pytest.raises(RequeteRefusee):
        executer_sql(requete, base)


def test_requetes_multiples_refusees(base):
    """Le schéma classique de l'injection SQL : greffer une seconde instruction
    derrière une première anodine.

    Le `match` est essentiel, pas cosmétique : sans lui, ce test passait alors
    que l'agent recevait « seules les lectures sont autorisées » — un message
    trompeur qui l'aurait poussé à réécrire un SELECT déjà correct.
    `sqlite3.ProgrammingError` hérite de `DatabaseError` : l'ordre des blocs
    `except` décide lequel des deux messages est renvoyé.
    """
    with pytest.raises(RequeteRefusee, match="une seule requête|Une seule requête"):
        executer_sql("SELECT 1; DROP TABLE teams", base)


def test_requete_invalide_message_exploitable(base):
    """Le message doit permettre à l'agent de corriger lui-même sa requête."""
    with pytest.raises(RequeteRefusee, match="invalide.*no such column"):
        executer_sql("SELECT colonne_inexistante FROM players", base)


def test_base_absente(tmp_path):
    with pytest.raises(RequeteRefusee, match="introuvable"):
        executer_sql("SELECT 1", tmp_path / "nexiste_pas.db")


def test_fonctions_interdites_listees():
    """load_extension permettrait de charger du code arbitraire : SQLITE_FUNCTION
    autorise toutes les fonctions, d'où cette liste d'exclusion explicite."""
    assert "load_extension" in FONCTIONS_INTERDITES


# --- Limites de ressources ---


def test_resultat_plafonne(base):
    """La limite borne ce qui remonte dans le prompt, et `truncated` avertit
    l'agent que sa réponse porte sur un extrait."""
    resultat = executer_sql("SELECT * FROM players", base, limite=2)
    assert resultat.row_count == 2
    assert resultat.truncated is True


def test_requete_trop_longue_interrompue(base_volumineuse):
    """Troisième barrière : le temps de calcul.

    Un produit cartésien à trois tables (500^3, soit 125 millions de
    combinaisons) est le cas typique d'une requête syntaxiquement correcte et
    parfaitement autorisée, mais qui bloquerait l'agent indéfiniment. Le LIMIT ne
    protège de rien ici : SQLite doit parcourir les combinaisons pour compter.

    L'arrêt survient à la première vérification APRÈS le seuil — SQLite appelle
    le gestionnaire toutes les N instructions de sa machine virtuelle — donc « au
    seuil configuré » et non à la milliseconde près. Le seuil est abaissé ici
    pour garder la suite de tests rapide.
    """
    with pytest.raises(RequeteRefusee, match="interrompue"):
        executer_sql(
            "SELECT COUNT(*) FROM stats a, stats b, stats c",
            base_volumineuse,
            delai_max=0.3,
        )


def test_cte_recursive_refusee(base):
    """Les CTE récursives sont refusées **volontairement**.

    C'est le moyen le plus simple de faire consommer du CPU sans limite à SQLite,
    indépendamment du volume des données : la requête ci-dessous compterait
    jusqu'à 500 millions sur une base de 3 lignes. Aucune question analytique de
    ce projet n'a besoin de récursion, donc elles sont bloquées par l'autoriseur
    (SQLITE_RECURSIVE hors allowlist) plutôt que rattrapées par le délai.

    Les CTE non récursives, elles, restent autorisées — cf. test_cte.
    """
    with pytest.raises(RequeteRefusee, match="refusée"):
        executer_sql(
            "WITH RECURSIVE boucle(x) AS ("
            "  SELECT 1 UNION ALL SELECT x + 1 FROM boucle WHERE x < 500000000"
            ") SELECT COUNT(*) FROM boucle",
            base,
        )


def test_valeur_trop_volumineuse_refusee(base):
    """Quatrième barrière : la taille d'une valeur, pas seulement le nombre de lignes.

    `SELECT zeroblob(...)` est un SELECT parfaitement légitime aux yeux des trois
    premières barrières : lecture seule, aucune opération interdite, exécution
    instantanée. Un LIMIT n'y change rien puisqu'il borne les lignes, pas leur
    contenu — une seule cellule suffirait à saturer le prompt.
    """
    with pytest.raises(RequeteRefusee, match="volumineuse"):
        executer_sql(f"SELECT zeroblob({TAILLE_MAX_VALEUR * 10})", base)


def test_valeur_sous_la_limite_acceptee(base):
    """Contre-épreuve : la limite ne doit pas gêner les valeurs normales."""
    resultat = executer_sql("SELECT LENGTH(zeroblob(1000)) AS n", base)
    assert resultat.rows == [{"n": 1000}]


def test_chemin_relatif_accepte(base, monkeypatch):
    """`Path.as_uri()` lève ValueError sur un chemin relatif, et `chemin_base` est
    un paramètre public : resolve() doit être appliqué avant."""
    monkeypatch.chdir(base.parent)
    resultat = executer_sql("SELECT COUNT(*) AS n FROM players", Path(base.name))
    assert resultat.rows == [{"n": 3}]


def test_fonction_interdite_insensible_a_la_casse():
    """Les noms de la liste doivent être en minuscules : SQLite transmet à
    l'autoriseur le nom *enregistré* de la fonction, toujours en minuscules."""
    assert all(nom == nom.casefold() for nom in FONCTIONS_INTERDITES)
