# EventSphere

Event booking, private venue hire, and QR check-in backed by five agents communicating through a JSON message bus.

- [Live site](https://eventsphere-hadhi.duckdns.org/)
- [API documentation](https://eventsphere-hadhi.duckdns.org/docs)

## Run locally

Install Python 3.11+ and Node.js 22.12+ with npm. From the repository root in Windows CMD:

```cmd
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
cd frontend
npm ci
npm run build
cd ..
set "DATABASE_URL="
.venv\Scripts\python.exe -m uvicorn main:app --reload
```

Open [the app](http://localhost:8000/) or [Swagger](http://localhost:8000/docs). On Linux, use `.venv/bin/python` and `unset DATABASE_URL`. FastAPI creates the local SQLite database at startup. For live frontend editing, keep FastAPI running and run `npm run dev` from `frontend/` in a second terminal.

## Demo accounts

| Role | Username | What to demonstrate |
|---|---|---|
| Admin | `organiser` | Create venues and drafts, publish events, review hires, and inspect agent traces |
| Staff | `gate` | Scan a QR ticket or enter its code manually |
| Attendee | `hadhi` | Book tickets, join the waitlist, request private hire, and receive notifications |

The listed usernames come from the demo dataset. Provide demo passwords privately with the submission. For a fresh local database, stop the app and create admin and staff accounts with these commands, then restart and register the attendee through the website:

```cmd
.venv\Scripts\python.exe create_account.py admin organiser
.venv\Scripts\python.exe create_account.py staff gate
```

Account passwords must have at least 12 characters.

## Environment variables

Set these in the environment of the FastAPI process. On EC2, systemd reads `~/eventsphere.env`. Keep credentials out of Git.

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | PostgreSQL connection URL in production. When unset, the app uses local `eventsphere.db`. |
| `SMTP_HOST` | SMTP server hostname. Required for email delivery. |
| `SMTP_FROM` | Sender address. Required for email delivery. |
| `SMTP_PORT` | SMTP port. Defaults to 587 for STARTTLS or 465 for implicit TLS. |
| `SMTP_SSL` | Set to `true` for implicit TLS. Otherwise the app uses STARTTLS. |
| `SMTP_USER` | SMTP username, if the provider requires authentication. |
| `SMTP_PASSWORD` | SMTP password or app password, if authentication is required. |

Attendees opt in to email under **Notifications**. Booking confirmation emails include one PNG QR attachment per ticket.

## Run tests

From the repository root:

```cmd
.venv\Scripts\python.exe -m pip install pytest
.venv\Scripts\python.exe -m pytest -v tests\test_agents.py
```

The 20 cases use temporary SQLite databases and cover all five agents, including concurrent booking of the last seat and double check-in. The suite does not send live email. Use the same commands with `.venv/bin/python` on Linux.
