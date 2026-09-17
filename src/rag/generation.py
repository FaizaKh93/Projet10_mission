# src/rag/generation.py
"""Génération de réponse via un agent Pydantic AI (Mistral), avec sortie
structurée validée (RAGAnswer) et vérification déterministe des citations."""
import logging

from pydantic_ai import Agent
from pydantic_ai.models.mistral import MistralModel
from pydantic_ai.providers.mistral import MistralProvider
from pydantic_ai.settings import ModelSettings

from config import MISTRAL_API_KEY, MODEL_NAME
from rag.schemas import RAGAnswer

# Prompt repris MOT POUR MOT du prototype d'origine (app/chat.py et eval/evaluate_ragas.py),
# volontairement non modifié : cette étape mesure l'effet de la couche Pydantic AI seule,
# pas celui d'un prompt amélioré. Ce qui doit être mis dans citations/abstain est décrit
# dans les Field(description=...) de RAGAnswer (schemas.py), transmis automatiquement au
# modèle via le JSON Schema de la sortie structurée.
SYSTEM_PROMPT = """Tu es 'NBA Analyst AI', un assistant expert sur la ligue de basketball NBA.
Ta mission est de répondre aux questions des fans en animant le débat.

---
{context_str}
---

QUESTION DU FAN:
{question}

RÉPONSE DE L'ANALYSTE NBA:"""

# Provider explicite (plutôt que la chaîne "mistral" qui lirait MISTRAL_API_KEY
# depuis l'environnement implicitement) - cohérent avec le reste du projet, qui
# passe toujours la clé explicitement
_model = MistralModel(MODEL_NAME, provider=MistralProvider(api_key=MISTRAL_API_KEY))

# output_type=RAGAnswer : force une sortie structurée validée par Pydantic plutôt
# qu'un texte libre (voir schemas.py) - construit une seule fois, réutilisé à chaque appel.
# On ne passe pas le paramètre system_prompt de l'Agent : comme le prototype d'origine,
# tout le template (persona + contexte + question) part dans un unique message user.
# D'où le nom SYSTEM_PROMPT conservé tel quel - c'est celui du prototype, même contenu.
# temperature=0.1 : valeur du prototype d'origine, reprise telle quelle (sans ça,
# l'agent utiliserait la valeur par défaut de Mistral et on introduirait une
# différence de comportement non voulue par rapport à la baseline).
agent = Agent(
    model=_model,
    output_type=RAGAnswer,
    model_settings=ModelSettings(temperature=0.1),
)


def _format_context(search_results: list[dict]) -> str:
    """Assemble les chunks récupérés en texte pour le prompt, chunk_id visible
    pour que le modèle puisse le citer dans RAGAnswer.citations."""
    return "\n\n---\n\n".join(
        f"chunk_id: {res['id']} | Source: {res['metadata'].get('source', 'Inconnue')} (Score: {res['score']:.1f}%)\n"
        f"Contenu: {res['text']}"
        for res in search_results
    ) or "Aucune information pertinente trouvée dans la base de connaissances pour cette question."


def verify_citations(answer: RAGAnswer, search_results: list[dict]) -> RAGAnswer:
    """Retire les chunk_id cités qui n'existent pas dans les chunks récupérés.

    Vérification déterministe faite en code, pas par le LLM (cf. schemas.py) : un
    chunk_id inventé est un signal d'hallucination. Fonction séparée de
    generate_answer() pour être testable sans appel API.
    """
    valid_ids = {res["id"] for res in search_results}
    invalid_citations = [c for c in answer.citations if c not in valid_ids]
    if invalid_citations:
        logging.warning(
            f"Citations invalides (chunk_id absent du contexte récupéré) : {invalid_citations}"
        )
        answer.citations = [c for c in answer.citations if c in valid_ids]
    return answer


def generate_answer(search_results: list[dict], question: str) -> RAGAnswer:
    """Génère une réponse structurée à partir des chunks récupérés et de la question."""
    context_str = _format_context(search_results)
    # Même assemblage que le prototype d'origine : le template complet dans un seul message
    user_prompt = SYSTEM_PROMPT.format(context_str=context_str, question=question)

    result = agent.run_sync(user_prompt)
    return verify_citations(result.output, search_results)
