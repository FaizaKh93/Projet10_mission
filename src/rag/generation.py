# src/rag/generation.py
"""Génération de la réponse : assemblage du contexte et du prompt, appel du modèle.

Cette logique vit dans le corps du script Streamlit (`app/chat.py`, étapes 4 à 6) ; elle
est extraite ici pour être **appelable** par l'évaluation et les tests. Mêmes étapes,
même prompt, même température, même sortie en texte brut.

Seul écart : le bloc `except` de `generer_reponse()` ne double plus son message d'un
`st.error()`, l'affichage restant du ressort de `app/chat.py`.
"""
import logging

import truststore
from mistralai.client import Mistral

from config import MISTRAL_API_KEY, MODEL_NAME

truststore.inject_into_ssl()  # requis derrière un proxy qui inspecte le TLS

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

client = Mistral(api_key=MISTRAL_API_KEY)


def formater_contexte(search_results: list[dict]) -> str:
    """Assemble les chunks récupérés pour le prompt.

    Source et score, puis contenu. Aucun identifiant de chunk n'est transmis : une
    réponse ne peut donc pas être rattachée à un passage précis.
    """
    if not search_results:
        logging.warning("Aucun contexte trouvé pour cette question.")
        return MESSAGE_CONTEXTE_VIDE
    return "\n\n---\n\n".join(
        f"Source: {res['metadata'].get('source', 'Inconnue')} (Score: {res['score']:.1f}%)\n"
        f"Contenu: {res['text']}"
        for res in search_results
    )


def generer_reponse(prompt_messages: list[dict]) -> str:
    """Envoie le prompt (qui inclut le contexte) à l'API Mistral et rend le texte."""
    if not prompt_messages:
        logging.warning("Tentative de génération de réponse avec un prompt vide.")
        return "Je ne peux pas traiter une demande vide."
    try:
        logging.info(
            f"Appel à l'API Mistral modèle '{MODEL_NAME}' avec {len(prompt_messages)} message(s)."
        )
        response = client.chat.complete(
            model=MODEL_NAME,
            messages=prompt_messages,
            temperature=TEMPERATURE,
        )
        if response.choices and len(response.choices) > 0:
            logging.info("Réponse reçue de l'API Mistral.")
            return response.choices[0].message.content
        logging.warning("L'API n'a pas retourné de choix valide.")
        return "Désolé, je n'ai pas pu générer de réponse valide pour le moment."
    except Exception:
        logging.exception("Erreur API Mistral pendant chat.complete")
        return "Je suis désolé, une erreur technique m'empêche de répondre. Veuillez réessayer plus tard."


def generate_answer(search_results: list[dict], question: str) -> str:
    """Le chemin complet : contexte, prompt, génération. Rend du **texte brut**.

    Aucune structure, aucune citation vérifiable, aucun signal d'abstention.
    """
    final_prompt = SYSTEM_PROMPT.format(
        context_str=formater_contexte(search_results), question=question
    )
    # Un seul message « user » plutôt qu'un message système séparé : Mistral traite bien
    # un long message utilisateur structuré.
    return generer_reponse([{"role": "user", "content": final_prompt}])
