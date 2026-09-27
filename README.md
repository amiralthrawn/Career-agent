# career-agent

## Objectif

`career-agent` est un agent de carrière personnel, d'abord utilisé par son fondateur pour
automatiser sa recherche d'emploi / d'alternance. À terme : comprendre le profil d'un
candidat, rechercher et qualifier des offres, adapter les candidatures, identifier des
contacts, envoyer des emails personnalisés, suivre les résultats et apprendre de l'historique.

**État actuel : fondation technique + Candidate Brain V0.1** (représentation structurée du
candidat : faits, préférences, contraintes, preuves). Aucun agent métier, LLM, scraping,
automatisation navigateur, candidature ou envoi d'email n'est encore implémenté.

## Stack

- Python 3.12+
- FastAPI + uvicorn
- PostgreSQL, SQLAlchemy 2.x (driver `psycopg` 3), Alembic
- Pydantic 2 / pydantic-settings
- pytest
- Frontend futur : Next.js / TypeScript (non commencé)

## Architecture actuelle

```
app/
├── api/          routes HTTP (aucune logique métier)
├── core/         configuration centralisée, moteur/session SQLAlchemy, erreurs
├── models/       modèles SQLAlchemy (Candidate Brain)
├── schemas/      schémas Pydantic
├── services/     logique métier
├── repositories/ accès aux données
├── agents/       futurs agents (vide)
└── main.py       application FastAPI
data/private/   données personnelles (ignorées par Git)
migrations/     environnement Alembic
tests/  docs/  scripts/
```

Détails : [docs/architecture.md](docs/architecture.md) et
[docs/candidate-brain.md](docs/candidate-brain.md) (modèle de données du Candidate Brain) et
[docs/cv-ingestion.md](docs/cv-ingestion.md) (ingestion locale du CV, avec validation humaine).

## Installation

Prérequis : Python 3.12+ et PostgreSQL.

```powershell
py -3.12 -m venv .venv          # ou toute version >= 3.12
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env     # puis renseigner DATABASE_URL
python scripts/init_private_dirs.py
```

Exemple de `DATABASE_URL` (à adapter, dans `.env` uniquement) :
`postgresql+psycopg://UTILISATEUR:MOT_DE_PASSE@localhost:5432/career_agent`

Créer la base PostgreSQL correspondante, puis appliquer les migrations :

```powershell
alembic upgrade head
```

Les migrations `0001` (Candidate Brain) et `0002` (ingestion de CV) créent les tables.

## Lancer le backend

```powershell
python scripts/manage_secrets.py init-api-token   # une fois : écrit API_TOKEN dans .env (sans l'afficher)
python scripts/run_api.py                          # écoute uniquement sur 127.0.0.1
```

- Healthcheck (public) : <http://127.0.0.1:8000/health>
- Documentation interactive : <http://127.0.0.1:8000/docs> (bouton *Authorize* avec le jeton)
- Toutes les routes `/api/*` exigent `Authorization: Bearer <API_TOKEN>` ; sans jeton configuré,
  elles répondent 503 (échec fermé).

## Lancer les tests

```powershell
pytest
```

Les tests ne nécessitent ni serveur PostgreSQL ni fichier `.env` : ils utilisent SQLite en
mémoire (les modèles n'emploient que des types portables) et vérifient que les migrations
Alembic correspondent aux modèles.

## API Candidate Brain

Routes sous `/api/candidate` : `GET`/`POST` sur `` (candidat), `/skills`, `/projects`,
`/experiences`, `/education`, `/certifications`, `/languages`, `/preferences`, `/constraints`,
`/evidence`, et `POST /evidence-links`. Documentation interactive sur `/docs`.

## Ingestion du CV

Le CV (`.docx`) placé dans `data/private/documents/` est lu localement (aucun LLM ni service
externe) et produit des **propositions** ; rien n'entre dans le Candidate Brain sans acceptation
humaine (`/api/candidate/ingestions/cv`, `/api/candidate/proposals/...`). Les dates gardent la
précision du CV (année, mois ou jour). Pour examiner les propositions sans rien stocker :
`python scripts/preview_cv_proposals.py documents/<cv>.docx` écrit un rapport HTML dans
`data/private/reviews/` (ignoré par Git). Voir [docs/cv-ingestion.md](docs/cv-ingestion.md).

## Cibles de candidature (étape 2)

La **Target** est l'unité du pipeline : une entreprise, avec une offre *optionnelle* (candidature
sur offre) ou sans offre (candidature spontanée). Entreprises, offres et contacts gardent leur
**source** ; aucun contact ni e-mail n'est jamais deviné. Import local d'un CSV placé dans
`data/private/imports/` (aperçu sans écriture, puis application idempotente). Voir
[docs/targets.md](docs/targets.md).

## Qualification des cibles (étape 3a)

Un **profil de recherche** (critères `required` / `preferred` / `flexible`) qualifie les cibles de
façon déterministe et explicable : `excluded`, `needs_information` ou `candidate`. **Aucun score.**
Une donnée inconnue n'est jamais une violation ; seule une incompatibilité *connue* d'un critère
obligatoire exclut, et une cible exclue est conservée avec ses raisons. Les critères sont
immuables (une modification crée une nouvelle version) et les qualifications sont en ajout seul.
Voir [docs/qualification.md](docs/qualification.md).

## Exigences et correspondance avec le Candidate Brain (étape 3b)

Les **exigences** d'une cible (compétences citées par l'offre, durée d'expérience explicite) sont
extraites de façon déterministe (taxonomie versionnée, sans réseau ni LLM), avec la **source** et
l'**extrait exact** du texte. L'importance (`required` / `nice_to_have`) ne vient que de marqueurs
explicites ; dans le doute : `unspecified`. Chaque qualification enregistre ce que le Brain
**établit** pour chaque exigence : `covered` (une *Skill* `known`/`verified`), `weak`, `gap`
(« non établi », jamais « le candidat ne l'a pas ») ou `unmeasurable` (ex. dates à l'année). Un
projet ne devient jamais une compétence ; un voisin (Tableau, pandas) ne couvre jamais Power BI ou
Python, seule une couverture explicite et à sens unique de la taxonomie compte (une Skill PostgreSQL
couvre une exigence SQL, pas l'inverse). Aucun
score : seulement des compteurs bruts, et le statut de 3a n'est jamais modifié. Un
`PersonalizationBrief` (forces, ce qu'il ne faut pas affirmer, questions ouvertes, angles à
privilégier) est calculé à la demande ; s'il repose sur une qualification périmée il est renvoyé avec
`stale: true` et le futur module de personnalisation **doit** vérifier ce champ avant de générer.
Voir [docs/requirements.md](docs/requirements.md).

## Sourcing (étape 3c)

Le sourcing transforme les résultats d'un **fournisseur de données** en cibles, via les services
existants : mode `offers` (offres publiées : Company + Opportunity + Target) ou `companies`
(entreprises pour candidatures spontanées : Target spontanée, **sans fausse offre** et sans jamais
affirmer que l'entreprise recrute). Deux ports distincts (`WebSearchProvider`, `OfferSource`), un
`HitExtractor` déterministe qui n'infère rien et rejette avec un motif structuré, et un
`SearchRun` auditable (compteurs, codes d'erreur, aucun payload ni secret). **Aucun fournisseur réel
n'est branché** : ni recherche web, ni Perplexity, ni OpenRouter, ni LLM, ni réseau ; le registre est
vide par défaut. Voir [docs/sourcing.md](docs/sourcing.md).

## Brouillons de candidature (étape 4)

Premier étage LLM : transforme le Candidate Brain, la qualification, les `RequirementMatch` et le
`PersonalizationBrief` en un **brouillon** de candidature (`ApplicationDraft`, statut `proposed`),
jamais envoyé automatiquement. **Le LLM est un générateur, jamais une source de vérité** : les
`claims` (affirmations sûres) et les `warnings` (ce qu'il ne faut pas affirmer) sont construits par
l'application à partir du brief, jamais extraits du texte du modèle. Un `LLMClient` abstrait
(`FakeLLMClient` déterministe pour les tests, sans réseau) masque tout fournisseur ; un adaptateur
OpenRouter est préparé derrière le `SecretStore` existant, **désactivé par défaut**
(`LLM_ENABLED=false`) et vérifié une fois manuellement contre l'API réelle (étape 5, deux modèles
comparés avec un scénario synthétique identique, script `scripts/manual/openrouter_smoke_test.py`,
jamais dans la suite pytest). Un humain doit approuver ou rejeter chaque brouillon ; aucune route
d'envoi n'existe. Voir [docs/drafts.md](docs/drafts.md).

## Recherche externe et contrat fournisseur (étape 6)

Chaque IA externe reçoit désormais un **contrat explicite** (`ProviderContract` : rôle,
responsabilités, limites, ce qu'on lui fournit, ce que Career-agent en fait ensuite), rendu en un
bloc de texte stable et réutilisable, séparé du contexte propre à chaque appel. **La sortie d'un
fournisseur n'est jamais automatiquement la vérité de Career-agent** : Perplexity renvoie des
`Observation` externes et sourcées (`ResearchResult`), jamais une décision (« bonne cible »,
score, candidature à envoyer) — rien de tel n'existe dans ces types, et aucune méthode n'écrit en
base. `CompanyResearchService` construit la requête depuis une `Company` existante (jamais de
donnée candidat) et renvoie le résultat du fournisseur inchangé. Adaptateur Perplexity préparé
derrière le `SecretStore` existant, **désactivé par défaut** (`RESEARCH_ENABLED=false`) et vérifié
une fois manuellement contre l'API réelle (`scripts/manual/perplexity_smoke_test.py`, jamais dans
la suite pytest). Aucune route API pour cette étape. Voir [docs/providers.md](docs/providers.md).

## Requalification et traitement par lot (étape 7)

Pour 500 opportunités, Career-agent ne demande pas 500 recherches Perplexity. Il qualifie d'abord
lui-même chaque cible (règles déterministes inchangées), identifie précisément quelles inconnues
(`InformationNeed`) **pourraient changer le statut** — seul un critère `required` actuellement
`unknown` compte, un `preferred` inconnu reste informatif et jamais bloquant — regroupe les
recherches **par entreprise** (plusieurs offres partagent souvent une entreprise) dans un
`ResearchPlan`, applique un **budget** d'appels (`max_research_calls`, 0 par défaut), interroge
Perplexity uniquement pour ce qui est pertinent, puis **requalifie** avec les mêmes règles
déterministes enrichies du texte de recherche accepté (jamais une réécriture des champs de
`Company`). Une qualification reste immuable : une requalification en crée une nouvelle avec un
nouveau fingerprint, l'ancienne reste consultable. Un échec Perplexity sur une entreprise est
enregistré explicitement (`research_failed`), jamais transformé en « inconnu devient faux », et
n'affecte jamais les autres entreprises du lot. Aucun score, aucun classement global. Voir
[docs/research_batch.md](docs/research_batch.md).

## Contact intelligence (étape 8)

Career-agent recherche un interlocuteur professionnel pertinent (recruteur, RH, manager, contact
technique...) pour une cible donnée, sans jamais décider seul qui contacter ni inventer une
coordonnée. Chaque résultat Perplexity est stocké comme une **proposition** en attente
(`ContactResearchObservation`, statut `pending`/`accepted`/`rejected` — même mécanisme que les
propositions d'ingestion de CV) : rien ne devient un `Contact`/`ContactChannel` réel sans
acceptation humaine explicite, qui réutilise le service existant, inchangé,
`ContactService.record_contact` (déduplication par nom/entreprise, respect de `do_not_contact`,
jamais de fusion automatique d'homonymes entre deux entreprises différentes). Une adresse e-mail
n'est enregistrée que si elle est explicitement publiée dans une source **et** confirmée par
l'humain au moment de l'acceptation — jamais devinée, déduite d'un format supposé, ni testée par
SMTP. Traitement par lot inspiré de l'étape 7 (regroupement par entreprise et catégorie de
contact, budget explicite, isolation des échecs, aucune recherche redondante quand un contact de
cette catégorie est déjà connu). Aucun score, aucun classement, aucune route d'envoi. Voir
[docs/contacts_research.md](docs/contacts_research.md).

## Application workflow (étape 9)

Transforme une cible qualifiée en candidature exploitable : `ApplicationPackage` référence le CV
original (jamais généré ni modifié), le brouillon `ApplicationDraft` existant (réutilisé sans
changement, juste un paramètre additif optionnel `extra_context`), le contact **accepté** de
l'étape 8 (jamais une observation en attente) et un contexte de personnalisation clairement
séparé : faits d'entreprise déjà acceptés (étape 7, quelques éléments seulement), et **preuves
GitHub** — un nouveau provider dédié (`app/integrations/github/`) qui lit les dépôts publics du
candidat (jamais un fork, jamais une invention de compétence ou d'expérience professionnelle : un
dépôt reste toujours un « projet personnel ») et les met en correspondance déterministe avec les
libellés des exigences du poste (recouvrement de mots, jamais un score). Cycle de validation
humaine `draft → pending_validation → approved/rejected` : aucun passage automatique, une
candidature refusée n'est jamais envoyée. Idempotence et péremption par empreinte, comme pour la
`Qualification`. Aucun score global, aucun classement, aucune route d'envoi. Voir
[docs/application_workflow.md](docs/application_workflow.md).

## Envoi contrôlé par lot et intégration Gmail (étape 10)

Une candidature `approved` (étape 9) peut être sélectionnée, seule ou en lot de plusieurs
dizaines, dans un `SendBatch`. Deux approbations humaines explicites et distinctes sont
nécessaires avant tout envoi réel : l'approbation individuelle de la candidature (étape 9,
inchangée) **et** l'approbation explicite du lot lui-même — un envoi individuel n'est d'ailleurs
qu'un lot d'un seul élément, via exactement le même mécanisme, jamais un chemin parallèle. Chaque
précondition est revérifiée juste avant l'envoi de CHAQUE candidature (approuvée, non périmée,
contact toujours accepté, `do_not_contact=false`, adresse e-mail présente, CV disponible, jamais
déjà envoyée) : un échec exclut uniquement cet élément, sans jamais bloquer ni fausser le reste du
lot (« 13 envoyés, 2 exclus » reste précis, jamais arrondi à 15). Une garantie au niveau base de
données (index unique partiel) empêche qu'une même candidature soit jamais envoyée deux fois,
même après une relance suite à un échec réseau partiel ; un `Message-ID` déterministe, conservé
d'une tentative à l'autre, permet en plus de vérifier auprès de Gmail (recherche `rfc822msgid:`,
best-effort) si un message a réellement été envoyé avant de retenter — utile si Google a bien
accepté l'e-mail mais que la réponse HTTP a été perdue. Nouveau client Gmail
(`app/integrations/gmail/`) implémentant le port `MailProvider` existant (étape 1, inchangé) : OAuth
2.0, jamais de mot de passe Gmail, jetons uniquement dans `SecretStore`. `SEND_MODE=auto` est
désormais disponible (sans liste blanche de destinataires : le contrôle est la double approbation
explicite). Aucun cron, aucun envoi déclenché automatiquement par la préparation d'une
candidature. Voir [docs/send_batches.md](docs/send_batches.md).

## Sécurité locale, secrets et e-mail (étape 1)

- API protégée par jeton, contrôle de l'en-tête `Host`, CORS fermé par défaut.
- Secrets dans le Gestionnaire d'identifiants Windows via `SecretStore` (jamais dans un fichier).
- `SEND_MODE=disabled` par défaut ; `dry_run` écrit un `.eml` local (CV joint tel quel) dans
  `data/private/outbox/` ; `manual` (liste blanche) et `auto` (étape 10, envoi par lot contrôlé)
  existent aussi, chacun exigeant une approbation humaine explicite avant tout envoi réel.
- Journal d'audit append-only (aucun contenu de mail, aucun secret).

Voir [docs/security.md](docs/security.md) et [docs/mail-architecture.md](docs/mail-architecture.md).

## Politique sur les données privées

- Toutes les données personnelles du candidat (profil, CV, documents, portfolio,
  candidatures) vivent uniquement dans `data/private/`.
- `data/private/` et `.env` sont ignorés par Git ; `.env.example` ne contient que des noms de
  variables, sans secret.
- Aucune clé API, token, mot de passe ou donnée personnelle ne doit être écrit dans le code ni
  commité. Toute configuration passe par les variables d'environnement.
- Aucune donnée fictive ressemblant à de vraies données de candidat n'est ajoutée au dépôt.
