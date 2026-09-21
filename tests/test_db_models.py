# tests/test_db_models.py
"""Tests des garde-fous du schéma SQLite (src/db_models.py).

Gratuits et rapides : tout se passe sur une base **en mémoire**, aucun fichier
écrit, aucun appel réseau.

Ces tests ne vérifient pas que les contraintes sont *déclarées* — ça, il suffit
de lire le modèle. Ils vérifient qu'elles sont réellement **appliquées**, parce
que SQLite ne le fait pas par défaut : sans tables STRICT les types déclarés sont
ignorés, et sans PRAGMA foreign_keys les clés étrangères le sont aussi.
"""
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db_models import Base, Player, Stat, Team, creer_engine


@pytest.fixture
def session():
    """Une base neuve en mémoire pour chaque test, via creer_engine() — donc avec
    le PRAGMA foreign_keys posé, exactement comme en conditions réelles."""
    engine = creer_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def base_peuplee(session):
    """Une équipe et un joueur déjà en base, pour tester les lignes de stats."""
    session.add(Team(code="OKC", name="Oklahoma City Thunder"))
    session.add(Player(player_id=1, full_name="Shai Gilgeous-Alexander"))
    session.commit()
    return session


def stats_valides(**surcharges):
    """Une ligne de stats complète, à 0 partout sauf ce qu'on veut tester.

    Les colonnes étant toutes NOT NULL, il faut les remplir toutes ; on les génère
    donc depuis le modèle plutôt que de les lister à la main (elles changeraient
    à chaque évolution du schéma).
    """
    fixes = {"player_id", "stat_id", "team_code", "season"}
    valeurs = {c.name: 0 for c in Stat.__table__.columns if c.name not in fixes}
    valeurs.update(player_id=1, team_code="OKC", season="2024-25")
    valeurs.update(surcharges)
    return valeurs


# --- Garde-fou 1 : les types sont réellement appliqués (tables STRICT) ---


def test_insertion_valide(base_peuplee):
    base_peuplee.add(Stat(**stats_valides(pts_total=2485)))
    base_peuplee.commit()
    assert base_peuplee.query(Stat).one().pts_total == 2485


def test_texte_dans_colonne_entiere_refuse(base_peuplee):
    """Sans STRICT, SQLite accepterait 'N/A' dans pts_total. Conséquence observée :
    SUM() renverrait un total faux, et MAX() désignerait ce joueur comme meilleur
    marqueur, car le texte se trie après les nombres. Aucune erreur, juste une
    réponse fausse — exactement ce qu'on veut rendre impossible."""
    base_peuplee.add(Stat(**stats_valides(pts_total="N/A")))
    with pytest.raises(IntegrityError):
        base_peuplee.commit()


# --- Garde-fou 2 : les clés étrangères sont réellement vérifiées ---


def test_equipe_inexistante_refusee(base_peuplee):
    """Sans le PRAGMA posé dans creer_engine(), cette insertion passerait sans
    broncher et créerait une ligne orpheline, invisible dans les jointures."""
    base_peuplee.add(Stat(**stats_valides(team_code="ZZZ")))
    with pytest.raises(IntegrityError):
        base_peuplee.commit()


def test_joueur_inexistant_refuse(base_peuplee):
    base_peuplee.add(Stat(**stats_valides(player_id=9999)))
    with pytest.raises(IntegrityError):
        base_peuplee.commit()


# --- Garde-fou 3 : une seule ligne par joueur et par saison ---


def test_doublon_joueur_saison_refuse(base_peuplee):
    """C'est la contrainte qui définit le grain de la table : une ligne = un
    joueur sur une saison. Sans elle, relancer l'ingestion deux fois doublerait
    les lignes, et tous les SUM() seraient multipliés par deux."""
    base_peuplee.add(Stat(**stats_valides()))
    base_peuplee.commit()
    base_peuplee.add(Stat(**stats_valides()))
    with pytest.raises(IntegrityError):
        base_peuplee.commit()


def test_meme_joueur_deux_saisons_accepte(base_peuplee):
    """La contrainte porte sur le couple (joueur, saison) : le même joueur sur
    deux saisons différentes reste parfaitement valide."""
    base_peuplee.add(Stat(**stats_valides(season="2023-24")))
    base_peuplee.add(Stat(**stats_valides(season="2024-25")))
    base_peuplee.commit()
    assert base_peuplee.query(Stat).count() == 2


def test_nom_de_joueur_en_double_refuse(session):
    """full_name est UNIQUE : deux lignes pour le même joueur signaleraient un
    problème d'ingestion (le fichier contient 569 noms tous distincts)."""
    session.add(Player(full_name="Nikola Jokić"))
    session.commit()
    session.add(Player(full_name="Nikola Jokić"))
    with pytest.raises(IntegrityError):
        session.commit()
