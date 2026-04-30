# OpenLeaseOps

[中文](README.md) | [English](README.en.md)

OpenLeaseOps is a lightweight lease contract management system for office buildings, business parks, and commercial real-estate operations. The backend uses `FastAPI + SQLite + Alembic`, the frontend uses `Vue 3 + Vite`, and the default deployment model is a single Docker Compose service.

The open-source package does not include any company name, building, floor, room, contract, user, PDF, or demo business data. On first launch, the system asks you to create the first administrator account.

## Features

- Contract ledger: contract CRUD, pagination, sorting, filters, Excel import/export, and PDF attachment management.
- Room ledger: room CRUD, office/commercial usage management, building, zone, and floor-level views.
- Operations dashboard: occupancy rate, leased area, monthly rent, expiry alerts, status distribution, and area performance.
- Monthly receivables: monthly rent, property management fee, and total amount reporting.
- System tools: batch PDF upload and matching, integrity checks, data backup and restore, user management, and activity logs.
- Access control: administrator, editor, and read-only roles.
- Encrypted storage: optional encryption for PDF files and backup archives.
- Integration layer: extension points for JDE, OA, finance systems, SaaS, REST Webhook, and MCP.

## Quick Start

### 1. Prepare Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

Generate `JWT_SECRET`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Write the generated value into `.env`:

```env
JWT_SECRET=replace-with-your-generated-random-string
```

If you want to encrypt PDFs and backups, generate `DATA_ENCRYPTION_KEY`:

```bash
python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Then enable encryption in `.env`:

```env
DATA_ENCRYPTION_ENABLED=true
DATA_ENCRYPTION_KEY=replace-with-your-32-byte-base64-key
```

### 2. Deploy With Docker

```bash
docker compose up -d --build
```

Default URL:

```text
http://127.0.0.1:8080
```

If no user exists, the first launch will show the administrator initialization flow. It only asks for a login account and password.

## Customization

All common deployment settings are maintained in `.env`. After changing `.env`, rebuild and restart the service:

```bash
docker compose up -d --build
```

### Basic Deployment

| Variable | Default | Purpose | How to customize |
| --- | --- | --- | --- |
| `APP_PORT` | `8080` | Host port exposed by Docker | Change to an available port, for example `APP_PORT=18080` |
| `CONTAINER_NAME` | `lease-manager` | Docker container name | Change it when running multiple instances on the same server |
| `APP_TIMEZONE` | `Asia/Shanghai` | Application timezone | Use another IANA timezone for non-China deployments |
| `DATA_DIR` | `./data` | Local data directory | Usually keep the default; Docker maps it to `/data` |
| `APP_VERSION` | `local` | Version label | Use a git commit, release number, or internal build number |

### Branding And Interface

These settings are applied at frontend build time, so you need to rebuild after changing them.

| Variable | Default | Purpose | How to customize |
| --- | --- | --- | --- |
| `APP_TITLE` | `OpenLeaseOps` | Product name shown on the login page, sidebar, and page headers | Change it to your product or internal system name |
| `APP_SUBTITLE` | `租赁运营后台` | Product subtitle | Use a short internal description |
| `APP_MARK` | `LM` | Text mark in the sidebar | Use your company abbreviation or project code |

Example:

```env
APP_TITLE=Enterprise Lease Operations Platform
APP_SUBTITLE=Asset Operations And Contract Management
APP_MARK=LEASE
```

### Contract Numbering

| Variable | Default | Purpose | How to customize |
| --- | --- | --- | --- |
| `CONTRACT_NO_PREFIX` | `LEASE` | Prefix for generated contract numbers | Use your company or project abbreviation, such as `OFFICE` or `PARK` |

The system does not hard-code building, floor, or room-numbering rules. Buildings, zones, floors, and room types are maintained through the UI or Excel import.

### Login And Security

| Variable | Default | Purpose | How to customize |
| --- | --- | --- | --- |
| `JWT_SECRET` | empty | Signing key for login tokens | Required. Use a long random string |
| `TOKEN_EXPIRE_HOURS` | `24` | Login session duration in hours | Shorter values are recommended for public deployments |
| `COOKIE_SECURE` | `false` | Send cookies only over HTTPS | Set to `true` when deploying behind HTTPS |

### Data Encryption

| Variable | Default | Purpose | How to customize |
| --- | --- | --- | --- |
| `DATA_ENCRYPTION_ENABLED` | `false` | Encrypt PDF files and backup archives | Recommended for production |
| `DATA_ENCRYPTION_KEY` | empty | Encryption key for PDFs and backups | Required when encryption is enabled; store it permanently and securely |

When encryption is enabled, newly uploaded PDFs are stored as encrypted files and new backups are generated as `.zip.enc` archives. Browser PDF download and backup restore flows remain unchanged.

If the encryption key is lost, encrypted PDFs and encrypted backups cannot be recovered. See [docs/ENCRYPTION.md](docs/ENCRYPTION.md) for details.

## Data Directory

Runtime data is stored in `data/` by default:

```text
data/
├── lease.db
├── lease.db-wal
├── lease.db-shm
├── pdfs/
└── backups/
```

The repository only keeps empty directory placeholders. Do not commit `.env`, `data/lease.db`, PDFs, backup archives, or real contract files before publishing.

## Roles

| Role | Permissions |
| --- | --- |
| `admin` | User management, backup and restore, system checks, activity logs, integration settings, and all business operations |
| `editor` | Contract, room, PDF, import/export, and other business operations |
| `viewer` | Read-only access to dashboard, contracts, rooms, and contract PDFs |

## Integrations

Administrators can configure connectors on the Integrations page. `REST Webhook` can be used now to push contract, room, PDF, and other key events. JDE, OA, finance systems, SaaS, MCP Server, MCP Client, and custom connectors are reserved extension points.

Connector secrets are never returned in plaintext. To save API keys, bearer tokens, or similar secrets, `DATA_ENCRYPTION_ENABLED=true` must be enabled first. See [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) for details.

## Local Development

Install backend dependencies:

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

Install frontend dependencies and build:

```bash
cd frontend
npm install
npm run build
```

Run migrations and start the backend:

```bash
python -m alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Run tests:

```bash
python -m pytest -q
```

Frontend E2E tests are disabled by default to avoid accidentally connecting to a production environment. Enable them explicitly when needed:

```bash
python -m playwright install chromium
LEASE_E2E=1 python -m pytest tests/e2e -q
```

Windows PowerShell:

```powershell
$env:LEASE_E2E="1"
python -m pytest tests/e2e -q
```

## Open-Source Edition Scope

- No tenant, contract, room, building, or floor demo data is included by default.
- SQLite is the default database and is suitable for single-project, small-team, lightweight deployments.
- PDF files and backups can be encrypted. The running SQLite database itself remains plaintext. If transparent database encryption is required, use disk encryption at the deployment layer or replace the database solution.
- Real JDE, OA, finance-system, SaaS, and MCP protocols must be adapted for each organization. This project provides the extension framework only.

## License

This project is licensed under the [MIT License](LICENSE).
