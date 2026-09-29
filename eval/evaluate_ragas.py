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

import logfire
import truststore

truststore.inject_into_ssl()  # magasin de certificats système (proxy/antivirus local)

# Sous Windows, Python retombe sur cp1252 dès que la sortie n'est pas un terminal (une
# redirection, un `| tee`). Le jeu de test contient « Jokić » : sans cette ligne, le run
# meurt au cas S3, après avoir déjà payé les deux premiers.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()  # charge .env (clés MISTRAL_API_KEY et OPENAI_API_KEY)

# Rend le paquet src/ importable (config.py, rag/*), quel que soit le répertoire
# depuis lequel ce script est lancé
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# --- Observabilité Logfire ---
# Ces 2 lignes sont volontairement dupliquées à l'identique dans chaque point d'entrée,
# sans module partagé : une divergence n'affecterait que la qualité des traces, jamais
# les réponses ni les scores.
# send_to_logfire="if-token-present" : rien n'est envoyé tant qu'aucun token n'est
# configuré, plutôt que d'échouer ou de réclamer une authentification.
logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()  # trace l'agent, ses relances et ses appels modèle

# --- Système évalué ---
from rag.pipeline import repondre
from rag.vector_store import VectorStoreManager

# --- Juge RAGAS : OpenAI (différent du système évalué, anti-biais) ---
from openai import AsyncOpenAI
from ragas.embeddings import OpenAIEmbeddings
from ragas.llms import llm_factory
from ragas.metrics.collections import AnswerCorrectness, ContextPrecision, ContextRecall, Faithfulness


def query_prototype(vector_store_manager: VectorStoreManager, question: str):
    """Le chemin complet, identique à celui de l'interface : routage, collecte, réponse.

    Un seul point d'entrée partagé — sans quoi l'évaluation mesurerait autre chose que
    ce que l'application fait.
    """
    return repondre(vector_store_manager, question)


# Ce que le modèle a vu quand il n'a reçu aucun extrait. Repris à l'identique de
# generation.py : le juge doit noter le contexte RÉEL, pas une reconstitution.
from rag.generation import MESSAGE_CONTEXTE_SQL, MESSAGE_CONTEXTE_VIDE


def creer_juge():
    """Instancie le juge une fois : quatre métriques partageant le même LLM.

    Extrait pour que la campagne et la renotation emploient **le même juge**. Deux
    constructions séparées finiraient par diverger, et des scores produits par deux
    juges différents ne se comparent pas.
    """
    print("Initialisation du juge RAGAS (OpenAI gpt-4o)...")
    openai_client = AsyncOpenAI()  # client asynchrone requis par ragas (score() lance ascore() en interne)
    # max_tokens relevé (défaut trop bas) : sur les contextes longs, le juge doit lister
    # beaucoup d'énoncés/verdicts en sortie structurée, et se faisait tronquer sans cette valeur.
    judge_llm = llm_factory("gpt-4o", client=openai_client, max_tokens=8192)
    judge_embeddings = OpenAIEmbeddings(client=openai_client)  # requis par Answer Correctness

    return {
        # La réponse invente-t-elle des faits absents du contexte récupéré ?
        "faithfulness": Faithfulness(llm=judge_llm),
        # Les contextes récupérés sont-ils pertinents ? Ne regarde pas la réponse.
        "context_precision": ContextPrecision(llm=judge_llm),
        # Le contexte contient-il de quoi répondre ? Exhaustivité vs pertinence.
        "context_recall": ContextRecall(llm=judge_llm),
        # La réponse correspond-elle à la référence ? Similarité + recoupement factuel :
        # sanctionne un chiffre faux même si la réponse reste sur le sujet.
        "answer_correctness": AnswerCorrectness(llm=judge_llm, embeddings=judge_embeddings),
    }


def noter(metriques: dict, question: str, reference: str, reponse: str, contextes: list) -> dict:
    """Les quatre scores d'un cas. Lève si le juge est indisponible."""
    m = metriques
    return {
        "faithfulness": m["faithfulness"].score(
            user_input=question, response=reponse, retrieved_contexts=contextes
        ).value,
        "context_precision": m["context_precision"].score(
            user_input=question, reference=reference, retrieved_contexts=contextes
        ).value,
        "context_recall": m["context_recall"].score(
            user_input=question, retrieved_contexts=contextes, reference=reference
        ).value,
        "answer_correctness": m["answer_correctness"].score(
            user_input=question, response=reponse, reference=reference
        ).value,
    }


def renoter(label: str, pause: float = 5.0):
    """Note les cas laissés sans score, à partir du fichier de résultats.

    Ne relance NI le routage, NI la génération, NI la moindre requête : la réponse et
    les contextes sont déjà sur disque, payés en appels Mistral. Seul le juge est
    rappelé. C'est ce que la séparation système / notation rend possible — une panne
    du juge, un quota épuisé, et l'on reprend là où l'on s'est arrêté.
    """
    chemin = Path(__file__).resolve().parent / "results" / f"{label}.json"
    resultats = json.loads(chemin.read_text(encoding="utf-8"))

    a_noter = [c for c in resultats if "error_notation" in c]
    perdus = [c["id"] for c in resultats if c.get("etape") == "systeme"]
    print(f"{len(resultats)} cas dans {chemin.name} : {len(a_noter)} à noter.")
    if perdus:
        print(f"  {len(perdus)} cas sans sortie système, irrécupérables ici : {perdus}")
    if not a_noter:
        print("Rien à faire.")
        return

    metriques = creer_juge()
    for i, cas in enumerate(a_noter, 1):
        print(f"[{i}/{len(a_noter)}] {cas['id']} - {cas['question'][:70]}...")
        try:
            cas.update(
                noter(
                    metriques,
                    cas["question"],
                    cas["reference_answer"],
                    cas["system_response"],
                    cas["retrieved_contexts"],
                )
            )
            cas.pop("error_notation", None)
        except Exception as e:
            print(f"  ECHEC NOTATION sur {cas['id']} : {e}")
            cas["error_notation"] = str(e)

        # Après chaque cas : une seconde panne ne reperd pas ce qui vient d'être noté.
        chemin.write_text(json.dumps(resultats, ensure_ascii=False, indent=2), encoding="utf-8")
        if pause and i < len(a_noter):
            time.sleep(pause)

    restants = [c["id"] for c in resultats if "error_notation" in c]
    print(f"\nTerminé. {len(restants)} cas encore sans score : {restants or 'aucun'}")


def construire_contextes(fragments: list, requetes: list, base_autorisee: bool):
    """Le contexte transmis au juge : ce que le modèle a réellement eu sous les yeux.

    Sur la route `base`, aucun extrait n'est récupéré — c'est voulu, cela évite un
    embedding facturé pour un contexte inutile. Le contexte du modèle est alors le
    RÉSULTAT DE LA REQUÊTE. Ne transmettre que les fragments laissait la liste vide,
    et RAGAS refuse un échantillon sans contexte : 9 cas sur 18 perdus.

    Attention à la lecture des scores qui en découlent : `context_precision` et
    `context_recall` portaient sur 5 extraits flous, ils porteront ici sur une ligne
    exacte. Ces deux métriques ne se comparent donc PAS d'un run à l'autre sur la
    moyenne globale — seulement par modalité, là où le mécanisme n'a pas changé.

    Rend (contextes, sources), alignés.
    """
    contextes = [f["text"] for f in fragments]
    sources = [f["metadata"].get("source", "Inconnue") for f in fragments]

    for i, resultat in enumerate(requetes, 1):
        contextes.append(resultat.pour_le_modele())
        sources.append(f"base NBA (sql_{i})")

    if not contextes:
        # Ni extrait ni requête : le modèle a quand même reçu une phrase, celle qui
        # figure dans son prompt. C'est elle que le juge doit noter.
        contextes = [MESSAGE_CONTEXTE_SQL if base_autorisee else MESSAGE_CONTEXTE_VIDE]
        sources = ["aucune source"]

    return contextes, sources


def interroger_avec_delai(vector_store_manager, question: str, delai: float):
    """Interroge le système en abandonnant au-delà de `delai` secondes.

    Garde-fou du HARNAIS, pas du système : on refuse d'attendre indéfiniment.

    Pas de `with ThreadPoolExecutor(...)` : sa sortie appelle `shutdown(wait=True)`,
    qui **attend le thread bloqué** — délai mesuré à 4 s pour 1 s demandée.

    Python ne peut pas tuer un thread : l'appel abandonné continue en arrière-plan,
    borné par le délai du client (45 s). Une vraie garantie d'arrêt demanderait une
    isolation par processus, disproportionnée pour 18 cas.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        futur = executor.submit(query_prototype, vector_store_manager, question)
        try:
            return futur.result(timeout=delai)
        except FuturesTimeout as e:
            raise TimeoutError(f"génération abandonnée après {delai} s") from e
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def main(limit: int | None = None, label: str | None = None, force: bool = False,
         pause: float = 5.0, delai_cas: float = 240.0):
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

    metriques = creer_juge()

    results = []
    for i, case in enumerate(testset, 1):
        print(f"[{i}/{len(testset)}] {case['id']} - {case['question'][:70]}...")

        # ÉTAPE 1 — le système. Ce qu'elle produit est payé en appels Mistral : on
        # l'enregistre AVANT toute notation, pour qu'un incident du juge ne le
        # détruise pas. Un run où le juge échoue reste alors renotable sans repayer.
        try:
            # Un span par cas : le tableau de bord regroupe alors recherche,
            # génération et relances éventuelles sous l'identifiant du cas.
            with logfire.span("cas", id=case["id"], categorie=case["categorie"]):
                resultat = interroger_avec_delai(
                    vector_store_manager, case["question"], delai_cas
                )
                search_results, reponse = resultat.fragments, resultat.reponse
        except Exception as e:
            print(f"  ECHEC SYSTEME sur {case['id']} : {e}")
            results.append({"id": case["id"], "error": str(e), "etape": "systeme"})
            out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            continue

        # Le juge note ce que l'utilisateur lit, motif d'abstention compris — noter
        # `answer` seul jugerait une phrase creuse. C'est aussi ce que les runs
        # précédents notaient, ce qui préserve la comparabilité.
        answer = reponse.texte_visible()
        contexts, sources = construire_contextes(
            search_results, resultat.requetes, resultat.route.source in ("base", "les_deux")
        )

        # Tout ce que le système a produit, indépendamment du juge.
        enregistrement = {
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
            "answer_raw": reponse.answer,
            "abstain": reponse.abstain,
            "abstain_reason": reponse.abstain_reason,
            "citations": reponse.citations,
            "route": resultat.route.source,
            "route_motif": resultat.route.motif,
            "sql_requetes": [r.requete for r in resultat.requetes],
            "sql_lignes": [r.lignes for r in resultat.requetes],
        }

        # ÉTAPE 2 — la notation. Un échec ici ne coûte que les scores.
        try:
            enregistrement.update(
                noter(metriques, case["question"], case["reference_answer"], answer, contexts)
            )
        except Exception as e:
            # La sortie du système est conservée : seuls les scores manquent, et ils
            # se rattrapent depuis ce fichier sans relancer un seul appel Mistral.
            print(f"  ECHEC NOTATION sur {case['id']} : {e}")
            enregistrement["error_notation"] = str(e)

        results.append(enregistrement)

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
    # 240 s : garde-fou de dernier recours, pas remède à une latence interne. Chaque
    # étape a son propre délai (routage 15 s, génération 45 s, SQL 5 s, recherche 60 s) ;
    # ce budget couvre leur enchaînement le plus long, jamais atteint en régime normal.
    parser.add_argument("--delai-cas", type=float, default=240.0, help="Délai maximal par cas (défaut : 240 s)")
    # --renoter : note les cas restés sans score, sans relancer le système
    parser.add_argument("--renoter", action="store_true", help="Noter les cas sans score d'un run existant (aucun appel Mistral)")
    args = parser.parse_args()

    if args.renoter:
        if not args.label:
            parser.error("--renoter exige --label")
        renoter(label=args.label, pause=args.pause)
    else:
        main(limit=args.limit, label=args.label, force=args.force, pause=args.pause, delai_cas=args.delai_cas)
