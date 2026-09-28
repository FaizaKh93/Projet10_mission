# src/schemas.py
"""Contrats Pydantic aux frontières du système.

**Pipeline** — `SourceDocument`, `Chunk`, `LotEmbeddings` : ce qui entre dans l'index et
ce qui en sort. **Système** — `Question`, `SearchResult`, `RAGAnswer` : l'entrée, le
contexte récupéré, la réponse.

Un contrat lève, il ne dégrade pas. L'indexation est hors ligne : s'arrêter coûte une
relance, un index dégradé se paie longtemps sans qu'on sache pourquoi.
"""
import math
import re
from typing import Annotated, Any

from config import EMBEDDING_DIM
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# Rejette les extractions résiduelles — « Page 1 », « --- » — que `if not contenu`
# laisse passer, n'attrapant que None et la chaîne vide.
MIN_CARACTERES_DOCUMENT = 50

# Via StringConstraints et non Field() : Field() n'a pas `strip_whitespace` et
# l'ignorerait sans rien signaler, laissant passer une chaîne d'espaces.
TexteNonVide = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


# ---------------------------------------------------------------- pipeline

class DocumentMetadata(BaseModel):
    """Provenance d'un document. `source` est ce qui s'affiche dans le prompt."""

    model_config = ConfigDict(extra="allow")  # le chargeur en ajoute selon le format

    source: TexteNonVide
    filename: TexteNonVide
    category: str = "root"
    full_path: str | None = None
    sheet: str | None = None  # feuille Excel, absente pour les autres formats


class SourceDocument(BaseModel):
    """Entrée du pipeline. Un fichier illisible produit un texte vide ou résiduel, qui
    coûterait des appels d'embedding pour un fragment inexploitable."""

    page_content: TexteNonVide
    metadata: DocumentMetadata

    @field_validator("page_content")
    @classmethod
    def contenu_exploitable(cls, v: str) -> str:
        if len(v) < MIN_CARACTERES_DOCUMENT:
            raise ValueError(
                f"contenu extrait trop court ({len(v)} caractères utiles, minimum "
                f"{MIN_CARACTERES_DOCUMENT}) — extraction probablement ratée"
            )
        return v


class Chunk(BaseModel):
    """Fragment prêt à être vectorisé.

    `id` relie un vecteur de l'index à son texte : sans lui, aucune citation vérifiable.
    """

    id: TexteNonVide
    text: TexteNonVide
    metadata: dict[str, Any]

    @field_validator("metadata")
    @classmethod
    def source_renseignee(cls, v: dict[str, Any]) -> dict[str, Any]:
        if not v.get("source"):
            raise ValueError("metadata['source'] manquante : fragment non traçable")
        return v

    @field_validator("id")
    @classmethod
    def identifiant_bien_forme(cls, v: str) -> str:
        doc, _, frag = v.partition("_")  # format du découpage : "<doc>_<fragment>"
        if not (doc.isdigit() and frag.isdigit()):
            raise ValueError(f"identifiant attendu sous la forme 'n_m', reçu {v!r}")
        return v


class LotEmbeddings(BaseModel):
    """Sortie du pipeline, et le contrat le plus important.

    Deux dégradations passaient sans déclencher d'erreur : un lot **plus court** que ses
    fragments décale tout ce qui suit ; des **vecteurs nuls** insérés à la place des
    manquants préservent l'alignement, donc franchissent tout contrôle de longueur.
    FAISS les accepte, leur similarité vaut 0 pour toute question — ces fragments sont
    comptés dans l'index et n'en ressortent jamais.
    """

    vecteurs: list[list[float]]
    fragments_envoyes: int = Field(gt=0)

    @field_validator("vecteurs")
    @classmethod
    def vecteurs_exploitables(cls, v: list[list[float]]) -> list[list[float]]:
        for i, vecteur in enumerate(v):
            if len(vecteur) != EMBEDDING_DIM:
                raise ValueError(f"vecteur {i} de dimension {len(vecteur)}, attendu {EMBEDDING_DIM}")
            if not any(x != 0.0 for x in vecteur):
                raise ValueError(f"vecteur {i} de norme nulle (lot en échec masqué)")
            if not all(math.isfinite(x) for x in vecteur):
                raise ValueError(f"vecteur {i} contient NaN ou infini")
        return v

    @model_validator(mode="after")
    def autant_de_vecteurs_que_de_fragments(self) -> "LotEmbeddings":
        # Contrôle relationnel : aucun champ pris isolément ne peut le voir.
        if len(self.vecteurs) != self.fragments_envoyes:
            raise ValueError(
                f"{len(self.vecteurs)} vecteurs pour {self.fragments_envoyes} fragments envoyés"
            )
        return self


# ---------------------------------------------------------------- système

class Question(BaseModel):
    """Entrée du système. Une question vide déclencherait recherche et génération pour
    rien ; une question démesurée gonflerait le prompt sans bénéfice."""

    texte: Annotated[
        str, StringConstraints(min_length=3, max_length=2000, strip_whitespace=True)
    ]


class SearchResult(BaseModel):
    """Fragment récupéré. `score` est une similarité cosinus en pourcentage.

    Borne basse à **-100 et non 0** : le produit scalaire de vecteurs normalisés vit
    dans [-1, 1], donc un fragment opposé à la question obtient un score négatif
    légitime.
    """

    id: TexteNonVide
    text: TexteNonVide
    score: float = Field(ge=-100, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RAGAnswer(BaseModel):
    """Sortie du système, structurée au lieu d'un texte libre.

    Les `description` ci-dessous ne sont pas de la documentation : Pydantic AI les
    transmet au modèle dans le schéma JSON, ce sont des instructions.
    """

    answer: str = Field(
        description="La réponse à la question, fondée uniquement sur le contexte fourni."
    )
    citations: list[str] = Field(
        default_factory=list,
        description="Identifiants des fragments qui soutiennent la réponse, sous la forme "
                    "1_14 — le nombre seul, sans crochets ni texte autour. "
                    "Laisser vide en cas d'abstention.",
    )
    abstain: bool = Field(
        default=False,
        description="Vrai si le contexte ne permet pas de répondre : information absente, "
                    "question ambiguë, ou donnée d'une granularité qui n'existe pas.",
    )
    abstain_reason: str | None = Field(
        default=None,
        description="Ce qui manque précisément pour répondre. Obligatoire si abstain est vrai.",
    )

    @field_validator("citations")
    @classmethod
    def normaliser_citations(cls, v: list[str]) -> list[str]:
        """Retire le décor d'une citation recopiée depuis le contexte.

        Observé sur un appel réel : `['[1_14] Sure_Station9370']`. Refuser cette forme
        coûterait une relance pour un simple habillage. La tolérance s'arrête là : on
        n'extrait que si la chaîne porte **exactement un** identifiant bien formé.
        """
        normalisees = []
        for citation in v:
            trouves = re.findall(r"\d+_\d+", citation)
            normalisees.append(trouves[0] if len(trouves) == 1 else citation)
        return normalisees

    @model_validator(mode="after")
    def abstention_motivee(self) -> "RAGAnswer":
        # Sans motif, l'utilisateur ne sait pas si la donnée manque ou si la question
        # était mal posée.
        if self.abstain and not (self.abstain_reason or "").strip():
            raise ValueError("une abstention doit être motivée (abstain_reason)")
        return self

    def texte_visible(self) -> str:
        """Ce que l'utilisateur lit, et ce que le juge note.

        Le motif porte la substance d'un refus (« ce joueur est absent des données ») :
        noter `answer` seul reviendrait à juger une phrase creuse.
        """
        if self.abstain and self.abstain_reason:
            return f"{self.answer}\n\n[Réponse incertaine] {self.abstain_reason}".strip()
        return self.answer
