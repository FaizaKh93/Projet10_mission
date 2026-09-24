# tests/test_compatibilite.py
"""Tests du validateur de compatibilité question ↔ schéma (src/rag/compatibilite.py).

Trois niveaux, volontairement séparés :

1. UNITAIRE — la règle, sur son vocabulaire complet. Gratuit, déterministe.
2. RÉGRESSION — les cas du testset, dont B1, B2 et M6 que le mécanisme corrige.
3. INTÉGRATION LLM — question réelle → intention → verdict. Payant (`-m api`).

Un échec au niveau 1 accuse la règle, un échec au niveau 3 accuse l'extraction.
"""
import pytest

from rag.compatibilite import (
    CAPACITES,
    FILTRES_INDISPONIBLES,
    Decision,
    Verdict,
    valider_intention,
)
from schemas import IntentionSQL

# =============================================================================
# 1. UNITAIRE — la règle
# =============================================================================

# Le complément de CAPACITES, écrit à la main : dérivé du code, ce test le
# réimplémenterait au lieu de le vérifier.
HORS_REGISTRE = {
    "granularite": ["match", "serie", "autre"],
    "periode": ["date_precise", "plusieurs_saisons", "n_derniers_matchs", "autre"],
    "competition": ["playoffs", "autre"],
}


def test_le_vocabulaire_teste_est_exhaustif():
    """Empêche qu'une valeur ajoutée à un `Literal` échappe aux tests : toute
    valeur déclarable est soit couverte par le registre, soit listée ci-dessus."""
    schema = IntentionSQL.model_json_schema()["properties"]
    for axe, couvertes in CAPACITES.items():
        assert set(schema[axe]["enum"]) == couvertes | set(
            HORS_REGISTRE[axe]
        ), f"{axe} : une valeur du vocabulaire n'est ni couverte ni testée"
    assert set(FILTRES_INDISPONIBLES) == {"filtre_lieu", "filtre_adversaire", "filtre_poste"}


def test_intention_entierement_couverte_garde_le_sql():
    """Les valeurs par défaut décrivent ce que la base sait faire : une saison
    régulière agrégée, sans filtre."""
    d = valider_intention(IntentionSQL(base_sollicitee=True))
    assert d.verdict is Verdict.SQL_DISPONIBLE
    assert d.manquants == ()


@pytest.mark.parametrize(
    "axe, valeur", [(a, v) for a, valeurs in sorted(HORS_REGISTRE.items()) for v in valeurs]
)
def test_chaque_valeur_hors_registre_retire_le_sql(axe, valeur):
    """« autre » compris : sans lui, une question sur le play-in se rabattrait sur
    la valeur par défaut, la seule couverte, et le tool répondrait des chiffres de
    saison régulière."""
    d = valider_intention(IntentionSQL(base_sollicitee=True, **{axe: valeur}))
    assert d.verdict is Verdict.SQL_RETIRE
    assert f"{axe}={valeur}" in d.manquants


@pytest.mark.parametrize("champ, libelle", sorted(FILTRES_INDISPONIBLES.items()))
def test_chaque_filtre_indisponible_retire_le_sql(champ, libelle):
    """Un filtre absent du schéma est abandonné en silence : B2 a comparé domicile
    et extérieur sur une base qui n'a pas la colonne. Requête valide, réponse fausse."""
    d = valider_intention(IntentionSQL(base_sollicitee=True, **{champ: True}))
    assert d.verdict is Verdict.SQL_RETIRE
    assert libelle in d.manquants


def test_manquants_nomme_toutes_les_dimensions_absentes():
    """Le motif doit être complet, pas seulement non vide : une dimension citée
    sur trois rendrait la trace trompeuse."""
    d = valider_intention(
        IntentionSQL(
            base_sollicitee=True,
            granularite="serie",
            competition="playoffs",
            filtre_adversaire=True,
        )
    )
    assert set(d.manquants) == {
        "granularite=serie",
        "competition=playoffs",
        FILTRES_INDISPONIBLES["filtre_adversaire"],
    }


def test_base_non_sollicitee_court_circuite_les_dimensions():
    """Une question sans chiffre ne peut pas être « hors périmètre » : il n'y a
    pas de périmètre à confronter."""
    d = valider_intention(
        IntentionSQL(base_sollicitee=False, granularite="serie", filtre_lieu=True)
    )
    assert d.verdict is Verdict.BASE_NON_SOLLICITEE
    assert d.manquants == ()


def test_le_registre_ne_couvre_que_la_saison_reguliere():
    """Documente l'état réel de la base : une seule saison, aucune granularité fine."""
    assert CAPACITES["granularite"] == {"saison"}
    assert CAPACITES["periode"] == {"saison_courante"}
    assert CAPACITES["competition"] == {"saison_reguliere"}


def test_aucune_valeur_autre_n_est_couverte():
    """« autre » ne doit jamais entrer dans le registre, sur aucun axe."""
    assert all("autre" not in valeurs for valeurs in CAPACITES.values())


def test_le_vocabulaire_des_axes_est_verrouille():
    """Les `Literal` interdisent `None` et les valeurs inconnues — garantie qui
    vaut aussi pour l'extraction, validée par Pydantic avant le validateur."""
    from pydantic import ValidationError

    for champ in ("granularite", "periode", "competition"):
        with pytest.raises(ValidationError):
            IntentionSQL(base_sollicitee=True, **{champ: None})

    with pytest.raises(ValidationError):
        IntentionSQL(base_sollicitee=True, granularite="trimestre")


# --- Charge utile de la Decision : ce qui part dans la trace ------------------


def test_la_decision_porte_l_intention():
    """Sans elle, un refus tracé est inexplicable."""
    intention = IntentionSQL(base_sollicitee=True, competition="playoffs")
    assert valider_intention(intention).intention is intention


def test_chaque_verdict_a_un_motif_distinct():
    """« le SQL est inutile » et « le SQL est impossible » ne doivent pas produire
    le même message : les confondre masquerait un faux refus."""
    motifs = {
        valider_intention(IntentionSQL(base_sollicitee=False)).motif(),
        valider_intention(IntentionSQL(base_sollicitee=True)).motif(),
        valider_intention(IntentionSQL(base_sollicitee=True, filtre_lieu=True)).motif(),
    }
    assert len(motifs) == 3


def test_le_motif_de_refus_nomme_la_dimension():
    d = valider_intention(IntentionSQL(base_sollicitee=True, filtre_lieu=True))
    assert "domicile" in d.motif()


def test_detail_ajoute_les_valeurs_retenues_au_motif():
    d = valider_intention(IntentionSQL(base_sollicitee=True, competition="playoffs"))
    assert d.motif() in d.detail()
    assert "playoffs" in d.detail()


def test_detail_sans_intention_se_limite_au_motif():
    """Cas de la panne d'extraction : la Decision est construite sans intention."""
    d = Decision(Verdict.SQL_DISPONIBLE)
    assert d.detail() == d.motif()


# =============================================================================
# 2. RÉGRESSION — les 24 cas du testset
# =============================================================================
# Intentions écrites à la main, sans appeler de modèle : elles servent de
# spécification au niveau 3, qui mesure si le modèle les retrouve.

# Aucun chiffre à aller chercher — pas « purement textuelles » : B3 porte sur une
# colonne du fichier Excel, mais sa réponse ne contient aucune donnée de la base.
SQL_INUTILE = {
    "S4": "deux joueurs du Magic cités comme young wing duo",
    "S5": "équipe citée comme champion sans avantage du terrain",
    "S6": "joueur des Pacers cité comme trash-talker",
    "C4": "arguments pour et contre les Wolves",
    "C5": "critiques contre l'usage du rTS%",
    "C6": "raisons d'un Thunder-Pacers peu attractif",
    "B4": "ce que disent les fans de Wembanyama",
    "B5": "meilleur meneur selon Reddit (subjectif)",
    "B6": "équipes les plus citées sur le fil",
    "B3": "sens de la colonne 15:00 dans le fichier",
}

COUVERTES = {
    "S1": IntentionSQL(base_sollicitee=True),
    "S2": IntentionSQL(base_sollicitee=True),
    "S3": IntentionSQL(base_sollicitee=True),
    "C1": IntentionSQL(base_sollicitee=True),
    "C2": IntentionSQL(base_sollicitee=True),
    "C3": IntentionSQL(base_sollicitee=True),
    # Hybrides : le texte identifie l'entité, la base donne un agrégat de saison.
    # M1 à M3 parlent de playoffs sans rien en demander à la base.
    "M1": IntentionSQL(base_sollicitee=True),
    "M2": IntentionSQL(base_sollicitee=True),
    "M3": IntentionSQL(base_sollicitee=True),
    "M4": IntentionSQL(base_sollicitee=True),
    # Entité absente, dimension couverte : hors ressort du validateur. Le tool
    # renvoie « aucune ligne » et l'agent s'abstient déjà correctement.
    "M5": IntentionSQL(base_sollicitee=True),
}

NON_COUVERTES = {
    "B1": IntentionSQL(base_sollicitee=True, granularite="match", periode="n_derniers_matchs"),
    "B2": IntentionSQL(base_sollicitee=True, filtre_lieu=True),
    "M6": IntentionSQL(
        base_sollicitee=True, granularite="serie", competition="playoffs", filtre_adversaire=True
    ),
}


def test_les_intentions_de_reference_donnent_le_verdict_attendu():
    """Valide les fixtures du niveau 3 : une faute de frappe ici se paierait en
    appels API. Une seule fonction, car les 10 cas de SQL_INUTILE partagent la
    même intention — les paramétrer donnerait 10 fois la même vérification."""
    assert (
        valider_intention(IntentionSQL(base_sollicitee=False)).verdict
        is Verdict.BASE_NON_SOLLICITEE
    )
    for cas, intention in COUVERTES.items():
        assert valider_intention(intention).sql_autorise, f"{cas} bloqué à tort"
    for cas, intention in NON_COUVERTES.items():
        d = valider_intention(intention)
        assert d.verdict is Verdict.SQL_RETIRE, f"{cas} : {d.motif()}"
        assert d.manquants, f"{cas} : le motif du refus doit être traçable"


def test_les_24_cas_du_testset_sont_couverts():
    """Garde-fou contre un testset qui évoluerait sans ces intentions."""
    declares = set(SQL_INUTILE) | set(COUVERTES) | set(NON_COUVERTES)
    assert len(declares) == 24, f"{len(declares)} intentions déclarées au lieu de 24"


# =============================================================================
# 3. INTÉGRATION LLM — payant
# =============================================================================


def _question_du_testset(cas: str) -> str:
    import json
    from pathlib import Path

    chemin = Path(__file__).parent.parent / "eval" / "testset.json"
    testset = json.loads(chemin.read_text(encoding="utf-8"))
    return next(c["question"] for c in testset if c["id"] == cas)


# Les deux cas que l'extraction rate encore, documentés plutôt que masqués.
# `strict=False` : un modèle n'est pas déterministe, un succès occasionnel ne doit
# pas faire échouer la suite à son tour.
ECHECS_CONNUS = {
    "B3": "question sur la structure du fichier classée comme sollicitant la base ; "
    "le tool est retiré quand même, le comportement du système reste correct",
    "M2": "competition=playoffs — la compétition citée prise pour celle demandée ; "
    "la même forme reformulée passe (cf. QUESTIONS_INEDITES)",
}


def _cas(nom: str, verdict: Verdict):
    marques = [pytest.mark.xfail(reason=ECHECS_CONNUS[nom], strict=False)] if nom in ECHECS_CONNUS else []
    return pytest.param(nom, verdict, marks=marques, id=f"{nom}-{verdict.name}")


@pytest.mark.api
@pytest.mark.parametrize(
    "cas, verdict_attendu",
    [_cas(c, Verdict.BASE_NON_SOLLICITEE) for c in sorted(SQL_INUTILE)]
    + [_cas(c, Verdict.SQL_DISPONIBLE) for c in sorted(COUVERTES)]
    + [_cas(c, Verdict.SQL_RETIRE) for c in sorted(NON_COUVERTES)],
)
def test_extraction_reelle_donne_le_bon_verdict(cas, verdict_attendu):
    """Le modèle retrouve-t-il l'intention écrite à la main ?

    Un échec accuse le modèle, pas le validateur. Cas révélateurs : M1 à M3, qui
    évoquent les playoffs sans en demander à la base.
    """
    from rag.generation import analyser_couverture

    decision = analyser_couverture(_question_du_testset(cas))
    assert decision.verdict is verdict_attendu, f"{cas} : {decision.detail()}"


# Questions absentes du prompt, du docstring d'IntentionSQL et du testset : elles
# disent si la règle généralise ou si elle ne rattrape que B1, B2 et M6. Les quatre
# premières visent des axes que l'extraction n'a jamais eu à produire.
QUESTIONS_INEDITES = [
    ("Comment le total de points de Jokić a-t-il évolué depuis trois saisons ?", Verdict.SQL_RETIRE),
    ("Combien de points Curry a-t-il inscrits au dernier All-Star Game ?", Verdict.SQL_RETIRE),
    ("Quel meneur de jeu distribue le plus de passes décisives ?", Verdict.SQL_RETIRE),
    ("Quel joueur marque le plus en deuxième mi-temps ?", Verdict.SQL_RETIRE),
    # Les deux formes que l'extraction rate aujourd'hui, autrement formulées :
    # systématique ou anecdotique ?
    (
        "Un fan affirme que Tatum a tourné à 35 points en finale. Est-ce cohérent "
        "avec sa moyenne de saison régulière ?",
        Verdict.SQL_DISPONIBLE,
    ),
    # « pivot » figurait ici : le modèle en déduisait filtre_poste, et le cas
    # mesurait deux choses à la fois. Limite retenue — rien dans le vocabulaire ne
    # distingue « la base doit filtrer par poste » de « la question cite un poste ».
    (
        "Quel joueur les commentaires jugent-ils dominant, et combien de rebonds "
        "a-t-il pris cette saison ?",
        Verdict.SQL_DISPONIBLE,
    ),
    # Contrôles : sans eux, un validateur qui refuserait tout obtiendrait 6/8.
    ("Quel joueur a réalisé le plus d'interceptions cette saison ?", Verdict.SQL_DISPONIBLE),
    ("Que reprochent les fans à l'arbitrage de cette série ?", Verdict.BASE_NON_SOLLICITEE),
]


@pytest.mark.api
@pytest.mark.parametrize("question, verdict_attendu", QUESTIONS_INEDITES)
def test_extraction_generalise_a_des_questions_inedites(question, verdict_attendu):
    """Mesure de généralisation, pas vérité terrain indépendante : les questions
    et leurs verdicts sont écrits par le concepteur du système. Ce qu'elle établit,
    c'est que ces formulations n'ont jamais été vues par le prompt."""
    from rag.generation import analyser_couverture

    decision = analyser_couverture(question)
    assert decision.verdict is verdict_attendu, f"{decision.detail()}"
