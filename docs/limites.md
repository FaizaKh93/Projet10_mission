# Limites et biais

Trois familles à ne pas confondre : ce que cette évaluation **ne peut pas établir**, ce que
l'architecture **ne sait pas faire**, et ce qui **n'a pas été mesuré du tout**.

Les cas cités sont les indices qui ont révélé chaque limite, pas la limite elle-même.

## 1. Ce que l'évaluation ne peut pas établir

| Limite | Pourquoi | Piste |
|---|---|---|
| **Le jeu de test a servi au réglage** | 24 cas, sur lesquels le prompt de classification et le registre de capacités ont été ajustés. C'est un jeu de **développement**, pas un jeu de test indépendant — un score obtenu dessus majore la performance attendue | constituer un jeu tenu à l'écart, jamais consulté pendant le développement |
| **Une seule exécution par système** | aucun intervalle de confiance. La variance du juge seule vaut **±0.05**, mesurée sur un contexte identique | répéter chaque run, publier un écart-type |
| **Un seul juge, jamais confronté à l'humain** | les métriques héritent des biais de gpt-4o sans qu'on sache lesquels | accord inter-juges sur un échantillon annoté à la main |
| **Deux mécanismes changés ensemble** | l'écart entre `tool_sql` et `guardrails` ne se répartit pas entre le validateur et le cadrage du prompt | un run par mécanisme |
| **Le contexte de preuve varie entre runs** | la fidélité ne compare pas des générateurs à contexte égal : les runs avec outil SQL sont jugés sur un contexte élargi | figer le contexte jugé, ou ne comparer que des systèmes à contexte identique |
| **Un score isolé induit en erreur** | un cas affichait une fidélité de 0.50 pour une justesse de 0.07 : fidèle à un contexte hors sujet | lire les métriques ensemble, et avec l'axe comportemental |

Le premier point est le plus important, et il a une histoire. Une version antérieure du
prompt de classification contenait **trois questions du jeu de test, mot pour mot**. Le
score de 22/24 qu'elle affichait était donc gonflé. Les exemples ont été retirés, et le
score honnête — mesuré sur des cas inédits — est le même : **22/24**. Mais il aurait pu ne
pas l'être, et rien dans le chiffre ne l'aurait signalé.

## 2. Ce que l'architecture ne sait pas faire

| Limite | Indice | Conséquence en production | Piste |
|---|---|---|---|
| **Récupération à `k` fixe, sans seuil ni reclassement** | M1, B6 | une reformulation anodine change la réponse ; un document entier ne peut pas être parcouru | seuil de similarité, reclassement par cross-encoder, parcours par lots |
| **Aucune vérification des entités envoyées au SQL** | M1, M4 | une erreur d'identification en amont produit une requête **correcte sur la mauvaise entité** : elle se propage sans aucun signal | exiger qu'une entité requêtée soit justifiée par un fragment cité |
| **Le garde-fou repose lui-même sur un modèle** | M2 | son mode d'échec est le refus excessif, silencieux pour l'utilisateur | journaliser chaque refus, suivre son taux d'erreur en continu |
| **Le registre ne couvre que les dimensions déclarées** | — | une dimension absente du registre passe comme avant : **le mécanisme ne protège que de ce qu'on a su nommer** | étendre le vocabulaire à mesure que des cas remontent des journaux |
| **Périmètre des données** : une saison, saison régulière, aucune granularité fine | B1, B2, M6 | une part des questions légitimes est **intrinsèquement sans réponse** | seconde source : calendrier, box scores |
| **La même source arrive par deux chemins** | B3 | le classeur est à la fois vectorisé et interrogeable ; sa version en fragments est une représentation dégradée d'un tableau | donner la préséance à la base pour tout chiffre — fait, mais non isolé dans la mesure |

La quatrième ligne est celle qui limite le plus la portée du travail. Le validateur est
efficace **sur les dimensions qu'on a pensé à inscrire** dans le registre. Une question
portant sur une dimension imprévue — la météo, la blessure, le temps de repos entre deux
matchs — passerait exactement comme avant.

Un exemple concret de cette limite a été découvert en testant : une mention de poste dans
la **partie textuelle** d'une question suffit à déclencher le filtre correspondant, alors
que la base n'a pas besoin de filtrer par poste. **Rien dans le vocabulaire ne distingue
« la base doit filtrer par poste » de « la question cite un poste ».**

## 3. Ce qui n'a pas été mesuré

- **Robustesse aux formulations.** Aucune paraphrase, faute de frappe ni question hors
  domaine dans le jeu de test. Un cas suggère pourtant que la sensibilité est réelle :
  raccourcir une question a suffi à faire sortir le bon fragment des cinq premiers, et le
  système a répondu sur un autre joueur.
- **Injection de prompt.** Le corpus est du **texte d'utilisateurs Reddit**, injecté tel
  quel dans le prompt. Rien ne vérifie qu'un commentaire ne peut pas détourner l'agent.
  C'est l'angle mort le plus sérieux pour une mise en production.
- **Latence et coût à l'échelle.** Mesurés sur des appels isolés, jamais sous charge.
- **Dérive.** Corpus et schéma figés. L'arrivée d'une saison supplémentaire invaliderait
  le registre de capacités, et **rien ne le détecterait** en production.
- **Charge et concurrence.** Le système n'a jamais été exercé au-delà d'une question à la
  fois.

## Portée des conclusions

Les résultats décrivent le comportement du système **sur ce jeu de test**, réglé en partie
sur lui, avec **un seul modèle générateur** et **un seul juge**, en **une exécution**.

Ils établissent solidement une chose : le retrait déterministe de l'outil corrige les
questions hors périmètre, et cette correction se mesure sans juge automatique. Tout le
reste — les écarts de métriques RAGAS — porte la variance du juge et l'absence de
répétition.
