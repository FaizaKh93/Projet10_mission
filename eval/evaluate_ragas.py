# eval/evaluate_ragas.py
"""Évalue le prototype RAG existant (notre copie, jamais P10_DSML) sur eval/testset.json.

Système évalué : Mistral (mistral-small-latest) + FAISS, via src/rag/vector_store.py.
Juge RAGAS : OpenAI gpt-4o (volontairement différent du système évalué, pour éviter
le biais d'auto-évaluation).
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import truststore

truststore.inject_into_ssl()  # magasin de certificats système (proxy/antivirus local)

from dotenv import load_dotenv

load_dotenv()  # charge .env (clés MISTRAL_API_KEY et OPENAI_API_KEY)

# Rend le package src/ importable (config.py, rag/vector_store.py), quel que soit
# le répertoire depuis lequel ce script est lancé
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# --- Système évalué : Mistral + FAISS (notre copie du prototype, jamais P10_DSML) ---
from mistralai.client import Mistral
from config import MISTRAL_API_KEY, MODEL_NAME, SEARCH_K
from rag.vector_store import VectorStoreManager

# --- Juge RAGAS : OpenAI (volontairement différent du système évalué, anti-biais) ---
from openai import AsyncOpenAI
from ragas.embeddings import OpenAIEmbeddings
from ragas.llms import llm_factory
from ragas.metrics.collections import AnswerCorrectness, ContextPrecision, ContextRecall, Faithfulness

# Prompt système identique à app/chat.py — ne pas diverger, on évalue le système tel quel
SYSTEM_PROMPT = """Tu es 'NBA Analyst AI', un assistant expert sur la ligue de basketball NBA.
Ta mission est de répondre aux questions des fans en animant le débat.

---
{context_str}
---

QUESTION DU FAN:
{question}

RÉPONSE DE L'ANALYSTE NBA:"""


def query_prototype(vector_store_manager: VectorStoreManager, mistral_client: Mistral, question: str):
    """Reproduit exactement la logique de app/chat.py : recherche puis génération."""
    # Étape 1 : recherche vectorielle FAISS — les k chunks les plus proches sémantiquement de la question
    search_results = vector_store_manager.search(question, k=SEARCH_K)

    # Étape 2 : assemble les chunks récupérés en un seul bloc de contexte texte pour le prompt
    context_str = "\n\n---\n\n".join(
        f"Source: {res['metadata'].get('source', 'Inconnue')} (Score: {res['score']:.1f}%)\nContenu: {res['text']}"
        for res in search_results
    ) or "Aucune information pertinente trouvée dans la base de connaissances pour cette question."

    # Étape 3 : génération — le contexte + la question sont injectés dans le prompt système, puis envoyés à Mistral
    final_prompt = SYSTEM_PROMPT.format(context_str=context_str, question=question)
    response = mistral_client.chat.complete(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": final_prompt}],
        temperature=0.1,
    )
    answer = response.choices[0].message.content if response.choices else ""

    # contexts = juste le texte des chunks (sans les métadonnées), c'est ce format que RAGAS attend
    contexts = [res["text"] for res in search_results]
    return contexts, answer


def main(limit: int | None = None, label: str | None = None, force: bool = False):
    # Nom de fichier explicite (ex. "baseline") ou, à défaut, un horodatage — jamais un nom fixe,
    # pour ne jamais écraser un run précédent (ex. la baseline) par erreur.
    label = label or datetime.now().strftime("run_%Y%m%d_%H%M%S")
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    out_path = results_dir / f"{label}.json"

    # Garde-fou : refuse d'écraser un fichier de résultats existant sauf si --force est passé explicitement
    if out_path.exists() and not force:
        raise FileExistsError(
            f"{out_path} existe déjà. Choisissez un autre --label, ou passez --force pour écraser volontairement."
        )

    # Charge les 18 cas de test (question + reference_answer + métadonnées catégorie/modalité)
    testset_path = Path(__file__).parent / "testset.json"
    testset = json.loads(testset_path.read_text(encoding="utf-8"))
    if limit:
        testset = testset[:limit]  # utile pour tester sur 2-3 cas avant de lancer les 18

    print(f"Chargement du VectorStoreManager (système évalué)...")
    vector_store_manager = VectorStoreManager()  # charge l'index FAISS + les 302 chunks depuis data/vector_db/
    mistral_client = Mistral(api_key=MISTRAL_API_KEY)

    print("Initialisation du juge RAGAS (OpenAI gpt-4o)...")
    openai_client = AsyncOpenAI()  # client asynchrone requis par ragas (score() lance ascore() en interne)
    # max_tokens relevé (défaut trop bas) : sur les questions à contexte long, le juge doit lister
    # beaucoup d'énoncés/verdicts en sortie structurée, et se faisait tronquer sans cette valeur.
    # gpt-4o supporte jusqu'à 16384 tokens de sortie ; la sortie étant un JSON contraint (Pydantic via
    # instructor), fixer une valeur haute ne coûte rien de plus — le modèle ne "remplit" pas inutilement.
    judge_llm = llm_factory("gpt-4o", client=openai_client, max_tokens=8192)
    judge_embeddings = OpenAIEmbeddings(client=openai_client)  # nécessaire pour Answer Correctness (similarité sémantique)

    # Instancie chaque métrique une seule fois (réutilisée pour les 18 questions, pas recréée à chaque tour)
    faithfulness = Faithfulness(llm=judge_llm)
    context_precision = ContextPrecision(llm=judge_llm)
    context_recall = ContextRecall(llm=judge_llm)
    answer_correctness = AnswerCorrectness(llm=judge_llm, embeddings=judge_embeddings)

    results = []
    for i, case in enumerate(testset, 1):
        print(f"[{i}/{len(testset)}] {case['id']} — {case['question'][:70]}...")

        # Chaque cas est isolé dans son propre try/except : un échec (ex. limite de tokens dépassée
        # côté juge) ne doit jamais faire perdre les résultats déjà obtenus (et déjà payés) sur les
        # cas précédents. On enregistre l'erreur pour ce cas et on continue avec le suivant.
        try:
            # Interroge le système évalué (retrieval + génération Mistral) — voir query_prototype() plus haut
            contexts, answer = query_prototype(vector_store_manager, mistral_client, case["question"])

            # Métrique 1 - Faithfulness : la réponse invente-t-elle des faits absents du contexte récupéré
            # (hallucination) ? Compare chaque affirmation de `response` à ce qui est réellement dans `contexts`.
            f = faithfulness.score(user_input=case["question"], response=answer, retrieved_contexts=contexts)

            # Métrique 2 - Context Precision : les chunks récupérés sont-ils pertinents par rapport à ce qu'il
            # fallait retrouver ? Ne regarde PAS la réponse générée, seulement `contexts` vs `reference`.
            cp = context_precision.score(
                user_input=case["question"], reference=case["reference_answer"], retrieved_contexts=contexts
            )

            # Métrique 3 - Context Recall : le contexte récupéré contient-il tout ce qu'il faut pour bien
            # répondre ? Complémentaire de Context Precision (précision vs exhaustivité de la récupération).
            cr = context_recall.score(
                user_input=case["question"], retrieved_contexts=contexts, reference=case["reference_answer"]
            )

            # Métrique 4 - Answer Correctness : la réponse générée correspond-elle à la réponse de référence
            # (ground truth) ? Combine similarité sémantique + recoupement factuel entre `response` et
            # `reference`. Contrairement à Answer Relevancy (retirée — voir notes du projet), celle-ci
            # sanctionne bien un chiffre faux même si la réponse reste "sur le sujet".
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
                    "faithfulness": f.value,
                    "context_precision": cp.value,
                    "context_recall": cr.value,
                    "answer_correctness": ac.value,
                }
            )
        except Exception as e:
            print(f"  ÉCHEC sur {case['id']} : {e}")
            results.append({"id": case["id"], "error": str(e)})

        # Sauvegarde après CHAQUE cas (pas seulement à la fin) : si le script plante au cas 15,
        # les 14 premiers restent sur disque au lieu d'être perdus.
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nRésultats sauvegardés dans {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Évaluation RAGAS du prototype RAG")
    # --limit N : ne traite que les N premiers cas du testset (pour tester le script sans tout relancer)
    parser.add_argument("--limit", type=int, default=None, help="Limiter à N premiers cas (pour test rapide)")
    # --label : nom du fichier de résultats dans eval/results/ (ex. "baseline", "apres_pydantic")
    parser.add_argument("--label", type=str, default=None, help="Nom du run (défaut : horodatage automatique)")
    # --force : autorise explicitement à écraser un fichier de résultats existant portant le même label
    parser.add_argument("--force", action="store_true", help="Écraser un résultat existant portant le même label")
    args = parser.parse_args()
    main(limit=args.limit, label=args.label, force=args.force)
