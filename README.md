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

## Sécurité locale, secrets et e-mail (étape 1)

- API protégée par jeton, contrôle de l'en-tête `Host`, CORS fermé par défaut.
- Secrets dans le Gestionnaire d'identifiants Windows via `SecretStore` (jamais dans un fichier).
- `SEND_MODE=disabled` par défaut ; `dry_run` écrit un `.eml` local (CV joint tel quel) dans
  `data/private/outbox/` ; **aucun envoi réel n'est possible** à ce stade, et `auto` est refusé.
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
