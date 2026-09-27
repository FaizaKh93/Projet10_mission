# eval/evaluate_ragas.py
"""Évalue le prototype RAG sur eval/testset.json avec RAGAS.

Système évalué : Mistral (mistral-small-latest) + FAISS, via src/rag/vector_store.py
et src/rag/generation.py — exactement le chemin qu'emprunte app/chat.py.
Juge RAGAS : OpenAI gpt-4o, volontairement différent du système évalué pour éviter
le biais d'auto-évaluation.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime
from pathlib import Path

import truststore

truststore.inject_into_ssl()  # magasin de certificats système (proxy/antivirus local)

from dotenv import load_dotenv

load_dotenv()  # charge .env (clés MISTRAL_API_KEY et OPENAI_API_KEY)

# Rend le paquet src/ importable (config.py, rag/*), quel que soit le répertoire
# depuis lequel ce script est lancé
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# --- Système évalué ---
from config import SEARCH_K
from rag.generation import generate_answer
from rag.vector_store import VectorStoreManager

# --- Juge RAGAS : OpenAI (différent du système évalué, anti-biais) ---
from openai import AsyncOpenAI
from ragas.embeddings import OpenAIEmbeddings
from ragas.llms import llm_factory
from ragas.metrics.collections import AnswerCorrectness, ContextPrecision, ContextRecall, Faithfulness


def query_prototype(vector_store_manager: VectorStoreManager, question: str) -> tuple[list[dict], str]:
    """Reproduit le chemin de app/chat.py : recherche vectorielle puis génération."""
    # Étape 1 : les k chunks les plus proches sémantiquement de la question
    search_results = vector_store_manager.search(question, k=SEARCH_K)
    # Étape 2 : assemblage du contexte, prompt et appel du modèle (rag/generation.py)
    answer = generate_answer(search_results, question)
    return search_results, answer


def interroger_avec_delai(vector_store_manager, question: str, delai: float):
    """Interroge le système en abandonnant au-delà de `delai` secondes.

    Garde-fou du HARNAIS, pas du système : le prototype garde sa configuration
    d'origine, on refuse seulement d'attendre indéfiniment une réponse de l'API.

    Python ne peut pas tuer un thread : l'appel abandonné continue en arrière-plan
    jusqu'à son terme. On enregistre l'incident et on passe au cas suivant.
    """
    with ThreadPoolExecutor(max_workers=1) as executor:
        futur = executor.submit(query_prototype, vector_store_manager, question)
        try:
            return futur.result(timeout=delai)
        except FuturesTimeout as e:
            raise TimeoutError(f"génération abandonnée après {delai} s") from e


def main(limit: int | None = None, label: str | None = None, force: bool = False,
         pause: float = 5.0, delai_cas: float = 120.0):
    # Nom de fichier explicite (ex. "baseline") ou, à défaut, un horodatage — jamais un
    # nom fixe, pour ne jamais écraser un run précédent par erreur.
    label = label or datetime.now().strftime("run_%Y%m%d_%H%M%S")
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    out_path = results_dir / f"{label}.json"

    # Refuse d'écraser un fichier de résultats existant sauf --force explicite.
    if out_path.exists() and not force:
        raise FileExistsError(
            f"{out_path} existe déjà. Choisissez un autre --label, ou passez --force pour écraser volontairement."
        )

    # Charge les cas de test (question + reference_answer + métadonnées)
    testset_path = Path(__file__).parent / "testset.json"
    testset = json.loads(testset_path.read_text(encoding="utf-8"))
    if limit:
        testset = testset[:limit]  # vérifier le script sur 2-3 cas avant le run complet

    print("Chargement du VectorStoreManager (système évalué)...")
    vector_store_manager = VectorStoreManager()  # index FAISS + chunks depuis data/vector_db/

    print("Initialisation du juge RAGAS (OpenAI gpt-4o)...")
    openai_client = AsyncOpenAI()  # client asynchrone requis par ragas (score() lance ascore() en interne)
    # max_tokens relevé (défaut trop bas) : sur les contextes longs, le juge doit lister
    # beaucoup d'énoncés/verdicts en sortie structurée, et se faisait tronquer sans cette valeur.
    judge_llm = llm_factory("gpt-4o", client=openai_client, max_tokens=8192)
    judge_embeddings = OpenAIEmbeddings(client=openai_client)  # requis par Answer Correctness

    # Chaque métrique est instanciée une seule fois, puis réutilisée pour tous les cas
    faithfulness = Faithfulness(llm=judge_llm)
    context_precision = ContextPrecision(llm=judge_llm)
    context_recall = ContextRecall(llm=judge_llm)
    answer_correctness = AnswerCorrectness(llm=judge_llm, embeddings=judge_embeddings)

    results = []
    for i, case in enumerate(testset, 1):
        print(f"[{i}/{len(testset)}] {case['id']} - {case['question'][:70]}...")

        # Chaque cas est isolé : un échec (ex. limite de tokens côté juge) ne doit pas
        # faire perdre les résultats déjà obtenus - et déjà payés - sur les cas précédents.
        try:
            search_results, answer = interroger_avec_delai(
                vector_store_manager, case["question"], delai_cas
            )
            # RAGAS attend le texte seul des chunks ; les sources sont gardées à part
            # pour l'analyse, sans avoir à relancer la recherche.
            contexts = [res["text"] for res in search_results]
            sources = [res["metadata"].get("source", "Inconnue") for res in search_results]

            # Métrique 1 - Faithfulness : la réponse invente-t-elle des faits absents du
            # contexte récupéré (hallucination) ?
            f = faithfulness.score(
                user_input=case["question"], response=answer, retrieved_contexts=contexts
            )

            # Métrique 2 - Context Precision : les chunks récupérés sont-ils pertinents au
            # regard de ce qu'il fallait retrouver ? Ne regarde pas la réponse générée.
            cp = context_precision.score(
                user_input=case["question"], reference=case["reference_answer"], retrieved_contexts=contexts
            )

            # Métrique 3 - Context Recall : le contexte contient-il tout ce qu'il faut pour
            # répondre ? Complémentaire de la précision (exhaustivité vs pertinence).
            cr = context_recall.score(
                user_input=case["question"], retrieved_contexts=contexts, reference=case["reference_answer"]
            )

            # Métrique 4 - Answer Correctness : la réponse correspond-elle à la référence ?
            # Similarité sémantique + recoupement factuel : sanctionne un chiffre faux même
            # si la réponse reste sur le sujet.
            ac = answer_correctness.score(
                user_input=case["question"], response=answer, reference=case["reference_answer"]
            )

            results.append(
                {
                    "id": case["id"],
                    "categorie": case["categorie"],
                    "modalite": case["modalite"],
                    "sous_type": case.get("sous_type"),
                    "expected_behavior": case["expected_behavior"],
                    "evaluation_focus": case["evaluation_focus"],
                    "question": case["question"],
                    "reference_answer": case["reference_answer"],
                    "system_response": answer,
                    "retrieved_contexts": contexts,
                    "retrieved_sources": sources,
                    "faithfulness": f.value,
                    "context_precision": cp.value,
                    "context_recall": cr.value,
                    "answer_correctness": ac.value,
                }
            )
        except Exception as e:
            print(f"  ECHEC sur {case['id']} : {e}")
            results.append({"id": case["id"], "error": str(e)})

        # Sauvegarde après CHAQUE cas : si le script s'arrête au cas 15, les 14 premiers
        # restent sur disque au lieu d'être perdus.
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

        # Pause entre les cas : gpt-4o est plafonné à 30 000 tokens/minute et un seul cas
        # consomme une vingtaine d'appels de juge. Sans elle, des cas se perdent sur des 429.
        if pause and i < len(testset):
            time.sleep(pause)

    print(f"\nRésultats sauvegardés dans {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Évaluation RAGAS du prototype RAG")
    # --limit N : ne traite que les N premiers cas (pour vérifier le script sans tout relancer)
    parser.add_argument("--limit", type=int, default=None, help="Limiter à N premiers cas (pour test rapide)")
    # --label : nom du fichier de résultats dans eval/results/ (ex. "baseline")
    parser.add_argument("--label", type=str, default=None, help="Nom du run (défaut : horodatage automatique)")
    # --force : autorise explicitement à écraser un fichier de résultats existant
    parser.add_argument("--force", action="store_true", help="Écraser un résultat existant portant le même label")
    # --pause : secondes entre deux cas, pour rester sous la limite de tokens/minute du juge
    parser.add_argument("--pause", type=float, default=5.0, help="Pause entre deux cas en secondes (défaut : 5)")
    # --delai-cas : abandon d'un cas dont la génération s'éternise
    parser.add_argument("--delai-cas", type=float, default=120.0, help="Délai maximal de génération par cas (défaut : 120 s)")
    args = parser.parse_args()
    main(limit=args.limit, label=args.label, force=args.force, pause=args.pause, delai_cas=args.delai_cas)
