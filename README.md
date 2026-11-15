# EventSphere

Local API prototype for EventSphere. Use Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe create_account.py admin organiser
.\.venv\Scripts\python.exe create_account.py staff gate
.\.venv\Scripts\python.exe -m uvicorn main:app
```

Open http://127.0.0.1:8000/docs. Register an attendee with POST /auth/register.
Log in with POST /auth/login, copy access_token and paste it into Authorize.
Sign in as the appropriate role before each action: admin creates an event at
POST /events, attendee books at POST /events/{event_id}/book, and staff checks
in at POST /events/{event_id}/scan. Passwords must contain at least 12 characters.
API authentication uses an explicit Authorization header. Cookie-based browser
forms will need CSRF protection when the web interface is added.
SQLite is the local default. Set DATABASE_URL to a PostgreSQL connection URL
to use PostgreSQL. BookingAgent and TicketAgent exchange validated messages and
persist their conversation in the messages table.

## React frontend

Start FastAPI from the project root on port 8000. In another terminal:

```cmd
cd frontend
npm install
npm run dev
```

Open the URL printed by Vite (usually http://localhost:5173). Vite proxies API requests to FastAPI.

For a single-server deployment, run `npm run build` inside frontend before starting Uvicorn.
FastAPI serves the resulting React bundle at /. Before a build is available, / serves the original page.

## SMTP notifications

Attendees can opt in under Notifications by saving an email address. New in-app notifications are also queued for that address. Configure the FastAPI process with `SMTP_HOST`, `SMTP_FROM`, and, if your provider requires authentication, `SMTP_USER` and `SMTP_PASSWORD`. The default uses STARTTLS on port 587. Set `SMTP_SSL=true` for implicit TLS on port 465. Override the port with `SMTP_PORT`. Keep SMTP credentials in environment variables.

The check-in chart uses timestamps from scans recorded after the update.
