# tests/test_schemas.py
"""Tests des contrats de src/schemas.py.

Gratuits (aucun appel API). Chaque test décrit une dégradation réelle que le contrat
doit arrêter, pas une propriété abstraite du modèle.
"""
import pytest
from pydantic import ValidationError

from schemas import (
    EMBEDDING_DIM,
    MIN_CARACTERES_DOCUMENT,
    Chunk,
    DocumentMetadata,
    LotEmbeddings,
    Question,
    RAGAnswer,
    SearchResult,
    SourceDocument,
)


SOURCE = {"source": "Reddit 1.pdf"}


def vecteur(valeur=0.1):
    return [valeur] * EMBEDDING_DIM


# --- Pipeline : sortie des embeddings ---------------------------------------------


def test_lot_complet_accepte():
    lot = LotEmbeddings(vecteurs=[vecteur(), vecteur(0.2)], fragments_envoyes=2)
    assert len(lot.vecteurs) == 2


def test_lot_plus_court_refuse():
    """Un lot en échec côté API renvoie moins de vecteurs que de fragments : tout ce
    qui suit serait décalé, un identifiant ne désignant plus le bon texte."""
    with pytest.raises(ValidationError, match="2 vecteurs pour 3 fragments"):
        LotEmbeddings(vecteurs=[vecteur(), vecteur()], fragments_envoyes=3)


def test_vecteur_nul_refuse():
    """Le cas le plus insidieux : des vecteurs nuls insérés après un échec préservent
    l'alignement, donc franchissent tout contrôle de longueur. FAISS les accepte, leur
    similarité vaut 0 pour toute question, et ces fragments ne remontent jamais."""
    with pytest.raises(ValidationError, match="norme nulle"):
        LotEmbeddings(vecteurs=[[0.0] * EMBEDDING_DIM], fragments_envoyes=1)


def test_mauvaise_dimension_refusee():
    """Un changement de modèle d'embedding produirait un index incompatible avec
    l'existant, sans aucune erreur au moment de l'écriture."""
    with pytest.raises(ValidationError, match="dimension 512"):
        LotEmbeddings(vecteurs=[[0.1] * 512], fragments_envoyes=1)


def test_valeurs_non_finies_refusees():
    with pytest.raises(ValidationError, match="NaN ou infini"):
        LotEmbeddings(vecteurs=[[float("nan")] * EMBEDDING_DIM], fragments_envoyes=1)


# --- Pipeline : documents et fragments --------------------------------------------


def document(contenu):
    return SourceDocument(
        page_content=contenu,
        metadata=DocumentMetadata(source="fichier.pdf", filename="fichier.pdf"),
    )


def test_document_vide_refuse():
    """Une feuille Excel vide ou un PDF illisible produit un texte vide : l'indexer
    coûterait des appels d'embedding pour un fragment inexploitable."""
    with pytest.raises(ValidationError):
        document("   ")


def test_document_residuel_refuse():
    """Le cas que `if not extracted_content` laisse passer : une extraction ratée qui
    ne renvoie pas une chaîne vide mais un résidu de mise en page."""
    with pytest.raises(ValidationError, match="trop court"):
        document("\nPage 1\n---\n")
    assert document("x" * MIN_CARACTERES_DOCUMENT).page_content


def test_fragment_sans_source_refuse():
    """Sans source, le fragment s'affiche « Inconnue » dans le prompt et la réponse
    devient intraçable."""
    with pytest.raises(ValidationError, match="source"):
        Chunk(id="0_0", text="t", metadata={"category": "root"})


def test_identifiant_de_fragment_verifie():
    """`id` relie un vecteur de l'index à son texte : sa forme n'est pas décorative."""
    assert Chunk(id="3_12", text="t", metadata=SOURCE).id == "3_12"
    with pytest.raises(ValidationError, match="'n_m'"):
        Chunk(id="fragment-3", text="t", metadata=SOURCE)


def test_texte_detoure_et_non_vide():
    """Les espaces sont retirés avant la mesure de longueur — d'où `StringConstraints`
    et non `Field()`, qui ignore `strip_whitespace` sans rien signaler."""
    assert Chunk(id="0_0", text="  Cade a impressionné.  ", metadata=SOURCE).text == "Cade a impressionné."
    with pytest.raises(ValidationError):
        Chunk(id="0_0", text="   ", metadata=SOURCE)


# --- Système : entrée et contexte --------------------------------------------------


def test_question_bornee():
    assert Question(texte="  Combien de points ?  ").texte == "Combien de points ?"
    with pytest.raises(ValidationError):
        Question(texte="       ")  # que des espaces
    with pytest.raises(ValidationError):
        Question(texte="x" * 2001)


def test_score_de_recherche_borne():
    """Similarité cosinus en pourcentage, donc dans [-100, 100].

    La borne basse n'est pas 0 : le produit scalaire de deux vecteurs normalisés vit
    dans [-1, 1], et un fragment opposé à la question obtient légitimement un score
    négatif. Le refuser écarterait un résultat valide.
    """
    assert SearchResult(id="0_1", text="t", score=87.2).score == 87.2
    assert SearchResult(id="0_1", text="t", score=-12.5).score == -12.5
    with pytest.raises(ValidationError):
        SearchResult(id="0_1", text="t", score=140)


# --- Système : sortie ---------------------------------------------------------------


def test_abstention_doit_etre_motivee():
    """Une abstention sans motif est inexploitable : l'utilisateur ne sait pas si la
    donnée manque ou si la question était mal posée."""
    with pytest.raises(ValidationError, match="motivée"):
        RAGAnswer(answer="Non.", abstain=True)


def test_texte_visible_porte_le_motif():
    """Ce que le juge de l'évaluation note. Le motif porte la substance de la réponse :
    le retirer reviendrait à noter une phrase creuse."""
    r = RAGAnswer(
        answer="Je ne peux pas répondre.",
        abstain=True,
        abstain_reason="Reggie Miller est absent des données statistiques.",
    )
    assert "Reggie Miller" in r.texte_visible()

    # Sans abstention, le texte visible est la réponse seule
    assert RAGAnswer(answer="1827 points.", citations=["0_1"]).texte_visible() == "1827 points."


def test_descriptions_transmises_au_modele():
    """Les `description` des champs voyagent vers le modèle dans le schéma JSON : ce
    sont des instructions, pas des commentaires. Les perdre changerait le comportement
    sans qu'aucun test de logique ne le voie."""
    champs = RAGAnswer.model_json_schema()["properties"]
    assert "contexte" in champs["answer"]["description"]
    assert "abstain" in champs["abstain_reason"]["description"]


def test_citation_decoree_normalisee():
    """Le modèle recopie volontiers l'étiquette entière du contexte.

    Observé sur un appel réel : `['[1_14] Sure_Station9370', '[1_15] The_Capulet']`.
    Ces citations désignent sans ambiguïté les fragments 1_14 et 1_15 ; les refuser
    coûterait un appel de relance pour un simple habillage.
    """
    r = RAGAnswer(answer="x", citations=["[1_14] Sure_Station9370", "[1_15] The_Capulet"])
    assert r.citations == ["1_14", "1_15"]
    assert RAGAnswer(answer="x", citations=["[0_3]"]).citations == ["0_3"]


def test_citation_ambigue_non_normalisee():
    """La tolérance s'arrête à l'habillage : une chaîne portant zéro ou plusieurs
    identifiants est laissée telle quelle, pour être rejetée par la vérification en
    aval — seule à connaître les fragments réellement servis."""
    for ambigue in (["fragment 2_5 et 3_1"], ["aucun identifiant"], [""]):
        assert RAGAnswer(answer="x", citations=ambigue).citations == ambigue


def test_identifiant_invente_reste_invente():
    """La normalisation ne doit jamais transformer un identifiant faux en vrai."""
    assert RAGAnswer(answer="x", citations=["[9_99] inventé"]).citations == ["9_99"]
