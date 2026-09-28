# tests/test_sql_tool.py
"""Tests de l'outil SQL : ce qu'il autorise, et surtout ce qu'il refuse.

Gratuits — aucun appel de modèle. La base est celle du projet, ouverte en lecture
seule ; les tests n'écrivent rien, et le vérifient.

Chaque barrière est **mise en échec volontairement**. Une barrière qu'on n'a jamais vue
refuser quelque chose n'est pas une barrière, c'est une intention.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import NBA_DB_PATH
from db_models import TABLES_INTERROGEABLES
from rag import sql_tool
from rag.sql_tool import RequeteRefusee, executer_sql
from schemas import SQLResult

pytestmark = pytest.mark.skipif(
    not Path(NBA_DB_PATH).exists(),
    reason="base absente : lancer scripts/load_excel_to_db.py",
)


# --- Ce que l'outil doit permettre --------------------------------------------------


def test_lecture_simple():
    r = executer_sql(
        "SELECT s.pts_total FROM stats s JOIN players p ON p.player_id = s.player_id "
        "WHERE p.full_name = 'Nikola Jokić'"
    )
    assert isinstance(r, SQLResult)
    assert r.lignes == [[2072]]
    assert r.colonnes == ["pts_total"]


def test_agregation_avec_jointure():
    """La question C4 du jeu de test, que le système échoue depuis trois runs."""
    r = executer_sql(
        "SELECT t.name, SUM(s.pts_total) AS total FROM stats s "
        "JOIN teams t ON t.code = s.team_code GROUP BY t.code ORDER BY total DESC LIMIT 1"
    )
    assert r.lignes[0][1] == 10292


def test_cte_autorisee():
    """Une CTE ne doit pas être confondue avec une table interdite : son alias n'est
    pas signalé comme une lecture, vérifié sur SQLite 3.53."""
    r = executer_sql(
        "WITH classement AS (SELECT team_code, SUM(pts_total) v FROM stats GROUP BY team_code) "
        "SELECT team_code FROM classement ORDER BY v DESC LIMIT 1"
    )
    assert r.lignes == [["DET"]]


# --- Barrière 1 : lecture seule -----------------------------------------------------


@pytest.mark.parametrize("requete", [
    "UPDATE stats SET pts_total = 0",
    "DELETE FROM players",
    "INSERT INTO teams (code, name) VALUES ('ZZZ', 'Faux')",
    "DROP TABLE stats",
    "CREATE TABLE intrus (a INTEGER)",
])
def test_aucune_ecriture_possible(requete):
    with pytest.raises(RequeteRefusee):
        executer_sql(requete)


def test_la_base_est_intacte_apres_les_tentatives():
    """Contrôle de fond : les refus ci-dessus n'ont rien modifié."""
    assert executer_sql("SELECT COUNT(*) FROM players").lignes == [[569]]
    assert executer_sql("SELECT COUNT(*) FROM teams").lignes == [[30]]


# --- Barrière 2 : tables autorisées -------------------------------------------------


@pytest.mark.parametrize("requete", [
    "SELECT content FROM reports LIMIT 1",
    "WITH r AS (SELECT content FROM reports) SELECT * FROM r LIMIT 1",
    "SELECT name FROM sqlite_master",
])
def test_tables_hors_perimetre_refusees(requete):
    """`reports` existe mais reste hors de portée : ses documents font 14 à 56 Ko et
    satureraient le prompt, et les questions narratives relèvent du vectoriel."""
    with pytest.raises(RequeteRefusee):
        executer_sql(requete)


def test_la_liste_est_blanche_et_non_noire():
    """Une table ajoutée demain doit être refusée par défaut."""
    assert sql_tool._autoriseur(sqlite3.SQLITE_READ, "table_future", None, "main", None) == sqlite3.SQLITE_DENY
    for table in TABLES_INTERROGEABLES:
        assert sql_tool._autoriseur(sqlite3.SQLITE_READ, table, None, "main", None) == sqlite3.SQLITE_OK


def test_fonctions_dangereuses_refusees():
    for nom in sql_tool.FONCTIONS_INTERDITES:
        assert sql_tool._autoriseur(sqlite3.SQLITE_FUNCTION, nom, None, "main", None) == sqlite3.SQLITE_DENY
    # Les agrégats restent indispensables
    assert sql_tool._autoriseur(sqlite3.SQLITE_FUNCTION, "sum", None, "main", None) == sqlite3.SQLITE_OK


def test_operations_hors_lecture_refusees():
    for action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                   sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_RECURSIVE):
        assert sql_tool._autoriseur(action, None, None, "main", None) == sqlite3.SQLITE_DENY


# --- Barrière 3 : délai -------------------------------------------------------------


def test_requete_trop_longue_interrompue(monkeypatch):
    """Mesuré, pas supposé : un produit cartésien est coupé par le gestionnaire de
    progression, pas par un LIMIT — qu'une telle requête peut porter sans être rapide."""
    import time

    monkeypatch.setattr(sql_tool, "DELAI_MAX_SECONDES", 0.3)
    debut = time.monotonic()
    with pytest.raises(RequeteRefusee, match="interrompue"):
        executer_sql("SELECT COUNT(*) FROM stats a, stats b, stats c, stats d")
    assert (time.monotonic() - debut) < 5, "le délai n'a pas coupé la requête"


# --- Barrière 4 : taille ------------------------------------------------------------


def test_resultat_tronque_et_signale(monkeypatch):
    """« Les 50 premières » ne doit jamais se lire comme « toutes »."""
    monkeypatch.setattr(sql_tool, "LIMITE_LIGNES", 5)
    r = executer_sql("SELECT full_name FROM players")

    assert len(r.lignes) == 5
    assert r.tronque
    assert "tronqué" in r.pour_le_modele()


def test_resultat_complet_non_signale():
    r = executer_sql("SELECT code FROM teams LIMIT 3")
    assert not r.tronque and "tronqué" not in r.pour_le_modele()


# --- Profil de capacités ------------------------------------------------------------


def test_le_profil_est_derive_de_la_base():
    """Aucune capacité n'est recopiée à la main : la recopie finit toujours par mentir."""
    profil = sql_tool.profil_capacites()

    assert "569 joueurs" in profil
    # Les limites réelles, déduites de l'absence de table et de colonnes
    assert "granularité par match" in profil
    assert "une seule saison" in profil
    assert "domicile/extérieur" in profil


def test_la_description_ne_montre_que_les_tables_autorisees():
    description = sql_tool.description_du_tool()

    for table in TABLES_INTERROGEABLES:
        assert f"CREATE TABLE {table}" in description
    assert "CREATE TABLE reports" not in description
    # Les exemples guident vers les tournures attendues
    assert "JOIN players" in description


def test_requete_invalide_donne_un_message_exploitable():
    """Le modèle doit pouvoir corriger : un message vide le laisserait inventer."""
    with pytest.raises(RequeteRefusee, match="refusée"):
        executer_sql("SELECT colonne_inexistante FROM stats")


def test_l_introspection_ne_passe_pas_par_l_autoriseur():
    """Deux connexions, deux usages — la confusion des deux a coûté deux tests.

    L'autoriseur contraint ce que le MODÈLE demande, et refuse `PRAGMA table_info`
    comme `sqlite_master`. Nos propres requêtes d'introspection, fixes et écrites ici,
    en ont besoin : elles passent par une connexion en lecture seule sans autoriseur.
    """
    chemin = Path(NBA_DB_PATH)

    # Ce que le modèle ne peut pas faire
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        sql_tool._ouvrir_connexion(chemin).execute("PRAGMA table_info(stats)").fetchall()

    # Ce que notre introspection fait, sur la même base et toujours en lecture seule
    colonnes = sql_tool._connexion_lecture(chemin).execute("PRAGMA table_info(stats)").fetchall()
    assert len(colonnes) == 47


def test_l_introspection_reste_en_lecture_seule():
    """Sans autoriseur, `mode=ro` reste la barrière : aucune écriture possible."""
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        sql_tool._connexion_lecture(Path(NBA_DB_PATH)).execute("DELETE FROM players")


def test_les_pieges_d_agregation_sont_documentes():
    """Observé sur un appel réel : le modèle a sommé `wins_total` par équipe et annoncé
    767 victoires — une saison en compte 82. Ces colonnes valent pour UN joueur, les
    additionner multiplie par l'effectif. Le schéma seul ne le révèle pas."""
    description = sql_tool.description_du_tool()

    assert "prendre le MAXIMUM, jamais la somme" in description
    assert "moyennes par match, pas les ratios des totaux" in description


def test_la_somme_des_victoires_n_a_pas_de_sens():
    """Le fait qui justifie l'avertissement ci-dessus, vérifié sur la base."""
    somme = executer_sql(
        "SELECT SUM(wins_total) FROM stats WHERE team_code = 'OKC'"
    ).lignes[0][0]
    maximum = executer_sql(
        "SELECT MAX(wins_total) FROM stats WHERE team_code = 'OKC'"
    ).lignes[0][0]

    assert somme > 82, "la somme dépasse le nombre de matchs d'une saison"
    assert maximum <= 82, "le maximum, lui, est un bilan d'équipe plausible"


def _requetes_des_exemples() -> list[str]:
    """Extrait les requêtes de EXEMPLES_SQL : coupe sur ';', retire les lignes de
    commentaire qui portent la question, garde ce qui commence à SELECT."""
    requetes = []
    for morceau in sql_tool.EXEMPLES_SQL.split(";"):
        corps = "\n".join(
            ligne for ligne in morceau.splitlines() if not ligne.strip().startswith("--")
        )
        debut = corps.upper().find("SELECT")
        if debut >= 0:
            requetes.append(corps[debut:].strip())
    return requetes


def test_tous_les_exemples_few_shot_s_executent():
    """Les exemples sont exécutés, pas relus.

    Un exemple faux enseignerait du SQL faux au modèle, et le défaut ne se verrait
    qu'à l'appel réel — c'est-à-dire une fois payé. Ils passent donc par le même
    `executer_sql()` que les requêtes du modèle, autoriseur compris.
    """
    requetes = _requetes_des_exemples()
    assert len(requetes) == 4, f"{len(requetes)} exemples extraits au lieu de 4"

    for requete in requetes:
        resultat = executer_sql(requete)
        assert resultat.lignes, f"exemple sans résultat : {requete[:70]}"


def test_l_exemple_multicritere_croise_where_et_having():
    """L'agrégation multicritères demandée au cahier des charges.

    WHERE filtre des JOUEURS (matchs joués, minutes), HAVING filtre des ÉQUIPES
    (taille de la rotation). Confondre les deux est l'erreur classique — et
    l'exemple n'enseigne rien si son HAVING n'écarte aucun groupe.
    """
    multicritere = _requetes_des_exemples()[-1]
    assert "WHERE" in multicritere
    assert "GROUP BY" in multicritere
    assert "HAVING" in multicritere

    retenues = len(executer_sql(multicritere.replace("LIMIT 3", "LIMIT 50")).lignes)
    sans_having = len(
        executer_sql(
            multicritere.replace("HAVING COUNT(*) >= 8", "").replace("LIMIT 3", "LIMIT 50")
        ).lignes
    )
    assert sans_having == 30, "les 30 équipes devraient passer sans le HAVING"
    assert retenues < sans_having, "un HAVING qui n'écarte rien n'enseigne rien"
