# src/config.py
import os
from pathlib import Path
from dotenv import load_dotenv

# Racine du projet, calculée depuis l'emplacement de ce fichier :
# rend les chemins ci-dessous indépendants du répertoire depuis lequel on lance les commandes
PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
EMBEDDING_DIM = 1024                # Dimension fixe des vecteurs de mistral-embed (doc officielle Mistral)
MODEL_NAME = "mistral-small-latest" # Ou un autre modèle comme mistral-large-latest

# --- Configuration de l'Indexation ---
# INPUT_DATA_URL = os.getenv("INPUT_DATA_URL") # Décommentez si vous utilisez une URL
INPUT_DIR = str(PROJECT_ROOT / "data" / "inputs")       # Dossier pour les données sources
VECTOR_DB_DIR = str(PROJECT_ROOT / "data" / "vector_db") # Dossier pour l'index Faiss et les chunks
FAISS_INDEX_FILE = os.path.join(VECTOR_DB_DIR, "faiss_index.idx")
DOCUMENT_CHUNKS_FILE = os.path.join(VECTOR_DB_DIR, "document_chunks.pkl")

EXCEL_FILE = PROJECT_ROOT / "data" / "inputs" / "regular NBA.xlsx"  # Source de la base NBA

# Base relationnelle construite depuis l'Excel. Rangée avec data/vector_db/ car
# comme lui, elle est dérivée et entièrement régénérable (scripts/load_excel_to_db.py)
# — donc gitignorée.
NBA_DB_FILE = PROJECT_ROOT / "data" / "nba.db"
NBA_DB_URL = f"sqlite:///{NBA_DB_FILE}"

# Saison associée aux lignes de stats. HYPOTHÈSE DOCUMENTÉE : l'année n'apparaît
# nulle part dans le fichier. Déduite de deux indices concordants — les 2485 points
# de Shai Gilgeous-Alexander correspondent à son total 2024-25, et les PDF Reddit
# sont datés du 12/06/2025 en commentant les playoffs 2025.
SEASON = "2024-25"

CHUNK_SIZE = 1500                   # Taille des chunks en *caractères* (vise ~512 tokens)
CHUNK_OVERLAP = 150                 # Chevauchement en *caractères*
EMBEDDING_BATCH_SIZE = 32           # Taille des lots pour l'API d'embedding

# --- Configuration de la Recherche ---
SEARCH_K = 5                        # Nombre de documents à récupérer par défaut

# --- Configuration de l'Application ---
APP_TITLE = "NBA Analyst AI"
NAME = "NBA" # Nom à personnaliser dans l'interface