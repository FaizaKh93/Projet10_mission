# src/rag/pipeline.py
"""La chaîne complète : router, collecter, répondre.

Un seul point d'entrée — `repondre()` — appelé à l'identique par l'interface et par
l'évaluation. C'est ce qui garantit qu'on mesure bien ce que l'application fait.

    question
       │
       ├─ [1] routage      décide de la ou des sources, AVANT toute collecte
       ├─ [2] collecte     recherche vectorielle si la route l'inclut
       └─ [3] réponse      l'outil SQL n'est proposé que si la route l'inclut

Le principe qui tient l'ensemble : **le modèle propose, le code dispose**. Le routeur
choisit mais ne collecte pas ; le modèle écrit le SQL mais ne l'exécute pas ; il cite
mais ses citations sont vérifiées.

Ce que le routage fait : éviter d'envoyer une question chiffrée vers une recherche
sémantique, et l'inverse. Ce qu'il ne fait pas : savoir si l'information existe.
Décider avant de chercher, c'est décider sans savoir ce qu'on trouvera — l'abstention
reste la responsabilité du nœud de réponse.
"""
import logging
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.mistral import MistralModel
from pydantic_ai.providers.mistral import MistralProvider
from pydantic_ai.settings import ModelSettings

from config import MISTRAL_API_KEY, MODEL_NAME, SEARCH_K
from rag.generation import ContexteRecupere, generate_answer
from rag.sql_tool import profil_capacites
from schemas import RAGAnswer, SQLResult


# ----------------------------------------------------- [1] routage

DELAI_ROUTAGE_S = 15

Source = Literal["documents", "base", "les_deux", "aucune"]


class Route(BaseModel):
    """La décision du routeur.

    Les `description` partent au modèle dans le schéma JSON : ce sont des
    instructions, pas de la documentation.
    """

    source: Source = Field(
        description=(
            "Où chercher la réponse. "
            "'documents' : opinions, débats, commentaires de fans — recherche sémantique. "
            "'base' : chiffres, totaux, classements, comparaisons statistiques. "
            "'les_deux' : la question exige d'identifier quelqu'un dans les discussions "
            "PUIS de chercher son chiffre. "
            "'aucune' : la question ne relève d'aucune des deux sources."
        )
    )
    motif: str = Field(
        description="En une phrase, ce qui justifie ce choix.",
    )


_modele = MistralModel(
    MODEL_NAME,
    provider=MistralProvider(api_key=MISTRAL_API_KEY),
    settings=ModelSettings(temperature=0.0, timeout=DELAI_ROUTAGE_S),  # 0 : décision, pas rédaction
)

routeur = Agent(_modele, output_type=Route, retries={"output": 0})


def _consigne() -> str:
    """Construite à chaque appel : le profil de la base doit refléter la base actuelle,
    pas celle du jour où ce fichier a été écrit."""
    return (
        "Tu orientes une question vers la bonne source d'information.\n\n"
        "DEUX SOURCES SONT DISPONIBLES.\n\n"
        "1. Les documents : quatre fils de discussion Reddit sur les playoffs NBA. "
        "Opinions, débats, jugements de fans. Aucune statistique fiable.\n\n"
        f"2. La base de données.\n{profil_capacites()}\n\n"
        "Choisis la source. Si la question demande un chiffre que la base ne peut pas "
        "fournir — voir ses limites ci-dessus — choisis tout de même 'base' : c'est là "
        "que l'absence pourra être constatée."
    )


def router(question: str) -> Route:
    """Décide de la source. En cas d'échec, interroge les deux plutôt que de deviner.

    Une panne du routeur ne doit pas faire perdre la question : 'les_deux' coûte une
    collecte de plus, mais ne prive d'aucune source.
    """
    try:
        resultat = routeur.run_sync(question, instructions=_consigne())
        logging.info(f"Routage : {resultat.output.source} — {resultat.output.motif}")
        return resultat.output
    except Exception as e:
        logging.exception("Échec du routage, repli sur les deux sources")
        return Route(source="les_deux", motif=f"routage indisponible ({type(e).__name__})")


# ------------------------- [2] collecte et [3] réponse

@dataclass
class Reponse:
    """Le résultat complet d'une question : la réponse, et le chemin parcouru pour
    l'obtenir. Ce second volet n'est pas décoratif — c'est lui qui rend le routage
    mesurable et une réponse diagnosticable."""

    reponse: RAGAnswer
    route: Route
    fragments: list = field(default_factory=list)
    requetes: list[SQLResult] = field(default_factory=list)


def repondre(vector_store_manager, question: str) -> Reponse:
    """Enchaîne les trois nœuds.

    La recherche vectorielle n'est lancée que si la route l'inclut : sur une question
    purement chiffrée, cela évite un appel d'embedding facturé pour un contexte que le
    modèle n'utilisera pas.
    """
    route = router(question)
    interroger_documents = route.source in ("documents", "les_deux")
    interroger_base = route.source in ("base", "les_deux")

    fragments = []
    if interroger_documents:
        try:
            fragments = vector_store_manager.search(question, k=SEARCH_K)
            logging.info(f"{len(fragments)} fragments récupérés.")
        except Exception:
            # On poursuit sans contexte vectoriel : le nœud de réponse s'abstiendra
            # s'il n'a rien d'autre, ce qui vaut mieux qu'une question perdue.
            logging.exception("Échec de la recherche vectorielle")

    # Le contexte est fourni pour être relu : il porte les requêtes exécutées par
    # l'outil pendant le run.
    contexte = ContexteRecupere(ids_servis=set(), base_autorisee=interroger_base)
    reponse = generate_answer(fragments, question, contexte=contexte)

    return Reponse(
        reponse=reponse, route=route, fragments=fragments, requetes=contexte.requetes
    )
