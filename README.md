# career-agent

## Objectif

`career-agent` est un agent de carrière personnel, d'abord utilisé par son fondateur pour
automatiser sa recherche d'emploi / d'alternance. À terme : comprendre le profil d'un
candidat, rechercher et qualifier des offres, adapter les candidatures, identifier des
contacts, envoyer des emails personnalisés, suivre les résultats et apprendre de l'historique.

**État actuel : fondation technique uniquement.** Aucun agent métier, scraping, automatisation
navigateur, candidature ou envoi d'email n'est encore implémenté.

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
├── api/        routes HTTP (aucune logique métier)
├── core/       configuration centralisée, moteur/session SQLAlchemy
├── models/     modèles SQLAlchemy (Base uniquement pour l'instant)
├── schemas/    schémas Pydantic
├── services/   logique métier
├── agents/     futurs agents (vide)
└── main.py     application FastAPI
data/private/   données personnelles (ignorées par Git)
migrations/     environnement Alembic
tests/  docs/  scripts/
```

Détails : [docs/architecture.md](docs/architecture.md).

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

(Aucune migration n'existe encore : le modèle métier n'est pas défini.)

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

Les tests ne nécessitent ni base de données ni fichier `.env`.

## Politique sur les données privées

- Toutes les données personnelles du candidat (profil, CV, documents, portfolio,
  candidatures) vivent uniquement dans `data/private/`.
- `data/private/` et `.env` sont ignorés par Git ; `.env.example` ne contient que des noms de
  variables, sans secret.
- Aucune clé API, token, mot de passe ou donnée personnelle ne doit être écrit dans le code ni
  commité. Toute configuration passe par les variables d'environnement.
- Aucune donnée fictive ressemblant à de vraies données de candidat n'est ajoutée au dépôt.
