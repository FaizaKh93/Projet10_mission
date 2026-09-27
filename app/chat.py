# app/chat.py (version RAG)
import sys
import logging
from pathlib import Path

import streamlit as st

# Rend le paquet src/ importable quel que soit le répertoire de lancement.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# --- Importations depuis vos modules ---
try:
    from config import (
        MISTRAL_API_KEY, MODEL_NAME, SEARCH_K,
        APP_TITLE, NAME
    )
    from rag.generation import SYSTEM_PROMPT, formater_contexte, generer_reponse
    from rag.vector_store import VectorStoreManager
except ImportError as e:
    st.error(f"Erreur d'importation: {e}. Vérifiez la structure de vos dossiers et les fichiers dans 'src'.")
    st.stop()


# --- Configuration du Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')

# --- Configuration de l'API Mistral ---
# Le client lui-même vit dans rag/generation.py, partagé avec l'évaluation.
api_key = MISTRAL_API_KEY
model = MODEL_NAME

if not api_key:
    st.error("Erreur : Clé API Mistral non trouvée (MISTRAL_API_KEY). Veuillez la définir dans le fichier .env.")
    st.stop()


# --- Chargement du Vector Store (mis en cache) ---
@st.cache_resource  # Garde le manager chargé en mémoire pour la session
def get_vector_store_manager():
    logging.info("Tentative de chargement du VectorStoreManager...")
    try:
        manager = VectorStoreManager()
        # Vérifie si l'index a bien été chargé par le constructeur
        if manager.index is None or not manager.document_chunks:
            st.error("L'index vectoriel ou les chunks n'ont pas pu être chargés.")
            st.warning("Assurez-vous d'avoir exécuté 'python scripts/index.py' après avoir placé vos fichiers dans 'data/inputs'.")
            logging.error("Index Faiss ou chunks non trouvés/chargés par VectorStoreManager.")
            return None  # Retourne None si échec
        logging.info(f"VectorStoreManager chargé avec succès ({manager.index.ntotal} vecteurs).")
        return manager
    except FileNotFoundError:
        st.error("Fichiers d'index ou de chunks non trouvés.")
        st.warning("Veuillez exécuter 'python scripts/index.py' pour créer la base de connaissances.")
        logging.error("FileNotFoundError lors de l'init de VectorStoreManager.")
        return None
    except Exception as e:
        st.error(f"Erreur inattendue lors du chargement du VectorStoreManager: {e}")
        logging.exception("Erreur chargement VectorStoreManager")
        return None


vector_store_manager = get_vector_store_manager()

# --- Initialisation de l'historique de conversation ---
if "messages" not in st.session_state:
    # Message d'accueil initial
    st.session_state.messages = [{"role": "assistant", "content": f"Bonjour ! Je suis votre analyste IA pour la {NAME}. Posez-moi vos questions sur les équipes, les joueurs ou les statistiques, et je vous répondrai en me basant sur les données les plus récentes."}]

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
        st.stop()  # On arrête ici car on ne peut pas faire de RAG

    # 3. Rechercher le contexte dans le Vector Store
    try:
        logging.info(f"Recherche de contexte pour la question: '{prompt}' avec k={SEARCH_K}")
        search_results = vector_store_manager.search(prompt, k=SEARCH_K)
        logging.info(f"{len(search_results)} chunks trouvés dans le Vector Store.")
    except Exception as e:
        st.error(f"Une erreur est survenue lors de la recherche d'informations pertinentes: {e}")
        logging.exception(f"Erreur pendant vector_store_manager.search pour la query: {prompt}")
        search_results = []  # On continue sans contexte si la recherche échoue

    # 4. Formater le contexte pour le prompt LLM
    context_str = formater_contexte(search_results)

    # 5. Construire le prompt final pour l'API Mistral en utilisant le System Prompt RAG
    final_prompt_for_llm = SYSTEM_PROMPT.format(context_str=context_str, question=prompt)

    # Créer la liste de messages pour l'API (juste le prompt système/utilisateur combiné)
    messages_for_api = [
        # On pourrait séparer system et user, mais Mistral gère bien un long message user structuré
        {"role": "user", "content": final_prompt_for_llm}
    ]

    # === Fin de la logique RAG ===

    # 6. Afficher indicateur + Générer la réponse de l'assistant via LLM
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        message_placeholder.text("...")  # Indicateur simple

        # Génération de la réponse de l'assistant en utilisant le prompt augmenté
        response_content = generer_reponse(messages_for_api)

        # Affichage de la réponse complète
        message_placeholder.write(response_content)

    # 7. Ajouter la réponse de l'assistant à l'historique (pour affichage UI)
    st.session_state.messages.append({"role": "assistant", "content": response_content})

# Petit pied de page optionnel
st.markdown("---")
st.caption("Powered by Mistral AI & Faiss | Data-driven NBA Insights")
