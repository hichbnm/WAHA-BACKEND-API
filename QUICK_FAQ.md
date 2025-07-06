# Project Quick FAQ

**Swagger Link:**
- After starting the FastAPI server, open: `http://<your-server>:5000/docs`

**Main Server Parameters:**
- Configured in `.env` and `app/config.py` (e.g., `MESSAGE_DELAY`, `SENDER_SWITCH_DELAY`, `LOG_LEVEL`, `LOG_FILE`).
- Number of queue workers (threads): set in `app/main.py` (`num_workers=4` by default, change in `init_services`).

**How to Run an Example:**
- Start the server (`uvicorn app.main:app --host 0.0.0.0 --port 5000`).
- Use `/api/send` (see Swagger) to queue campaigns/messages. You can queue multiple mailings by calling this endpoint several times with different data.

**Waha 14-Day Inactivity:**
- If a user hasn't sent campaigns for 14 days, the session may expire. The backend exposes `last_active`, `expires_at`, and `requires_auth` in the session status endpoint for the frontend to warn users and prompt re-authentication.

**Sending Timeouts for New Users:**
- Message and sender switch delays are set in `.env` (`MESSAGE_DELAY`, `SENDER_SWITCH_DELAY`). Defaults can be changed at runtime via admin endpoints.

**Logging:**
- Logs are written to the file specified in `.env` (e.g., `LOG_FILE=./logs/whatsapp_backend.log`). Log level is set by `LOG_LEVEL`.
- All errors and key events are logged automatically.

---
For more, see `DEPLOYMENT.md` and `/app/docs/API_DOCS.md`.
