# src/rag/schemas.py
"""Schémas Pydantic pour la sortie structurée du LLM (via Pydantic AI)."""
from pydantic import BaseModel, Field


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
