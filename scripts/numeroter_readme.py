# scripts/numeroter_readme.py
"""Renumérote les titres du README.

Idempotent : relançable après n'importe quel remaniement de structure. Les numéros
existants sont retirés avant d'être recalculés, donc déplacer une section ne demande
aucune retouche manuelle — c'est ce qui rend la numérotation tenable.

Les blocs de code sont ignorés : un `# commentaire` Python ou un `## titre` dans un
exemple Markdown ne doit pas être pris pour un titre de section.

Un titre peut être laissé hors de la numérotation en le déclarant dans `HORS_PLAN` —
utile pour une annexe qui ne fait pas partie de la progression du document.

    uv run python scripts/numeroter_readme.py
"""
import re
import sys
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"

TITRE = re.compile(r"^(?P<niveau>#{2,4})\s+(?P<texte>.+?)\s*$")
NUMERO = re.compile(r"^\d+(\.\d+)*\.?\s+")  # « 3.2 » ou « 3. » en tête de titre

# Sections volontairement hors de la progression numérotée.
HORS_PLAN = {"Structure du dépôt"}


def numeroter(lignes: list[str]) -> tuple[list[str], list[tuple[int, str, str]]]:
    """Rend les lignes numérotées et la liste (niveau, numéro, texte) des titres."""
    compteurs = [0, 0, 0]  # ##, ###, ####
    sortie, titres = [], []
    dans_un_bloc = False

    for ligne in lignes:
        if ligne.lstrip().startswith("```"):
            dans_un_bloc = not dans_un_bloc
            sortie.append(ligne)
            continue

        m = None if dans_un_bloc else TITRE.match(ligne)
        if not m:
            sortie.append(ligne)
            continue

        texte = NUMERO.sub("", m.group("texte"))  # retire l'ancienne numérotation
        if texte in HORS_PLAN:
            sortie.append(f"{m.group('niveau')} {texte}")
            continue

        profondeur = len(m.group("niveau")) - 2  # 0 pour ##, 1 pour ###, 2 pour ####
        compteurs[profondeur] += 1
        for suivant in range(profondeur + 1, 3):
            compteurs[suivant] = 0

        numero = ".".join(str(c) for c in compteurs[: profondeur + 1])
        sortie.append(f"{m.group('niveau')} {numero} {texte}")
        titres.append((profondeur, numero, texte))

    return sortie, titres


def main() -> int:
    lignes = README.read_text(encoding="utf-8").splitlines()
    lignes, titres = numeroter(lignes)
    README.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    print(f"{len(titres)} titres numérotés.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
