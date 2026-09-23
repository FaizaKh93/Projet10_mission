# src/schemas.py
"""Contrats de données du pipeline RAG, validés par Pydantic.

Partagé par loading/ et rag/ : placé à la racine de src/ pour que le chargement
(loading) n'ait pas à importer depuis le RAG, ce qui inverserait la dépendance
logique (c'est rag/ qui consomme ce que loading/ produit).

Parcours des données et modèle correspondant :
    fichier brut -> SourceDocument -> TextChunk -> EmbeddedChunk -> index Faiss
    question -> SearchResult (chunks récupérés) -> RAGAnswer (réponse du modèle)
"""
from pydantic import BaseModel, Field, field_validator, model_validator

# Nombre minimal de caractères non blancs pour qu'un document extrait soit jugé
# exploitable. Rejette les extractions résiduelles type "\nPage 1\n" ou "---" que
# le test `if not extracted_content` laisse passer (il n'attrape que None et "").
MIN_CARACTERES_DOCUMENT = 50


class SourceDocument(BaseModel):
    """Document extrait d'un fichier source (PDF, feuille Excel...), avant découpage."""

    page_content: str
    metadata: dict

    @field_validator("page_content")
    @classmethod
    def contenu_exploitable(cls, v: str) -> str:
        if len(v.strip()) < MIN_CARACTERES_DOCUMENT:
            raise ValueError(
                f"contenu extrait trop court ({len(v.strip())} caractères utiles, "
                f"minimum {MIN_CARACTERES_DOCUMENT}) - extraction probablement ratée"
            )
        return v

    @field_validator("metadata")
    @classmethod
    def source_renseignee(cls, v: dict) -> dict:
        if not v.get("source"):
            raise ValueError("metadata['source'] manquante : le document serait non traçable")
        return v


class TextChunk(BaseModel):
    """Fragment de document prêt à être vectorisé."""

    id: str = Field(min_length=1)
    text: str
    metadata: dict

    @field_validator("text")
    @classmethod
    def texte_non_vide(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("chunk vide : rien à vectoriser")
        return v

    @field_validator("metadata")
    @classmethod
    def source_renseignee(cls, v: dict) -> dict:
        if not v.get("source"):
            raise ValueError("metadata['source'] manquante : citation impossible à tracer")
        return v


class EmbeddedChunk(BaseModel):
    """Chunk associé à son vecteur. Interdit explicitement le vecteur nul : une
    erreur d'embedding ne doit jamais être convertie en donnée fabriquée, sinon le
    chunk entre dans l'index et devient définitivement irrécupérable (score 0)."""

    id: str = Field(min_length=1)
    embedding: list[float]

    @field_validator("embedding")
    @classmethod
    def vecteur_valide(cls, v: list[float]) -> list[float]:
        from config import EMBEDDING_DIM

        if len(v) != EMBEDDING_DIM:
            raise ValueError(f"dimension {len(v)} au lieu de {EMBEDDING_DIM} attendues")
        if not any(v):
            raise ValueError("vecteur nul : embedding manquant déguisé en donnée valide")
        return v


class SearchResult(BaseModel):
    """Chunk renvoyé par la recherche vectorielle, juste avant injection dans le prompt."""

    id: str = Field(min_length=1)
    text: str
    score: float = Field(ge=-100, le=100)  # similarité cosinus en %, peut être négative
    metadata: dict
    raw_score: float | None = None


class TeamRow(BaseModel):
    """Une franchise, lue depuis la feuille « Equipe »."""

    code: str = Field(min_length=2, max_length=4)
    name: str = Field(min_length=3)


class ReportRow(BaseModel):
    """Un document qualitatif (PDF Reddit), avant insertion en base.

    Réutilise le seuil de SourceDocument : une extraction qui rend moins de
    caractères utiles a échoué, et insérer « \\nPage 1\\n » en base reviendrait à
    enregistrer un échec d'OCR comme une source valide.
    """

    title: str = Field(min_length=3)
    source: str = Field(min_length=2)
    file_name: str = Field(min_length=3)
    content: str = Field(min_length=MIN_CARACTERES_DOCUMENT)


class PlayerRow(BaseModel):
    """Une ligne joueur de la feuille « Données NBA », avant insertion en base.

    Ne valide que ce qui pourrait être silencieusement faux ; les types sont déjà
    garantis par les tables STRICT côté SQLite.

    Deux règles volontairement ABSENTES, car elles paraissent raisonnables mais
    rejetteraient des données valides (vérifié sur le fichier réel) :
    - « aucune valeur négative » : plus_minus est négatif pour 325 joueurs,
      netrtg pour 327, pie pour 9 — un différentiel négatif est normal ;
    - « pourcentages entre 0 et 100 » : efg_pct peut atteindre 150 % puisque
      EFG% = (FGM + 0,5 × 3PM) / FGA.

    Écartée aussi : « gp <= 82 ». La saison régulière compte habituellement 82
    matchs, mais elle a été écourtée (50 en 1998-99, 66 en 2011-12, 72 en 2020-21),
    et un joueur transféré peut dépasser ce total, les calendriers des deux équipes
    ne coïncidant pas.
    """

    full_name: str = Field(min_length=2)
    team_code: str = Field(min_length=2, max_length=4)
    games_played: int = Field(ge=0)
    wins_total: int = Field(ge=0)
    losses_total: int = Field(ge=0)
    fgm_total: int = Field(ge=0)
    fga_total: int = Field(ge=0)
    three_pm_total: int = Field(ge=0)
    three_pa_total: int = Field(ge=0)
    ftm_total: int = Field(ge=0)
    fta_total: int = Field(ge=0)

    @model_validator(mode="after")
    def tirs_reussis_inferieurs_aux_tentes(self) -> "PlayerRow":
        """On ne peut pas réussir plus de tirs qu'on n'en tente.

        Vérifié sur les 569 lignes du fichier actuel, pour les trois familles de
        tirs. C'est ce qui identifie la colonne à l'en-tête corrompu : si un futur
        export décalait les colonnes, l'ingestion s'arrêterait ici plutôt que de
        remplir three_pm_total avec une autre statistique.
        """
        for reussis, tentes, libelle in (
            (self.fgm_total, self.fga_total, "fgm/fga"),
            (self.three_pm_total, self.three_pa_total, "three_pm/three_pa"),
            (self.ftm_total, self.fta_total, "ftm/fta"),
        ):
            if reussis > tentes:
                raise ValueError(
                    f"{libelle} : {reussis} réussis pour {tentes} tentés — "
                    "colonnes probablement décalées"
                )
        return self

    @model_validator(mode="after")
    def victoires_et_defaites_couvrent_les_matchs(self) -> "PlayerRow":
        """Identité structurelle : tout match joué est une victoire ou une défaite.

        Il n'y a pas de match nul en NBA, donc w + l == gp quelle que soit la
        longueur de la saison. Un écart signalerait des colonnes désalignées.
        """
        if self.wins_total + self.losses_total != self.games_played:
            raise ValueError(
                f"wins ({self.wins_total}) + losses ({self.losses_total}) != "
                f"games_played ({self.games_played})"
            )
        return self


class SQLResult(BaseModel):
    """Résultat d'une requête SQL exécutée par le tool, renvoyé à l'agent.

    `truncated` signale que la requête renvoyait plus de lignes que la limite :
    l'agent doit alors savoir que sa réponse porte sur un extrait, pas sur
    l'ensemble — sinon il conclurait « il y a 50 joueurs » sur un LIMIT 50.
    """

    query: str
    columns: list[str]
    rows: list[dict]
    row_count: int
    truncated: bool = False
    execution_ms: float


class RAGAnswer(BaseModel):
    """Réponse structurée attendue du modèle, au lieu d'un texte libre.

    citations référence des chunk_id (ex. "0_3") plutôt que du texte cité, pour
    permettre une vérification déterministe en code Python (le chunk_id cité
    existe-t-il bien parmi les chunks récupérés ?) plutôt qu'une recherche de
    sous-chaîne fragile sur des valeurs numériques.

    Pas de champ "grounded: bool" auto-déclaré par le LLM : un modèle qui
    hallucine peut tout aussi bien répondre grounded=true à tort. La fiabilité
    de la réponse se vérifie via "citations" (code), pas via une auto-évaluation
    du modèle lui-même.
    """

    answer: str = Field(
        description="La réponse à la question, basée uniquement sur le contexte fourni."
    )
    citations: list[str] = Field(
        default_factory=list,
        description="Les chunk_id (ex. '0_3') du contexte qui justifient la réponse.",
    )
    abstain: bool = Field(
        default=False,
        description="True si le contexte fourni ne permet pas de répondre de façon fiable "
        "(donnée absente, question ambiguë, anomalie détectée dans les données).",
    )
    abstain_reason: str | None = Field(
        default=None,
        description="Si abstain=True, explique brièvement pourquoi (donnée absente, "
        "question ambiguë, anomalie de données...).",
    )


class AnswerWithSQL(RAGAnswer):
    """RAGAnswer enrichie des requêtes SQL réellement exécutées pendant le run.

    Sous-classe et non champ supplémentaire de RAGAnswer : le modèle reçoit le
    JSON Schema de RAGAnswer (4 champs), donc il ne voit jamais sql_queries et ne
    peut pas le remplir. Le code le renseigne après coup depuis la trace du tool.

    Même raisonnement que pour l'absence de "grounded" : une requête auto-déclarée
    par le modèle pourrait être inventée, alors qu'une requête lue dans la trace a
    forcément été exécutée.
    """

    sql_queries: list[str] = Field(
        default_factory=list,
        description="Requêtes SQL exécutées par le tool, relevées par le code.",
    )
    sql_results: list[str] = Field(
        default_factory=list,
        description="Ce que le tool a renvoyé au modèle, mot pour mot. C'est la "
        "partie du contexte qui vient de la base : sans elle, une évaluation de "
        "fidélité jugerait la réponse sur les seuls chunks vectoriels.",
    )
