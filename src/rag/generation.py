# src/rag/generation.py
"""Génération de réponse via un agent Pydantic AI (Mistral), avec sortie
structurée validée (RAGAnswer) et vérification déterministe des citations."""
import logging
from dataclasses import dataclass, field, replace

import logfire
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.mistral import MistralModel
from pydantic_ai.providers.mistral import MistralProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from config import MISTRAL_API_KEY, MODEL_NAME
from rag.sql_tool import RequeteRefusee, description_du_tool, executer_sql
from schemas import AnswerWithSQL, RAGAnswer, SearchResult, SQLResult

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
@dataclass
class TraceSQL:
    """Requêtes exécutées pendant un run. Une instance neuve par appel, passée en
    `deps` : deux appels simultanés ne mélangent pas leurs traces."""

    resultats: list[SQLResult] = field(default_factory=list)


agent = Agent(
    model=_model,
    output_type=RAGAnswer,
    deps_type=TraceSQL,
    model_settings=ModelSettings(temperature=0.1),
)


def _injecter_schema(ctx: RunContext[TraceSQL], tool_def: ToolDefinition) -> ToolDefinition:
    """Pose la description du tool au moment du run plutôt qu'à l'import.

    Appelée par Pydantic AI avant chaque run. Le décorateur évaluerait
    `description=` à l'import, ce qui exigerait que `data/nba.db` existe dès le
    chargement du module.
    """
    return replace(tool_def, description=description_du_tool())


@agent.tool(prepare=_injecter_schema)
def interroger_base_nba(ctx: RunContext[TraceSQL], requete: str) -> str:
    """Exécute une requête SQL de lecture sur la base NBA."""  # remplacée par _injecter_schema
    with logfire.span("sql_tool", **{"sql.query": requete}) as span:
        try:
            resultat = executer_sql(requete)
        except RequeteRefusee as e:
            span.set_attribute("sql.refused", str(e))
            # ModelRetry renvoie le message au modèle, qui corrige sa requête et
            # réessaie - d'où l'importance de messages d'erreur exploitables.
            raise ModelRetry(str(e)) from e
        span.set_attribute("sql.rows_returned", resultat.row_count)
        span.set_attribute("sql.execution_ms", resultat.execution_ms)
        span.set_attribute("sql.truncated", resultat.truncated)

    ctx.deps.resultats.append(resultat)
    return _formater_resultat(resultat)


def _formater_resultat(resultat: SQLResult) -> str:
    """Met les lignes en texte pour le modèle : en-tête, puis une ligne par tuple."""
    if not resultat.rows:
        return "Aucune ligne : la base ne contient pas cette donnée."
    entete = " | ".join(resultat.columns)
    lignes = [" | ".join(str(l[c]) for c in resultat.columns) for l in resultat.rows]
    texte = "\n".join([entete, *lignes])
    if resultat.truncated:
        # Sans cet avertissement, le modèle conclurait « il y a 50 joueurs » sur un LIMIT 50
        texte += f"\n\n[Coupé à {resultat.row_count} lignes : il en existe davantage.]"
    return texte


def _format_context(search_results: list[SearchResult]) -> str:
    """Assemble les chunks récupérés en texte pour le prompt, chunk_id visible
    pour que le modèle puisse le citer dans RAGAnswer.citations."""
    return "\n\n---\n\n".join(
        f"chunk_id: {res.id} | Source: {res.metadata.get('source', 'Inconnue')} (Score: {res.score:.1f}%)\n"
        f"Contenu: {res.text}"
        for res in search_results
    ) or "Aucune information pertinente trouvée dans la base de connaissances pour cette question."


def verify_citations(answer: RAGAnswer, search_results: list[SearchResult]) -> RAGAnswer:
    """Retire les chunk_id cités qui n'existent pas dans les chunks récupérés.

    Vérification déterministe faite en code, pas par le LLM (cf. schemas.py) : un
    chunk_id inventé est un signal d'hallucination. Fonction séparée de
    generate_answer() pour être testable sans appel API.
    """
    valid_ids = {res.id for res in search_results}
    invalid_citations = [c for c in answer.citations if c not in valid_ids]
    if invalid_citations:
        logging.warning(
            f"Citations invalides (chunk_id absent du contexte récupéré) : {invalid_citations}"
        )
        answer.citations = [c for c in answer.citations if c in valid_ids]
    return answer


def generate_answer(search_results: list[SearchResult], question: str) -> AnswerWithSQL:
    """Génère une réponse structurée à partir des chunks récupérés et de la question.

    L'agent dispose des deux sources et choisit : les chunks pour le qualitatif,
    le tool SQL pour le chiffré. Les requêtes qu'il a réellement exécutées sont
    relevées dans la trace, puis jointes à la réponse.
    """
    context_str = _format_context(search_results)
    # Même assemblage que le prototype d'origine : le template complet dans un seul message
    user_prompt = SYSTEM_PROMPT.format(context_str=context_str, question=question)

    trace = TraceSQL()
    result = agent.run_sync(user_prompt, deps=trace)
    answer = verify_citations(result.output, search_results)

    # sql_queries vient de la trace, jamais d'une déclaration du modèle (cf. schemas.py)
    return AnswerWithSQL(
        **answer.model_dump(),
        sql_queries=[r.query for r in trace.resultats],
    )
