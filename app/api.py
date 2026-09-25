# app/api.py
"""API REST exposant l'assistant NBA.

Voisine de `app/chat.py` : même pipeline, autre interface. Aucune règle métier ici —
la logique vit dans `src/rag/`, les deux interfaces l'appellent à l'identique.

Cinq endpoints : `/` (identité), `/health` (le processus vit), `/ready` (les
dépendances répondent), `/ask` (poser une question), `/logs` (les derniers appels).

Lancement :
    uv run uvicorn app.api:app --reload
Documentation interactive : http://localhost:8000/docs
"""
import logging
import sqlite3  # pour le test de vie de la base dans /ready, sans passer par SQLAlchemy
import sys
import time  # perf_counter : mesure des latences
from collections import deque  # file bornée pour le journal des appels
from contextlib import asynccontextmanager  # requis par le lifespan de FastAPI
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import logfire
import truststore
from fastapi import FastAPI, HTTPException
from opentelemetry import trace  # donne l'identifiant de trace de la requête en cours
from pydantic import BaseModel, Field

# --- Mise en place -----------------------------------------------------------------

# Avant logfire.configure(), comme dans app/chat.py : derrière un proxy qui inspecte
# le TLS, l'export des traces échoue sinon, silencieusement.
truststore.inject_into_ssl()

# Rend src/ importable quel que soit le répertoire de lancement : ce fichier vit dans
# app/, le code métier dans src/, et Python ne connaît pas ce dernier par défaut.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Imports placés APRÈS sys.path.insert, d'où les `noqa` : sans cette ligne, ils
# échoueraient. C'est aussi la disposition de app/chat.py.
from config import APP_TITLE, MISTRAL_API_KEY, NBA_DB_FILE, SEARCH_K  # noqa: E402
from rag.generation import generate_answer  # noqa: E402
from rag.vector_store import VectorStoreManager  # noqa: E402
from schemas import AnswerWithSQL  # noqa: E402

# "if-token-present" : n'envoie rien tant qu'aucun token Logfire n'est configuré,
# plutôt que d'échouer. Mêmes deux lignes que dans app/chat.py et eval/evaluate_ragas.py.
logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()  # trace les appels de l'agent (src/rag/generation.py)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(module)s - %(message)s"
)

# L'index (302 vecteurs) est chargé UNE fois au démarrage, jamais par requête :
# équivalent du @st.cache_resource de app/chat.py. Un dict plutôt qu'une variable
# globale, pour que le lifespan puisse le remplir puis le vider proprement.
_ressources: dict = {}

# Journal des derniers appels, borné par construction : pas de fuite mémoire possible.
# Volatile et propre au processus — voir la docstring de /logs.
_journal: deque = deque(maxlen=100)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ce qui s'exécute au démarrage et à l'arrêt du serveur.

    Tout ce qui précède le `yield` tourne une fois au démarrage, tout ce qui suit à
    l'arrêt. C'est ici qu'on charge l'index : le faire par requête ajouterait des
    secondes à chaque question.
    """
    logging.info("Chargement de l'index vectoriel...")
    manager = VectorStoreManager()  # le constructeur lit l'index et les chunks
    if manager.index is None or not manager.document_chunks:
        # On démarre quand même : /health doit pouvoir répondre pour diagnostiquer.
        logging.error("Index absent — lancer d'abord `uv run python scripts/index.py`")
    else:
        logging.info(f"Index chargé : {manager.index.ntotal} vecteurs")
    _ressources["vector_store"] = manager
    yield  # le serveur traite les requêtes pendant que l'exécution est suspendue ici
    _ressources.clear()


# title et description alimentent la page /docs : c'est la documentation de l'API,
# générée par FastAPI, pas un simple libellé.
app = FastAPI(
    title="NBA Analyst AI",
    description="Assistant d'analyse NBA : recherche vectorielle sur des discussions "
    "Reddit, et base statistique interrogée en SQL sous garde-fous.",
    version="1.0.0",
    lifespan=lifespan,
)
logfire.instrument_fastapi(app)  # trace chaque requête : durée, statut, exceptions


# --- Contrats d'entrée et de sortie ------------------------------------------------
# Des modèles Pydantic, donc validés automatiquement ET publiés dans /docs : FastAPI
# en déduit les schémas de requête et de réponse sans qu'on les écrive deux fois.


class Question(BaseModel):
    """Entrée de /ask. Les bornes évitent les requêtes vides et les prompts géants."""

    question: str = Field(
        min_length=3,  # en dessous, ce n'est pas une question
        max_length=500,  # au-dessus, c'est une tentative de saturer le prompt
        examples=["Combien de points Nikola Jokić a-t-il marqués cette saison ?"],
    )


class ReponseAsk(BaseModel):
    """Réponse de /ask.

    `latence_ms` est séparée de la réponse elle-même : `AnswerWithSQL` est le contrat
    partagé avec Streamlit et l'évaluation, il ne doit pas dépendre du transport.
    """

    reponse: AnswerWithSQL  # la réponse du système, telle que Streamlit la reçoit aussi
    latence_ms: dict[str, float] = Field(
        description="Décomposition : recherche vectorielle, génération, total."
    )


class AppelJournalise(BaseModel):
    """Une ligne du journal. La réponse est tronquée : 100 réponses entières
    alourdiraient la charge utile et exposeraient inutilement leur contenu.

    `issue_appel` dit si le traitement est allé au bout, **pas** si la réponse est bonne.
    Une abstention est donc `abouti` : le système est allé au bout et a conclu qu'il ne
    pouvait pas répondre de façon fiable. `echec` est réservé aux pannes.
    """

    horodatage: datetime                     # quand l'appel a eu lieu, en UTC
    issue_appel: Literal["abouti", "echec"]  # allé au bout, ou tombé en panne
    question: str                            # la question reçue, telle quelle
    base_interrogee: bool = False            # le tool SQL a-t-il tourné ?
    requetes_sql: list[str] = []             # le SQL exécuté sur data/nba.db
    citations: list[str] = []                # les chunk_id venus de l'index
    abstention: bool = False                 # le modèle a refusé de répondre
    abstain_reason: str | None = None        # et pourquoi il a refusé
    latence_ms: dict[str, float] = {}        # recherche / génération / total
    extrait_reponse: str = ""                # les 300 premiers caractères
    erreur: str | None = None                # le message, si issue_appel = echec
    trace_id: str | None = None              # mène à la trace Logfire complète


def _trace_id() -> str | None:
    """Identifiant de la trace Logfire en cours, pour relier un appel à son détail.

    Logfire ouvre un span par requête ; on lui emprunte son identifiant, formaté en
    32 caractères hexadécimaux — celui qu'affiche le tableau de bord.
    """
    contexte = trace.get_current_span().get_span_context()
    return format(contexte.trace_id, "032x") if contexte.is_valid else None


# --- Endpoints ---------------------------------------------------------------------


@app.get("/", summary="Identité du service")
def racine() -> dict:
    """Point d'entrée : ce qu'est ce service et où trouver sa documentation."""
    return {
        "service": APP_TITLE,
        "version": app.version,
        "documentation": "/docs",
        "endpoints": ["/health", "/ready", "/ask", "/logs"],
    }


@app.get("/ready", summary="Le service peut-il traiter une requête ?")
def ready() -> dict:
    """Readiness : vérifie les trois dépendances, contrairement à /health.

    C'est cet endpoint qu'un orchestrateur interroge avant de router du trafic — un
    processus vivant dont l'index n'est pas chargé répondrait 503 à chaque question.
    """
    manager = _ressources.get("vector_store")
    index_ok = manager is not None and manager.index is not None and bool(manager.document_chunks)

    # mode=ro : on ouvre la base en lecture seule, comme le tool SQL. Le SELECT 1
    # prouve que le fichier existe ET que la table attendue s'y trouve.
    try:
        with sqlite3.connect(f"file:{NBA_DB_FILE}?mode=ro", uri=True) as conn:
            conn.execute("SELECT 1 FROM players LIMIT 1").fetchone()
        base_ok = True
    except sqlite3.Error as e:
        logging.warning(f"Base NBA injoignable : {e}")
        base_ok = False

    verifications = {
        "index_vectoriel": index_ok,
        "base_nba": base_ok,
        "cle_mistral": bool(MISTRAL_API_KEY),
    }
    # 503 plutôt qu'un 200 mensonger : le détail dit laquelle des trois est tombée.
    if not all(verifications.values()):
        raise HTTPException(status_code=503, detail=verifications)
    return {"status": "ready", "verifications": verifications}


@app.get("/health", summary="Le service répond-il ?")
def health() -> dict:
    """Liveness : le processus est debout. Ne vérifie aucune dépendance."""
    return {"status": "ok"}


# `def` et non `async def` : generate_answer() appelle agent.run_sync(), qui est
# bloquant. Un async def gèlerait la boucle d'événements et sérialiserait toutes les
# requêtes ; un def simple est exécuté par FastAPI dans un pool de threads.
@app.post("/ask", response_model=ReponseAsk, summary="Poser une question à l'assistant")
def ask(entree: Question) -> ReponseAsk:
    """Recherche les chunks pertinents, puis génère une réponse structurée.

    La réponse porte les requêtes SQL **réellement exécutées**, relevées dans la trace
    du run et jamais déclarées par le modèle.
    """

    def journaliser(**champs) -> None:
        """Toute issue est journalisée, y compris les pannes — sans quoi /logs ne
        montrerait que les succès et mentirait sur ce qu'il annonce.

        Définie ici pour capturer `entree.question` sans avoir à la repasser.
        """
        _journal.append(
            AppelJournalise(
                horodatage=datetime.now(timezone.utc),
                question=entree.question,
                trace_id=_trace_id(),
                **champs,
            )
        )

    # Première issue possible : l'index n'a pas pu être chargé au démarrage.
    manager = _ressources.get("vector_store")
    if manager is None or manager.index is None:
        motif = "Index vectoriel indisponible : lancer `uv run python scripts/index.py`."
        journaliser(issue_appel="echec", erreur=motif)
        raise HTTPException(status_code=503, detail=motif)

    # Trois repères de temps : début → après la recherche → fin. Leur différence donne
    # la décomposition de la latence, que rien ne mesurait jusqu'ici.
    debut = time.perf_counter()
    try:
        chunks = manager.search(entree.question, k=SEARCH_K)  # les 5 chunks les plus proches
        apres_recherche = time.perf_counter()
        reponse = generate_answer(chunks, entree.question)  # agent + tool SQL éventuel
    # Deuxième issue : une panne pendant le traitement (API injoignable, base absente...)
    except Exception as e:
        logging.exception("Échec du traitement de la question")
        journaliser(
            issue_appel="echec",
            erreur=f"{type(e).__name__}: {e}",
            latence_ms={"total": round((time.perf_counter() - debut) * 1000, 1)},
        )
        raise HTTPException(status_code=500, detail=f"Erreur pendant la génération : {e}") from e

    fin = time.perf_counter()
    latences = {
        "recherche": round((apres_recherche - debut) * 1000, 1),
        "generation": round((fin - apres_recherche) * 1000, 1),
        "total": round((fin - debut) * 1000, 1),
    }

    # Troisième issue : le traitement a abouti. Une abstention en fait partie — le
    # système refuse de répondre parce qu'il ne peut pas le faire de façon fiable.
    journaliser(
        issue_appel="abouti",                       # le traitement est allé au bout
        base_interrogee=bool(reponse.sql_queries),  # le tool SQL a-t-il tourné ?
        requetes_sql=reponse.sql_queries,           # le SQL exécuté sur data/nba.db
        citations=reponse.citations,                # les chunk_id venus de l'index
        abstention=reponse.abstain,                 # le modèle a refusé de répondre
        abstain_reason=reponse.abstain_reason,      # et pourquoi il a refusé
        latence_ms=latences,                        # recherche / génération / total
        extrait_reponse=reponse.answer[:300],       # tronqué : /logs en garde 100
    )
    return ReponseAsk(reponse=reponse, latence_ms=latences)


@app.get("/logs", response_model=list[AppelJournalise], summary="Les derniers appels")
def logs(limit: int = 100) -> list[AppelJournalise]:
    """Les 100 derniers appels à /ask, du plus récent au plus ancien.

    **Volatile** : un redémarrage efface tout, et chaque worker ne voit que ses propres
    appels. Fenêtre de débogage, pas de journal d'audit — Logfire garde la trace complète.

    **Expose les questions et les réponses** : à protéger avant toute mise en production.
    """
    return list(reversed(_journal))[:limit]  # reversed : le plus récent en premier
