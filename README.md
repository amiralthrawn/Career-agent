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
uvicorn app.main:app --reload
```

- Healthcheck : <http://127.0.0.1:8000/health>
- Documentation interactive : <http://127.0.0.1:8000/docs>

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

## Politique sur les données privées

- Toutes les données personnelles du candidat (profil, CV, documents, portfolio,
  candidatures) vivent uniquement dans `data/private/`.
- `data/private/` et `.env` sont ignorés par Git ; `.env.example` ne contient que des noms de
  variables, sans secret.
- Aucune clé API, token, mot de passe ou donnée personnelle ne doit être écrit dans le code ni
  commité. Toute configuration passe par les variables d'environnement.
- Aucune donnée fictive ressemblant à de vraies données de candidat n'est ajoutée au dépôt.
