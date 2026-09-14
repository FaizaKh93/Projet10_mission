# Projet 10 — Assistant IA d'analyse de performance NBA (SportSee)

## Contexte

SportSee est une startup spécialisée dans l'IA appliquée à l'analyse de performance sportive. Ce projet fait évoluer un prototype d'assistant conversationnel (RAG + Mistral) pour qu'il puisse répondre de façon fiable à des questions analytiques précises sur des statistiques NBA (ex. *"Quel joueur a le meilleur pourcentage de réussite à 3 points sur les 5 derniers matchs ?"*), et pas seulement à des questions générales sur du contenu textuel.

## Installation (environnement reproductible avec uv)

Le projet est géré avec [uv](https://docs.astral.sh/uv/) — `pyproject.toml` et `uv.lock` remplacent volontairement un `requirements.txt` classique : ils figent aussi les dépendances transitives (ce qu'un simple `requirements.txt` ne garantit pas), tout en restant équivalents pour l'installation.

```bash
# 1. Installer les dépendances dans un environnement virtuel dédié (.venv)
uv sync

# 2. Configurer la clé API Mistral
# Créer un fichier .env à la racine avec :
# MISTRAL_API_KEY="votre_clé"
# (clé à obtenir sur https://console.mistral.ai/)
```

Python 3.11 est pinné (`.python-version`) — version choisie pour la compatibilité de l'écosystème ML (`faiss-cpu`, `torch`/`easyocr` n'ont pas encore de wheels pour les versions Python les plus récentes).

## Lancer le projet

```bash
# Construire/reconstruire l'index vectoriel à partir des documents dans data/inputs/
uv run python scripts/index.py

# Lancer l'application Streamlit
uv run streamlit run app/chat.py
```

## Structure du dépôt

```
.
├── data/
│   ├── inputs/           # documents sources (PDF, Excel...)
│   └── vector_db/        # index FAISS + chunks générés par scripts/index.py
├── scripts/
│   └── index.py            # script CLI d'indexation
├── src/                    # code source réutilisable (importé par scripts/ et app/)
│   ├── config.py
│   ├── loading/
│   │   └── loaders.py       # extraction multi-format (PDF/OCR, DOCX, TXT, CSV, Excel)
│   └── rag/
│       └── vector_store.py  # FAISS + embeddings Mistral
├── app/
│   └── chat.py              # interface Streamlit
├── pyproject.toml          # dépendances et métadonnées du projet (uv)
├── uv.lock                 # versions verrouillées (reproductibilité)
└── P10_DSML/                # prototype de référence fourni par Sarah (local uniquement, non versionné)
```

`P10_DSML/` est volontairement absent du dépôt git (`.gitignore`) : c'est le prototype d'origine, conservé intact en local comme référence, jamais modifié directement. Le contenu de `data/`, `scripts/`, `src/` et `app/` en a été repris (copié puis adapté a minima : imports et deux correctifs d'environnement — voir plus bas), pas réécrit à ce stade.

## Analyse du prototype de référence (P10_DSML)

Le prototype fourni est un pipeline RAG texte classique :

| Fichier | Rôle |
|---|---|
| `utils/config.py` | Configuration centralisée (clé API, modèles Mistral, taille de chunks, chemins) |
| `utils/data_loader.py` | Extraction de texte multi-formats (PDF avec fallback OCR, DOCX, TXT, CSV, Excel) |
| `utils/vector_store.py` (`VectorStoreManager`) | Découpage en chunks, génération d'embeddings (`mistral-embed`), index FAISS (`IndexFlatIP`, similarité cosinus), recherche top-k |
| `indexer.py` | Script CLI orchestrant l'indexation complète (chargement → chunking → embeddings → sauvegarde) |
| `MistralChat.py` | Interface Streamlit : question → recherche vectorielle → contexte injecté dans un prompt → génération (`mistral-small-latest`) |

**Données sources** : 4 PDF de discussions Reddit (contenu narratif) + 1 fichier Excel `regular NBA.xlsx` (statistiques de matchs, données structurées).

### Limite diagnostiquée

Le fichier Excel de statistiques est traité comme du texte brut chunké (`df.to_string()` puis découpage par caractères), au même titre que les PDF Reddit. Testé en conditions réelles sur les questions cibles de la mission :

- La récupération sémantique fonctionne bien sur le contenu narratif (PDF Reddit) : réponses correctement sourcées.
- Sur les questions analytiques nécessitant un calcul (ex. % de réussite à 3 points sur les 5 derniers matchs), le modèle ne retrouve pas de données exploitables dans les chunks Excel, et complète sa réponse avec des chiffres qui ne proviennent pas des données réelles de SportSee (connaissances générales du modèle, non vérifiables, potentiellement fausses) — sans le signaler clairement à l'utilisateur.

Cette limite oriente la conception de l'architecture cible : la recherche sémantique par similarité de texte n'est pas adaptée pour répondre à des questions nécessitant filtrage/agrégation sur des données structurées.

### Correctifs d'environnement appliqués lors de la reprise du code

Deux problèmes indépendants de la logique métier ont dû être corrigés pour que le code fonctionne (nécessaires sur cette machine, pas des choix de conception) :

- **TLS** (`src/rag/vector_store.py`) : les appels à l'API Mistral échouaient (`CERTIFICATE_VERIFY_FAILED`) derrière le proxy/antivirus local qui inspecte le trafic HTTPS. Corrigé via [`truststore`](https://github.com/sethmlarson/truststore), qui fait confiance au magasin de certificats du système plutôt qu'au bundle `certifi` embarqué.
- **Encodage console Windows** (`src/loading/loaders.py`) : les barres de progression d'EasyOCR (caractères Unicode `█`) faisaient planter l'initialisation de l'OCR sur la console Windows par défaut (non-UTF-8), empêchant toute extraction des PDF scannés. Corrigé en forçant `stdout`/`stderr` en UTF-8 au démarrage.

## État d'avancement

- [x] Environnement reproductible (uv, Python 3.11)
- [x] Prototype de référence testé et diagnostiqué
- [x] Scripts du prototype repris et restructurés (`data/`, `scripts/`, `src/`, `app/`)
- [ ] Architecture cible pour les questions analytiques
- [ ] Implémentation
