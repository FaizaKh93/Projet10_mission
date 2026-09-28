# src/config.py
import os
from dotenv import load_dotenv

# Charger les variables d'environnement du fichier .env
load_dotenv()

# --- Clé API ---
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
if not MISTRAL_API_KEY:
    print("⚠️ Attention: La clé API Mistral (MISTRAL_API_KEY) n'est pas définie dans le fichier .env")
    # Vous pouvez choisir de lever une exception ici ou de continuer avec des fonctionnalités limitées
    # raise ValueError("Clé API Mistral manquante. Veuillez la définir dans le fichier .env")

# --- Modèles Mistral ---
EMBEDDING_MODEL = "mistral-embed"
EMBEDDING_DIM = 1024                # dimension des vecteurs de ce modèle, vérifiée sur l'index
MODEL_NAME = "mistral-small-latest" # Ou un autre modèle comme mistral-large-latest

# --- Configuration de l'Indexation ---
# INPUT_DATA_URL = os.getenv("INPUT_DATA_URL") # Décommentez si vous utilisez une URL
# Chemins résolus depuis la racine du dépôt : la référence les écrit relatifs à son
# propre dossier, alors qu'ici scripts/ et eval/ s'exécutent depuis ailleurs. C'est la
# seule adaptation de ce fichier.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT_DIR = os.path.join(PROJECT_ROOT, "data", "inputs")
VECTOR_DB_DIR = os.path.join(PROJECT_ROOT, "data", "vector_db")
FAISS_INDEX_FILE = os.path.join(VECTOR_DB_DIR, "faiss_index.idx")
DOCUMENT_CHUNKS_FILE = os.path.join(VECTOR_DB_DIR, "document_chunks.pkl")

# Seules les extensions listées ici sont indexées. Le classeur de statistiques en est
# exclu : l'évaluation a montré que la feuille contenant les données (143 des 302
# fragments de l'index) n'était jamais récupérée, les feuilles qui *décrivent* les
# données sortant à sa place. Ces fragments occupaient la moitié de l'index sans jamais
# être lus, et prenaient sur les questions mixtes des places du top-5 aux fils Reddit.
# La recherche vectorielle est faite pour du texte narratif ; les chiffres seront
# atteints autrement.
EXTENSIONS_INDEXEES = {".pdf"}

CHUNK_SIZE = 1500                   # Taille des chunks en *caractères* (vise ~512 tokens)
CHUNK_OVERLAP = 150                 # Chevauchement en *caractères*
EMBEDDING_BATCH_SIZE = 32           # Taille des lots pour l'API d'embedding

# --- Configuration de la Recherche ---
SEARCH_K = 5                        # Nombre de documents à récupérer par défaut

# --- Configuration de la Base de Données ---
DATABASE_DIR = "database"
DATABASE_FILE = os.path.join(DATABASE_DIR, "interactions.db")
DATABASE_URL = f"sqlite:///{DATABASE_FILE}" # URL pour SQLAlchemy

# --- Configuration de l'Application ---
APP_TITLE = "NBA Analyst AI"
NAME = "NBA" # Nom à personnaliser dans l'interface