# Usage Meter

> **Public demo scope:** This repository is a curated public demo of the privately maintained Usage Meter project. It includes selected source code and documentation to showcase the architecture and engineering approach. Private configuration, usage and session records, operational details, and internal development materials are intentionally excluded. This repository is intended for portfolio review and demonstration, and is not a complete distribution of the private project.

A Python utility for understanding Codex allowance and session usage. It combines
a compact Windows desktop widget with a PostgreSQL-backed importer for local
session telemetry.

## What it does

- Shows reported allowance remaining, reset timing, and a refresh countdown in an
  always-on-top Windows widget.
- Keeps observed allowance history in PostgreSQL, with a durable local outbox
  when the database is unavailable.
- Imports selected token-usage records from local Codex session files, with
  response deduplication and incremental checkpoints.
- Provides SQL queries for daily usage, task usage, and allowance history.
- Checks the configured database identity before returning a database connection.

The desktop collector only requests account status through the installed Codex
app server. It does not start model turns or redeem reset credits. The importer
reads local session files and does not make model requests.

This is an independent project and is not an official OpenAI product.

## Components and platform support

| Component | Purpose | Platform |
| --- | --- | --- |
| `usage_meter.py` | Tkinter allowance widget and account polling | Windows |
| `import_sessions.py` | Incremental JSONL telemetry import | Headless Python; Windows and Linux launchers |
| `pg_storage.py` | Verified PostgreSQL connections, cache, and retry outbox | Python |
| `db_admin.py` | Schema setup, read-only queries, and optional legacy SQLite import | Python and PostgreSQL client |
| `sql/` | Additive schemas and analysis queries | PostgreSQL |

The Windows widget uses Windows process and desktop APIs. Linux deployment of the
headless importer has not yet been validated end to end. There is no macOS
desktop implementation.

## Requirements

- Python 3.13 for the documented setup, including Tkinter for the Windows widget.
- PostgreSQL and a dedicated database named `usage_meter`.
- A PostgreSQL role that owns that database or has the permissions needed to
  create and use the tables.
- The PostgreSQL `psql` client for `db_admin.py sql` and `db_admin.py query`.
- For the widget: an installed, signed-in Codex CLI whose app server supports
  `account/rateLimits/read` and `account/usage/read`.
- For imports: local Codex session JSONL files containing the telemetry event
  types handled by the importer.

Account endpoints and session event formats can differ between Codex versions.
If the installed version does not expose the expected data, those features will
be unavailable; the tool does not manufacture missing usage values.

## Windows setup

Run these commands from the repository directory in PowerShell.

### 1. Create a Python environment

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item db_config.example.json db_config.json
```

### 2. Configure your database

Provision the dedicated `usage_meter` database and role on your own PostgreSQL
server. For a local installation, an administrator can use:

```powershell
createuser --host=127.0.0.1 --port=5432 --username=postgres --pwprompt usage_meter
createdb --host=127.0.0.1 --port=5432 --username=postgres --owner=usage_meter usage_meter
```

Edit the ignored `db_config.json` for your environment:

```json
{
  "host": "127.0.0.1",
  "port": 5432,
  "database": "usage_meter",
  "user": "usage_meter",
  "expected_server_address": "127.0.0.1",
  "expected_server_port": 5432,
  "sslmode": "prefer"
}
```

`host` and `port` select the connection endpoint. The two `expected_server_*`
values must match the address and port reported by PostgreSQL itself. They may
differ from the client endpoint when using a proxy, container, or port mapping.
Set them deliberately; a mismatch closes the connection before application
queries or writes are allowed.

The database name is intentionally fixed to `usage_meter`. Both Python
connection checks and the SQL schema scripts reject other database names.
The SQL-file helper also verifies the configured identity on the same
`psql` connection that executes the requested file.

The example is for a local server. For a remote deployment, select the TLS
settings required by your PostgreSQL service, such as `sslmode: "verify-full"`
and an optional `sslrootcert` certificate path. These settings are applied to
both the Python connection and the `psql` helper.

Set the database password in the process environment. This PowerShell prompt
keeps the password out of the command text:

```powershell
$dbSecret = Read-Host "PostgreSQL password" -AsSecureString
$env:USAGE_METER_DB_PASSWORD = [System.Net.NetworkCredential]::new("", $dbSecret).Password
Remove-Variable dbSecret
```

The tool does not read a shared application credential file, does not accept a
password in `db_config.json`, and does not automatically load `.env` files.

### 3. Create the base schema

With the password environment variable set in the same terminal:

```powershell
.\.venv\Scripts\python.exe db_admin.py sql sql\001_schema.sql
```

The session importer applies its additional telemetry schema when it runs.
Schema commands are additive and do not drop existing tables or databases.

### 4. Start the widget

If automatic executable discovery does not locate your Codex installation, set
`USAGE_METER_CODEX_EXE` to the full path of your `codex.exe`.

```powershell
.\.venv\Scripts\python.exe usage_meter.py
```

Drag the compact display to position it. Use the expand control for the budget
view and Details, or right-click to exit. The last position is stored locally.

`launch.vbs` prefers the project's `.venv\Scripts\pythonw.exe`, then the
standard per-user Python 3.13 installation. It inherits its parent's environment,
so the password variable must be present in the process launching it. Launching
it from the configured PowerShell terminal is one option:

```powershell
cscript //nologo .\launch.vbs
```

No startup task or automatic schedule is installed.

## Import session telemetry

The default source directory is the current user's `.codex` directory.
Both `sessions` and `archived_sessions` are scanned. Specify `--root` to use a
different local directory.

```powershell
.\import_sessions.bat --from 2026-01-01 --to 2026-01-07
.\database.bat query sql\session_analysis.sql
```

The date range is inclusive and interpreted in `America/New_York`. Source files
are not changed. Re-running a range does not duplicate imported response records.

### Linux headless setup

The following commands are provided for the headless importer; a complete Linux
deployment has not been validated.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp db_config.example.json db_config.json
# Edit db_config.json for your own database before continuing.
read -r -s -p "PostgreSQL password: " USAGE_METER_DB_PASSWORD
export USAGE_METER_DB_PASSWORD
bash database.sh sql sql/001_schema.sql
bash import_sessions.sh --from 2026-01-01 --to 2026-01-07
bash database.sh query sql/session_analysis.sql
```

The Bash and batch launchers use the project virtual environment when available.
`USAGE_METER_PYTHON` can select another Python executable.
`USAGE_METER_PSQL` can select the `psql` executable; otherwise it is found on
`PATH`, with a standard Windows PostgreSQL 18 installation as a fallback.

## How the data is handled

### Collection and outages

Allowance is polled every 60 seconds. Account usage is requested every 15 minutes
and when the reported allowance window changes. Account-level readings may
include activity from other machines; the session importer only sees local files.

PostgreSQL is the authoritative store. Pending writes are saved under
`data/postgres_outbox/` and retried using stable source keys. Files are removed
from the outbox after the transaction is acknowledged. A local cache supports
displaying the last readings during an outage; the UI flags storage problems.

### Allowance budget

The budget view uses a 14-percentage-point daily allocation with carryover within
the same reset window. It starts at the first observation, so the first partial
day does not include earlier usage. Reset-time changes larger than 60 seconds
start a new baseline. Small timestamp jitter preserves the existing budget.

This is an observational budget, not a spending cap. Delayed reporting,
simultaneous activity, and offline gaps prevent exact per-task attribution.
Decreasing usage within a window is flagged as an uncertain adjustment.

### Token totals and completeness

Only per-response `token_usage_record.usage` values contribute to imported token
totals. Response IDs deduplicate repeated records. Cached input is part of input,
and reasoning output is part of output; neither is added twice.

Cumulative `token_count` totals are excluded from token totals. Missing response
IDs are counted as coverage gaps. Missing model, effort, and service-tier values
remain null. The importer does not fetch another machine's session files or
claim account-wide completeness.

Checkpoints and imported rows commit together for each file and range.
Incomplete trailing lines wait for a later run. Malformed complete telemetry
lines and truncated files stop the import rather than silently advancing its
checkpoint.

### Local data stays local to your setup

The session importer stores selected metadata and usage numbers, including
session IDs, hostnames, source-file paths, and working-directory metadata. It
does not store prompt bodies, reasoning text, or tool arguments. The account
collector stores the account-response payloads returned by the installed CLI.

Treat your database and generated `data/` directory as private. The repository
contains source code, schemas, and example configuration, not session histories
or database exports. Local configuration, authentication files, session JSONL,
logs, caches, and backups are excluded by `.gitignore`.

## Useful commands

```powershell
# Query allowance and sample history in a read-only transaction.
.\database.bat query sql\queries.sql

# Optional account probe: makes read-only account requests, not model calls.
.\.venv\Scripts\python.exe usage_meter.py --probe

# Run local tests with mocked database connections and synthetic session data.
.\.venv\Scripts\python.exe -m unittest discover -v
```

For an existing legacy `data/usage.sqlite3` archive only,
`database.bat migrate` creates a timestamped SQLite backup, imports rows with
stable keys, and verifies them before commit. Stop the widget first. A fresh
installation does not need this migration.

`verify_session_import.py` is an optional operational verifier that connects to
the configured database and compares imported rows with their original local
JSONL offsets. It is not part of the offline unit-test suite.

## Project scope

The project focuses on observable allowance, reliable local imports, and
transparent reporting. It does not provide billing reconciliation, enforce
account limits, or guarantee compatibility with every Codex version.

