# scripts/figures_rapport.py
"""Génère les figures du comparatif, lues depuis `eval/results/`.

Les chiffres ne sont recopiés nulle part : ils viennent des fichiers de résultats, les
mêmes que lit le notebook. Une figure ne peut donc pas diverger du tableau qu'elle
illustre.

Deux variantes par figure, claire et sombre : le site bascule de thème, et une image à
fond blanc sur une page sombre se lit mal. Material les choisit par le suffixe
`#only-light` / `#only-dark`.

Appelé par `scripts/mkdocs_hooks.py` à chaque construction. Les fichiers produits sont
jetables et ignorés par git.
"""
import json
import statistics as st
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # aucun affichage : on écrit des fichiers
import matplotlib.pyplot as plt  # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
RESULTATS = RACINE / "eval" / "results"
SORTIE = RACINE / "docs" / "assets"

RUNS = ["baseline", "reddit_only", "pydantic_contracts", "sql_routing"]
METRIQUES = ["faithfulness", "context_precision", "context_recall", "answer_correctness"]
ORDRE_MOD = ["pdf_reddit", "excel", "hybride"]
ETIQUETTES_MOD = {"pdf_reddit": "Reddit", "excel": "Excel", "hybride": "hybride"}

# Les runs sont une PROGRESSION, pas des catégories : une seule teinte, du clair au
# foncé. Les modalités, elles, sont des identités distinctes : des teintes séparées.
# Palette reprise du notebook déjà publié dans ce site, pour que les figures du
# rapport et celles de l'annexe se lisent comme un seul jeu.
TEINTES_RUNS = ["#c9d3dc", "#94aec8", "#5580a8", "#1f4e79"]
TEINTES_MOD = ["#4a7fb5", "#c4693d", "#5b9e6f"]

# Le texte porte une encre neutre, jamais la couleur d'une série.
THEMES = {
    "light": {"fond": "#ffffff", "encre": "#1a1a1a", "encre_faible": "#5a5a5a", "grille": "#d8d8d8"},
    "dark": {"fond": "#1e2129", "encre": "#e6e6e6", "encre_faible": "#a0a0a0", "grille": "#3a3f4a"},
}


def charger() -> dict:
    """Les cas notés de chaque run."""
    donnees = {}
    for nom in RUNS:
        cas = json.loads((RESULTATS / f"{nom}.json").read_text(encoding="utf-8"))
        donnees[nom] = [c for c in cas if "answer_correctness" in c]
    return donnees


def _habiller(ax, theme, titre, ylabel):
    """Grille discrète, axes effacés, encre neutre — le message doit venir des marques."""
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


def figure_metriques(donnees: dict, theme_nom: str) -> None:
    """Les quatre métriques, les quatre runs. Barres groupées par métrique."""
    theme = THEMES[theme_nom]
    fig, ax = plt.subplots(figsize=(9, 3.8), facecolor=theme["fond"])

    largeur = 0.2
    positions = range(len(METRIQUES))
    for i, (nom, couleur) in enumerate(zip(RUNS, TEINTES_RUNS)):
        valeurs = [st.mean(c[m] for c in donnees[nom]) for m in METRIQUES]
        ax.bar([p + i * largeur for p in positions], valeurs, largeur * 0.9,
               label=nom, color=couleur)

    ax.set_xticks([p + 1.5 * largeur for p in positions])
    ax.set_xticklabels([m.replace("_", "\n") for m in METRIQUES])
    ax.set_ylim(0, 1)
    _habiller(ax, theme, "Les quatre métriques, du prototype livré à l'état actuel", "score moyen")
    legende = ax.legend(fontsize=8.5, frameon=False, ncol=4, loc="upper center",
                        bbox_to_anchor=(0.5, 1.02))
    for texte in legende.get_texts():
        texte.set_color(theme["encre_faible"])

    fig.tight_layout()
    fig.savefig(SORTIE / f"metriques-{theme_nom}.png", dpi=160, facecolor=theme["fond"])
    plt.close(fig)


def figure_par_modalite(donnees: dict, theme_nom: str) -> None:
    """`answer_correctness` par modalité : c'est là que l'histoire se lit."""
    theme = THEMES[theme_nom]
    fig, ax = plt.subplots(figsize=(9, 4), facecolor=theme["fond"])

    x = range(len(RUNS))
    for modalite, couleur in zip(ORDRE_MOD, TEINTES_MOD):
        valeurs = [
            st.mean(c["answer_correctness"] for c in donnees[nom] if c["modalite"] == modalite)
            for nom in RUNS
        ]
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
    legende = ax.legend(fontsize=8.5, frameon=False, ncol=3, loc="upper left")
    for texte in legende.get_texts():
        texte.set_color(theme["encre_faible"])

    fig.tight_layout()
    fig.savefig(SORTIE / f"modalites-{theme_nom}.png", dpi=160, facecolor=theme["fond"])
    plt.close(fig)


def main() -> None:
    SORTIE.mkdir(parents=True, exist_ok=True)
    donnees = charger()
    for theme_nom in THEMES:
        figure_metriques(donnees, theme_nom)
        figure_par_modalite(donnees, theme_nom)


if __name__ == "__main__":
    main()
    print(f"4 figures écrites dans {SORTIE.relative_to(RACINE)}")
