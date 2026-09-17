# app/chat.py
import streamlit as st
import os
import sys
import logging
from pathlib import Path

import logfire
import truststore
from dotenv import load_dotenv

# Doit précéder logfire.configure() : derrière un proxy qui intercepte le TLS,
# l'export des traces échoue sinon (silencieusement, avec un simple warning).
# Ne pas compter sur l'injection faite à l'import de rag.vector_store : elle
# dépendrait de l'ordre des imports.
truststore.inject_into_ssl()

# Rend le package src/ importable, quel que soit le répertoire depuis lequel cette appli est lancée
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# --- Importations depuis vos modules ---
try:
    from config import (
        MISTRAL_API_KEY, MODEL_NAME, SEARCH_K,
        APP_TITLE, NAME
    )
    from rag.generation import generate_answer
    from rag.vector_store import VectorStoreManager
except ImportError as e:
    st.error(f"Erreur d'importation: {e}. Vérifiez la structure de vos dossiers et les fichiers dans 'src'.")
    st.stop()

# --- Observabilité Logfire ---
# NB: ces 2 lignes sont volontairement dupliquées à l'identique dans
# eval/evaluate_ragas.py (pas de module partagé) - si on les modifie ici, penser à
# faire la même chose là-bas.
# send_to_logfire="if-token-present" : n'envoie rien tant qu'aucun token Logfire
# n'est configuré, plutôt que d'échouer ou de demander une authentification
logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()  # trace automatiquement les appels de l'agent (rag/generation.py)


# --- Configuration du Logging ---
# Note: Streamlit peut avoir sa propre gestion de logs. Configurer ici est une bonne pratique.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Configuration de l'API Mistral ---
api_key = MISTRAL_API_KEY
model = MODEL_NAME

if not api_key:
    st.error("Erreur : Clé API Mistral non trouvée (MISTRAL_API_KEY). Veuillez la définir dans le fichier .env.")
    st.stop()

# Le client Mistral n'est plus instancié ici : il est encapsulé dans l'agent
# Pydantic AI de src/rag/generation.py (appelé via generate_answer()).

# --- Chargement du Vector Store (mis en cache) ---
@st.cache_resource # Garde le manager chargé en mémoire pour la session
def get_vector_store_manager():
    logging.info("Tentative de chargement du VectorStoreManager...")
    try:
        manager = VectorStoreManager()
        # Vérifie si l'index a bien été chargé par le constructeur
        if manager.index is None or not manager.document_chunks:
            st.error("L'index vectoriel ou les chunks n'ont pas pu être chargés.")
            st.warning("Assurez-vous d'avoir exécuté 'python indexer.py' après avoir placé vos fichiers dans le dossier 'inputs'.")
            logging.error("Index Faiss ou chunks non trouvés/chargés par VectorStoreManager.")
            return None # Retourne None si échec
        logging.info(f"VectorStoreManager chargé avec succès ({manager.index.ntotal} vecteurs).")
        return manager
    except FileNotFoundError:
         st.error("Fichiers d'index ou de chunks non trouvés.")
         st.warning("Veuillez exécuter 'python indexer.py' pour créer la base de connaissances.")
         logging.error("FileNotFoundError lors de l'init de VectorStoreManager.")
         return None
    except Exception as e:
        st.error(f"Erreur inattendue lors du chargement du VectorStoreManager: {e}")
        logging.exception("Erreur chargement VectorStoreManager")
        return None

vector_store_manager = get_vector_store_manager()

# Le prompt système vit maintenant dans src/rag/generation.py (source unique,
# partagée avec eval/evaluate_ragas.py pour que l'évaluation teste bien ce que
# l'app fait réellement).

# --- Initialisation de l'historique de conversation ---
if "messages" not in st.session_state:
    # Message d'accueil initial
    st.session_state.messages = [{"role": "assistant", "content": f"Bonjour ! Je suis votre analyste IA pour la {NAME}. Posez-moi vos questions sur les équipes, les joueurs ou les statistiques, et je vous répondrai en me basant sur les données les plus récentes."}]

# La génération vit maintenant dans src/rag/generation.py : generate_answer()
# renvoie un RAGAnswer validé (answer, citations, abstain, abstain_reason) au lieu
# d'un texte libre.

# --- Interface Utilisateur Streamlit ---
st.title(APP_TITLE)
st.caption(f"Assistant virtuel pour {NAME} | Modèle: {model}")

# Affichage des messages de l'historique (pour l'UI)
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

# Zone de saisie utilisateur
if prompt := st.chat_input(f"Posez votre question sur la {NAME}..."):
    # 1. Ajouter et afficher le message de l'utilisateur
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    # === Début de la logique RAG ===

    # 2. Vérifier si le Vector Store est disponible
    if vector_store_manager is None:
        st.error("Le service de recherche de connaissances n'est pas disponible. Impossible de traiter votre demande.")
        logging.error("VectorStoreManager non disponible pour la recherche.")
        # On arrête ici car on ne peut pas faire de RAG
        st.stop()

    # 3. Rechercher le contexte dans le Vector Store
    try:
        logging.info(f"Recherche de contexte pour la question: '{prompt}' avec k={SEARCH_K}")
        search_results = vector_store_manager.search(prompt, k=SEARCH_K)
        logging.info(f"{len(search_results)} chunks trouvés dans le Vector Store.")
    except Exception as e:
        st.error(f"Une erreur est survenue lors de la recherche d'informations pertinentes: {e}")
        logging.exception(f"Erreur pendant vector_store_manager.search pour la query: {prompt}")
        search_results = [] # On continue sans contexte si la recherche échoue

    if not search_results:
        logging.warning(f"Aucun contexte trouvé pour la query: {prompt}")

    # === Fin de la logique RAG ===

    # 4. Afficher indicateur + Générer la réponse via l'agent Pydantic AI
    # (assemblage du contexte + prompt + validation des citations : voir rag/generation.py)
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        message_placeholder.text("...") # Indicateur simple

        try:
            answer = generate_answer(search_results, prompt)
            response_content = answer.answer
        except Exception as e:
            st.error(f"Erreur lors de la génération de la réponse : {e}")
            logging.exception("Erreur pendant generate_answer")
            answer = None
            response_content = "Je suis désolé, une erreur technique m'empêche de répondre. Veuillez réessayer plus tard."

        message_placeholder.write(response_content)

        # Rend visible la sortie structurée : abstention explicite et chunks cités
        # (citations déjà filtrées côté generation.py si le modèle en a inventé)
        if answer is not None:
            if answer.abstain:
                st.warning(f"Réponse incertaine : {answer.abstain_reason or 'raison non précisée'}")
            if answer.citations:
                st.caption(f"Sources citées (chunk_id) : {', '.join(answer.citations)}")

    # 5. Ajouter la réponse de l'assistant à l'historique (pour affichage UI)
    st.session_state.messages.append({"role": "assistant", "content": response_content})

# Petit pied de page optionnel
st.markdown("---")
st.caption("Powered by Mistral AI & Faiss | Data-driven NBA Insights")