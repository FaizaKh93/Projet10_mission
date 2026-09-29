# tests/test_renotation.py
"""La renotation : reprendre un run dont seul le juge a échoué.

Écrit après un run réel interrompu par un quota OpenAI épuisé. Les 18 cas avaient
produit leur réponse et leurs requêtes — payées en appels Mistral — mais aucun score.
Sans cette reprise, il faudrait tout relancer, et donc tout repayer.

Le juge est simulé ici : ces tests ne coûtent rien.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

import evaluate_ragas as ev

LABEL = "_test_renotation"
SCORES = {
    "faithfulness": 0.9,
    "context_precision": 0.8,
    "context_recall": 0.7,
    "answer_correctness": 0.6,
}


def cas(id_, *, note: bool, erreur: str | None = None) -> dict:
    """Un enregistrement comme la boucle en écrit un."""
    enr = {
        "id": id_,
        "question": f"Question {id_} ?",
        "reference_answer": "La référence.",
        "system_response": "La réponse du système.",
        "retrieved_contexts": ["Le contexte servi."],
        "route": "base",
        "route_motif": "chiffre demandé",
        "sql_requetes": ["SELECT 1"],
        "sql_lignes": [[[1]]],
    }
    if note:
        enr.update({k: v + 0.01 for k, v in SCORES.items()})  # scores déjà là, à ne pas toucher
    if erreur:
        enr["error_notation"] = erreur
    return enr


@pytest.fixture
def fichier(tmp_path, monkeypatch):
    """Écrit un run dans un dossier temporaire et redirige `renoter` vers lui."""
    dossier = tmp_path / "results"
    dossier.mkdir()
    # `renoter` résout son chemin depuis __file__ : on le fait pointer vers tmp_path
    monkeypatch.setattr(ev, "__file__", str(tmp_path / "evaluate_ragas.py"))

    def ecrire(contenu):
        chemin = dossier / f"{LABEL}.json"
        chemin.write_text(json.dumps(contenu, ensure_ascii=False), encoding="utf-8")
        return chemin

    return ecrire


@pytest.fixture
def juge_simule(monkeypatch):
    """Remplace le juge : aucun appel OpenAI, et on compte les cas notés."""
    vus = []
    monkeypatch.setattr(ev, "creer_juge", lambda: "juge simulé")

    def faux_noter(metriques, question, reference, reponse, contextes):
        vus.append(question)
        return dict(SCORES)

    monkeypatch.setattr(ev, "noter", faux_noter)
    return vus


def test_seuls_les_cas_sans_score_sont_renotes(fichier, juge_simule):
    """Renoter un cas déjà noté, c'est repayer le juge pour rien."""
    chemin = fichier([cas("S1", note=True), cas("S2", note=False, erreur="429 quota")])

    ev.renoter(LABEL, pause=0)

    assert len(juge_simule) == 1, "un seul cas était à noter"
    assert "S2" in juge_simule[0]

    apres = {c["id"]: c for c in json.loads(chemin.read_text(encoding="utf-8"))}
    assert apres["S2"]["faithfulness"] == SCORES["faithfulness"]
    assert "error_notation" not in apres["S2"], "le marqueur d'échec doit disparaître"
    # Le cas déjà noté garde SES scores, pas ceux du faux juge
    assert apres["S1"]["faithfulness"] == SCORES["faithfulness"] + 0.01


def test_la_sortie_du_systeme_est_preservee(fichier, juge_simule):
    """Ce que Mistral a produit ne doit pas bouger : c'est ce qui a coûté."""
    chemin = fichier([cas("S3", note=False, erreur="429 quota")])

    ev.renoter(LABEL, pause=0)

    apres = json.loads(chemin.read_text(encoding="utf-8"))[0]
    assert apres["system_response"] == "La réponse du système."
    assert apres["route"] == "base"
    assert apres["route_motif"] == "chiffre demandé"
    assert apres["sql_requetes"] == ["SELECT 1"]
    assert apres["retrieved_contexts"] == ["Le contexte servi."]


def test_un_run_complet_n_appelle_pas_le_juge(fichier, juge_simule):
    """Relancer la renotation sur un run déjà noté ne doit rien coûter."""
    fichier([cas("S1", note=True), cas("S2", note=True)])

    ev.renoter(LABEL, pause=0)

    assert juge_simule == [], "aucun appel ne devait partir"


def test_un_second_echec_laisse_le_cas_renotable(fichier, monkeypatch):
    """Si le juge retombe en panne, le cas reste marqué — et repris au coup suivant."""
    chemin = fichier([cas("S4", note=False, erreur="429 quota")])
    monkeypatch.setattr(ev, "creer_juge", lambda: "juge simulé")

    def juge_en_panne(*a, **k):
        raise RuntimeError("429 toujours pas de crédits")

    monkeypatch.setattr(ev, "noter", juge_en_panne)

    ev.renoter(LABEL, pause=0)

    apres = json.loads(chemin.read_text(encoding="utf-8"))[0]
    assert "error_notation" in apres
    assert "429" in apres["error_notation"]
    assert apres["system_response"] == "La réponse du système."


def test_un_cas_sans_sortie_systeme_est_signale(fichier, juge_simule, capsys):
    """Un échec système, lui, est irrécupérable : il n'y a rien à noter."""
    fichier([{"id": "S5", "error": "timeout", "etape": "systeme"},
             cas("S6", note=False, erreur="429 quota")])

    ev.renoter(LABEL, pause=0)

    sortie = capsys.readouterr().out
    assert "irrécupérables" in sortie
    assert "S5" in sortie
    assert len(juge_simule) == 1, "seul S6 est notable"
