# src/rag/generation.py
"""Génération de la réponse : contexte, prompt, appel du modèle — sous contrat.

L'entrée est validée (`Question`, `SearchResult`) et la sortie est **structurée**
(`RAGAnswer`) au lieu d'être du texte libre. Un appelant peut donc distinguer une
réponse fondée d'un refus, et remonter aux fragments invoqués.

Les citations ne sont pas prises pour argent comptant : un validateur de sortie les
confronte aux fragments servis et **renvoie le modèle corriger**. Un contrat Pydantic
ne peut pas faire ce contrôle, il ne connaît pas le contexte du run.

Le prompt reste celui d'origine, au caractère près. Seuls changent pour le modèle le
préfixe d'identifiant sur chaque extrait, et le schéma JSON de `RAGAnswer`.
"""
import logging
from dataclasses import dataclass

import truststore
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.mistral import MistralModel
from pydantic_ai.providers.mistral import MistralProvider
from pydantic_ai.settings import ModelSettings

from config import MISTRAL_API_KEY, MODEL_NAME
from schemas import Question, RAGAnswer, SearchResult

truststore.inject_into_ssl()  # requis derrière un proxy qui inspecte le TLS

# Inchangé au caractère près : cette branche fait varier la structure de la sortie, pas
# la consigne. Ce que le modèle doit savoir — citer, s'abstenir — voyage déjà dans les
# `description` des champs de RAGAnswer. L'écrire ici serait un doublon, et surtout une
# seconde variable modifiée.
SYSTEM_PROMPT = """Tu es 'NBA Analyst AI', un assistant expert sur la ligue de basketball NBA.
Ta mission est de répondre aux questions des fans en animant le débat.

---
{context_str}
---

QUESTION DU FAN:
{question}

RÉPONSE DE L'ANALYSTE NBA:"""

MESSAGE_CONTEXTE_VIDE = (
    "Aucune information pertinente trouvée dans la base de connaissances pour cette question."
)
TEMPERATURE = 0.1  # température basse, pour des réponses factuelles basées sur le contexte

# Le problème observé est un *gel* — un appel bloqué plusieurs minutes — et non une
# limite de débit. Le défaut du SDK est de 300 s : c'est lui qu'on remplace. 45 s, une
# trentaine de fois la latence habituelle (~1,5 s). Ce délai porte sur CHAQUE requête,
# pas sur la génération entière : le pire cas vaut (1 + RELANCES_SORTIE) appels, et le
# seul garde-fou global reste `--delai-cas` du harnais.
DELAI_APPEL_S = 45

# Une seconde chance, pas davantage : le modèle voit pourquoi sa réponse a été refusée
# et corrige. Au-delà on paierait des appels pour un modèle qui ne comprend pas.
RELANCES_SORTIE = 1


@dataclass
class ContexteRecupere:
    """Ce dont le validateur de sortie a besoin : les identifiants réellement servis."""

    ids_servis: set[str]


_modele = MistralModel(
    MODEL_NAME,
    provider=MistralProvider(api_key=MISTRAL_API_KEY),
    settings=ModelSettings(temperature=TEMPERATURE, timeout=DELAI_APPEL_S),
)

agent = Agent(
    _modele,
    output_type=RAGAnswer,
    deps_type=ContexteRecupere,
    retries={"output": RELANCES_SORTIE},
)


@agent.output_validator
def citations_verifiables(ctx: RunContext[ContexteRecupere], reponse: RAGAnswer) -> RAGAnswer:
    """Refuse une réponse qui cite un fragment absent du contexte servi.

    En Python, pas dans le prompt : un modèle qui invente une citation peut tout aussi
    bien affirmer qu'elle est correcte. `ModelRetry` lui renvoie le motif — mieux
    qu'effacer les citations fautives en laissant la réponse fondée sur du vide.
    """
    inventees = [c for c in reponse.citations if c not in ctx.deps.ids_servis]
    if inventees:
        logging.warning(f"Citations inexistantes, réponse renvoyée au modèle : {inventees}")
        raise ModelRetry(
            f"Les identifiants {inventees} ne figurent pas dans le contexte fourni. "
            f"Utilise uniquement ceux entre crochets, ou abstiens-toi."
        )
    return reponse


def formater_contexte(fragments: list[SearchResult]) -> str:
    """Assemble les fragments récupérés pour le prompt.

    Chaque fragment est préfixé de son identifiant : c'est ce qui rend la citation
    possible, donc vérifiable.
    """
    if not fragments:
        logging.warning("Aucun contexte trouvé pour cette question.")
        return MESSAGE_CONTEXTE_VIDE
    return "\n\n---\n\n".join(
        f"[{f.id}] Source: {f.metadata.get('source', 'Inconnue')} (Score: {f.score:.1f}%)\n"
        f"Contenu: {f.text}"
        for f in fragments
    )


def generate_answer(search_results: list[dict], question: str) -> RAGAnswer:
    """Le chemin complet : contrats d'entrée, contexte, génération sous contrat.

    Rend un `RAGAnswer`, pas du texte : `texte_visible()` donne la chaîne que
    l'utilisateur lit et que l'évaluation note.
    """
    # Contrats d'ENTRÉE : arrêtés ici, avant de payer un appel au modèle.
    question = Question(texte=question).texte
    fragments = [SearchResult(**r) for r in search_results]

    prompt = SYSTEM_PROMPT.format(
        context_str=formater_contexte(fragments), question=question
    )
    try:
        resultat = agent.run_sync(
            prompt, deps=ContexteRecupere(ids_servis={f.id for f in fragments})
        )
        return resultat.output
    except Exception as e:
        # L'interface et le harnais attendent une réponse, pas une exception : rendue
        # comme une abstention, avec le message d'origine — le type seul ne dirait pas
        # si le modèle a épuisé ses relances ou mal formé sa sortie.
        logging.exception("Échec de la génération")
        return RAGAnswer(
            answer="Je suis désolé, une erreur technique m'empêche de répondre.",
            abstain=True,
            abstain_reason=f"Erreur technique : {type(e).__name__} — {e}"[:300],
        )
