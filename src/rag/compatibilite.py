# src/rag/compatibilite.py
"""Décide si la base peut répondre à une question, avant d'écrire le moindre SQL.

Le tool garantit qu'une requête est exécutable, pas qu'elle répond à la question :
B2 a inventé une partition domicile/extérieur, M6 a donné un total de saison pour
une série de playoffs. Deux requêtes valides. La description du tool avertit
pourtant de ces absences — une consigne dans le prompt ne contraint rien.

Le verdict ne porte que sur la BRANCHE SQL : la recherche vectorielle continue.
"""
from dataclasses import dataclass
from enum import Enum

from schemas import IntentionSQL

# Ce que la base couvre, par catégorie plutôt que par cas : « 5 derniers matchs »
# n'y figure pas, c'est une granularité et une période toutes deux absentes.
CAPACITES = {
    "granularite": {"saison"},  # ni match, ni série
    "periode": {"saison_courante"},  # ni date précise, ni plusieurs saisons
    "competition": {"saison_reguliere"},  # pas de playoffs
}

# Seul un filtre manquant passe en silence : le modèle l'abandonne et répond à côté.
# Une métrique ou une entité inconnue, elles, sont déjà attrapées par SQLite.
FILTRES_INDISPONIBLES = {
    "filtre_lieu": "domicile/extérieur",
    "filtre_adversaire": "adversaire d'un match",
    "filtre_poste": "poste du joueur",
}


class Verdict(Enum):
    """Trois issues : « le SQL est inutile » n'est pas « le SQL est impossible ».
    Les confondre ferait abstenir une question purement textuelle."""

    SQL_DISPONIBLE = "sql_disponible"
    SQL_RETIRE = "sql_retire"
    BASE_NON_SOLLICITEE = "base_non_sollicitee"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    manquants: tuple[str, ...] = ()  # dimensions demandées mais absentes du schéma
    intention: "IntentionSQL | None" = None  # conservée : sans elle, un refus est inexplicable

    @property
    def sql_autorise(self) -> bool:
        return self.verdict is Verdict.SQL_DISPONIBLE

    def motif(self) -> str:
        if self.verdict is Verdict.BASE_NON_SOLLICITEE:
            return "question sans dimension chiffrée : la base n'est pas sollicitée"
        if self.verdict is Verdict.SQL_DISPONIBLE:
            return "toutes les dimensions demandées sont couvertes par le schéma"
        return "dimensions absentes du schéma : " + ", ".join(self.manquants)

    def detail(self) -> str:
        """Motif + valeurs retenues, pour la trace et les échecs de test."""
        if self.intention is None:
            return self.motif()
        retenu = {k: v for k, v in self.intention.model_dump().items() if v not in (False, None)}
        return f"{self.motif()} | intention : {retenu}"


def valider_intention(intention: IntentionSQL) -> Decision:
    """Confronte ce que la question demande à la base à ce que la base contient.

    L'entrée vient d'un modèle et reste faillible ; le verdict, lui, est
    reproductible — donc testable.
    """
    if not intention.base_sollicitee:
        return Decision(Verdict.BASE_NON_SOLLICITEE, intention=intention)

    manquants = [
        f"{axe}={getattr(intention, axe)}"
        for axe, couvertes in CAPACITES.items()
        if getattr(intention, axe) not in couvertes
    ]
    manquants += [
        libelle for champ, libelle in FILTRES_INDISPONIBLES.items() if getattr(intention, champ)
    ]

    if manquants:
        return Decision(Verdict.SQL_RETIRE, tuple(manquants), intention=intention)
    return Decision(Verdict.SQL_DISPONIBLE, intention=intention)
