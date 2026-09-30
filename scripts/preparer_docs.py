# scripts/preparer_docs.py
"""Prépare ce que `docs/` ne contient pas en propre, avant chaque construction du site.

Deux choses, pour la même raison — **ne pas maintenir de doublon** :

- le notebook d'analyse vit dans `eval/`, à côté des résultats qu'il lit ;
- les figures du comparatif sont calculées depuis `eval/results/`, jamais recopiées.

Les deux sont régénérés à chaque construction et ignorés par git : l'original reste la
seule version qu'on modifie.

MkDocs appelle `on_pre_build()` via la clé `hooks:` de `mkdocs.yml`. Le module se lance
aussi seul, pour régénérer les figures sans construire le site :

    uv run python scripts/preparer_docs.py
"""
import json
import logging
import shutil
import statistics as st
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
RESULTATS = RACINE / "eval" / "results"
NOTEBOOK_ORIGINAL = RACINE / "eval" / "analyze_results.ipynb"
NOTEBOOK_COPIE = RACINE / "docs" / "notebook.ipynb"
FIGURES = RACINE / "docs" / "assets"

RUNS = ["baseline", "reddit_only", "pydantic_contracts", "sql_routing"]
METRIQUES = ["faithfulness", "context_precision", "context_recall", "answer_correctness"]
ORDRE_MOD = ["pdf_reddit", "excel", "hybride"]
ETIQUETTES_MOD = {"pdf_reddit": "Reddit", "excel": "Excel", "hybride": "hybride"}

# La grille de contrôle : une LIGNE par nature de jugement. Celle du bas porte les deux
# métriques qui ne se comparent pas au quatrième run — leur unité a changé. La
# disposition dit donc l'avertissement, au lieu de compter sur une légende.
GRILLE = [
    ("faithfulness", "answer_correctness"),    # ce qui juge la réponse
    ("context_precision", "context_recall"),   # ce qui juge la récupération
]
INTITULE_LIGNES = "en haut ce qui juge la réponse, en bas ce qui juge la récupération"

# Un marqueur par modalité : l'identité ne repose jamais sur la seule couleur — ni pour
# un daltonien, ni sur une impression en noir et blanc.
MARQUEURS_MOD = {"pdf_reddit": "o", "excel": "s", "hybride": "^"}

# Les runs sont une PROGRESSION : une seule teinte, du clair au foncé. Les modalités
# sont des identités distinctes : des teintes séparées. Palette reprise du notebook
# déjà publié, pour que figures du rapport et de l'annexe se lisent comme un seul jeu.
TEINTES_RUNS = ["#c9d3dc", "#94aec8", "#5580a8", "#1f4e79"]
TEINTES_MOD = ["#4a7fb5", "#c4693d", "#5b9e6f"]

# Le texte porte une encre neutre, jamais la couleur d'une série.
THEMES = {
    "light": {"fond": "#ffffff", "encre": "#1a1a1a", "encre_faible": "#5a5a5a", "grille": "#d8d8d8"},
    "dark": {"fond": "#1e2129", "encre": "#e6e6e6", "encre_faible": "#a0a0a0", "grille": "#3a3f4a"},
}

log = logging.getLogger("mkdocs.hooks")


# --- le notebook ---------------------------------------------------------------------


def copier_notebook() -> bool:
    """Copie le notebook dans `docs/`. Rend False s'il est introuvable.

    Un original absent n'interrompt pas la construction : le site se bâtit sans la page
    du notebook, ce qui vaut mieux qu'un échec au moment de publier.
    """
    if not NOTEBOOK_ORIGINAL.exists():
        log.warning(
            f"{NOTEBOOK_ORIGINAL.name} introuvable : la page du notebook sera absente."
        )
        return False

    NOTEBOOK_COPIE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(NOTEBOOK_ORIGINAL, NOTEBOOK_COPIE)  # copy2 préserve la date
    return True


# --- les figures ---------------------------------------------------------------------


def charger_runs() -> dict[str, list[dict]]:
    """Les cas NOTÉS de chaque run — un cas sans score fausserait les moyennes."""
    return {
        nom: [
            c
            for c in json.loads((RESULTATS / f"{nom}.json").read_text(encoding="utf-8"))
            if "answer_correctness" in c
        ]
        for nom in RUNS
    }


def moyenne(cas: list[dict], metrique: str, modalite: str | None = None) -> float:
    """Moyenne d'une métrique, éventuellement restreinte à une modalité."""
    retenus = [c for c in cas if modalite is None or c["modalite"] == modalite]
    return st.mean(c[metrique] for c in retenus)


def _habiller(ax, theme, titre, ylabel):
    """Grille discrète, axes effacés, encre neutre — le message vient des marques."""
    ax.set_facecolor(theme["fond"])
    ax.set_title(titre, loc="left", fontsize=10.5, color=theme["encre"], pad=12)
    ax.set_ylabel(ylabel, fontsize=9, color=theme["encre_faible"])
    ax.tick_params(colors=theme["encre_faible"], labelsize=9)
    ax.grid(axis="y", color=theme["grille"], alpha=0.6, linewidth=0.8)
    ax.set_axisbelow(True)
    for cote, visible in (("top", False), ("right", False), ("left", False), ("bottom", True)):
        ax.spines[cote].set_visible(visible)
        if visible:
            ax.spines[cote].set_color(theme["grille"])


def _legende(ax, theme, **kwargs):
    legende = ax.legend(fontsize=8.5, frameon=False, **kwargs)
    for texte in legende.get_texts():
        texte.set_color(theme["encre_faible"])


def figure_metriques(donnees: dict, theme_nom: str) -> Path:
    """Les quatre métriques, les quatre runs. Barres groupées par métrique."""
    import matplotlib.pyplot as plt

    theme = THEMES[theme_nom]
    fig, ax = plt.subplots(figsize=(9, 3.8), facecolor=theme["fond"])

    largeur = 0.2
    positions = range(len(METRIQUES))
    for i, (nom, couleur) in enumerate(zip(RUNS, TEINTES_RUNS)):
        valeurs = [moyenne(donnees[nom], m) for m in METRIQUES]
        ax.bar([p + i * largeur for p in positions], valeurs, largeur * 0.9,
               label=nom, color=couleur)

    ax.set_xticks([p + 1.5 * largeur for p in positions])
    ax.set_xticklabels([m.replace("_", "\n") for m in METRIQUES])
    ax.set_ylim(0, 1)
    _habiller(ax, theme, "Les quatre métriques, du prototype livré à l'état actuel", "score moyen")
    _legende(ax, theme, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.02))

    chemin = FIGURES / f"metriques-{theme_nom}.png"
    fig.tight_layout()
    fig.savefig(chemin, dpi=160, facecolor=theme["fond"])
    plt.close(fig)
    return chemin


def figure_par_modalite(donnees: dict, theme_nom: str) -> Path:
    """`answer_correctness` par modalité : c'est là que l'histoire se lit."""
    import matplotlib.pyplot as plt

    theme = THEMES[theme_nom]
    fig, ax = plt.subplots(figsize=(9, 4), facecolor=theme["fond"])

    x = range(len(RUNS))
    for modalite, couleur in zip(ORDRE_MOD, TEINTES_MOD):
        valeurs = [moyenne(donnees[nom], "answer_correctness", modalite) for nom in RUNS]
        ax.plot(x, valeurs, marker="o", markersize=7, linewidth=2,
                color=couleur, label=ETIQUETTES_MOD[modalite])
        # Étiquette directe au bout de chaque ligne : l'identité n'est jamais portée
        # par la couleur seule.
        ax.annotate(f"{ETIQUETTES_MOD[modalite]}  {valeurs[-1]:.3f}",
                    (len(RUNS) - 1, valeurs[-1]), xytext=(10, 0),
                    textcoords="offset points", va="center",
                    fontsize=9, color=theme["encre"])

    ax.set_xticks(list(x))
    ax.set_xticklabels(RUNS, fontsize=8.5)
    ax.set_xlim(-0.2, len(RUNS) - 0.35)
    ax.set_ylim(0, 0.7)
    _habiller(ax, theme,
              "answer_correctness par modalité — tout le gain vient des questions Excel",
              "score moyen")
    _legende(ax, theme, ncol=3, loc="upper left")

    chemin = FIGURES / f"modalites-{theme_nom}.png"
    fig.tight_layout()
    fig.savefig(chemin, dpi=160, facecolor=theme["fond"])
    plt.close(fig)
    return chemin


def figure_par_metrique(donnees: dict, theme_nom: str) -> Path:
    """Les quatre métriques, chacune découpée par modalité : la grille de contrôle.

    C'est la figure du § 6.1. Sur la ligne du bas, la courbe **Reddit reste plate** —
    le mécanisme de récupération n'y a pas changé — là où Excel décolle du plancher
    zéro. Le bond global des deux métriques de contexte vient donc d'un changement
    d'unité, et non d'une meilleure recherche. Le tableau du rapport le démontre ; la
    figure le rend visible en une seconde.

    Axe y commun à [0, 1] sur les quatre panneaux : une échelle ajustée par panneau
    ferait paraître un mouvement de 0.05 aussi ample qu'un mouvement de 0.5, ce qui
    ruinerait exactement la comparaison que cette figure existe pour permettre.
    """
    import matplotlib.pyplot as plt

    theme = THEMES[theme_nom]
    fig, axes = plt.subplots(
        2, 2, figsize=(9.5, 6.6), facecolor=theme["fond"], sharex=True, sharey=True
    )

    x = range(len(RUNS))
    for ligne, metriques in enumerate(GRILLE):
        for colonne, metrique in enumerate(metriques):
            ax = axes[ligne][colonne]
            for modalite, couleur in zip(ORDRE_MOD, TEINTES_MOD):
                valeurs = [moyenne(donnees[nom], metrique, modalite) for nom in RUNS]
                ax.plot(x, valeurs, marker=MARQUEURS_MOD[modalite], markersize=6,
                        linewidth=1.8, color=couleur, label=ETIQUETTES_MOD[modalite])
            ax.set_ylim(0, 1)
            # Le ylabel seulement à gauche : répété quatre fois il n'informe plus.
            _habiller(ax, theme, metrique, "score moyen" if colonne == 0 else "")

    # Les noms de runs ne tiennent qu'inclinés : `pydantic_contracts` en fait dix-huit
    # caractères pour un panneau de demi-largeur.
    for ax in axes[1]:
        ax.set_xticks(list(x))
        ax.set_xticklabels(RUNS, fontsize=7.5, rotation=22, ha="right")

    fig.suptitle(
        f"Les quatre métriques par modalité — {INTITULE_LIGNES}",
        x=0.012, ha="left", fontsize=10.5, color=theme["encre"],
    )
    # Une seule légende pour les quatre panneaux : les trois mêmes séries partout.
    poignees, etiquettes = axes[0][0].get_legend_handles_labels()
    legende = fig.legend(poignees, etiquettes, ncol=3, frameon=False, fontsize=9,
                         loc="upper left", bbox_to_anchor=(0.01, 0.955))
    for texte in legende.get_texts():
        texte.set_color(theme["encre_faible"])

    chemin = FIGURES / f"metriques-par-modalite-{theme_nom}.png"
    fig.tight_layout(rect=(0, 0, 1, 0.91))  # laisse la bande du titre et de la légende
    fig.savefig(chemin, dpi=160, facecolor=theme["fond"])
    plt.close(fig)
    return chemin


def generer_figures() -> list[Path]:
    """Les six fichiers : trois figures × deux thèmes.

    Deux variantes parce que le site bascule de thème, et qu'une image à fond blanc sur
    une page sombre se lit mal. Material les choisit par le suffixe `#only-light`.
    """
    import matplotlib

    matplotlib.use("Agg")  # aucun affichage : on écrit des fichiers

    FIGURES.mkdir(parents=True, exist_ok=True)
    donnees = charger_runs()
    return [
        figure(donnees, theme_nom)
        for theme_nom in THEMES
        for figure in (figure_metriques, figure_par_modalite, figure_par_metrique)
    ]


# --- le crochet MkDocs ---------------------------------------------------------------


def on_pre_build(config, **kwargs) -> None:
    """Appelé par MkDocs avant qu'il ne parcoure `docs/`."""
    if copier_notebook():
        log.info(f"Notebook copié depuis {NOTEBOOK_ORIGINAL.name}")

    try:
        fichiers = generer_figures()
    except ImportError as e:
        log.warning(f"Figures non générées ({e}). Installer le groupe `docs`.")
        return
    log.info(f"{len(fichiers)} figures générées depuis eval/results/")


def main() -> int:
    copier_notebook()
    fichiers = generer_figures()
    print(f"{len(fichiers)} figures écrites dans {FIGURES.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
