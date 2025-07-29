# WhatsApp Bulk Messaging Backend: Requirements & Logic Overview

## 1. Overview
This backend provides a robust, scalable API for sending bulk WhatsApp messages using the WAHA API, with per-sender campaign serialization, strict daily message limits, and configurable per-user message delays. It is built with FastAPI, Celery, SQLAlchemy (async), and PostgreSQL.

---

## 2. Core Features & Logic

### 2.1 Campaign Processing
- **One campaign at a time per sender:**
  - Only one campaign can be in progress or pending for a given sender.
  - New campaigns for a sender are return error until the previous one completes.
- **Distributed processing:**
  - Campaigns are processed by Celery workers, allowing horizontal scaling.
  - Database-level locking ensures no race conditions or duplicate processing.

### 2.2 Message Sending & Rate Limiting
- **Per-user message delay:**
  - Each sender can have a custom message delay (in seconds) set via the `/user-delays` endpoint.
  - For each message, the backend picks a random delay in the range `[delay-1, delay+1]` seconds (minimum 1s).
  - The actual delay used is logged for audit/debugging.
- **No delay logic in the frontend:**
  - The frontend does not control or send the delay value with each message; the backend enforces it.

### 2.3 Daily Message Limits
- **Configurable daily limits:**
  - Limits are set in `.env` for new and existing users (e.g., `NEW_USER_DAILY_LIMIT`, `EXISTING_USER_DAILY_LIMIT`).
  - The backend enforces a rolling 24-hour window for message counts per sender.
  - If a sender exceeds their daily limit, new campaigns are rejected with a clear error response.

### 2.4 Session Management
- **Session lifecycle:**
  - Sessions are created, monitored, and expired automatically.
  - QR code authentication is supported for WhatsApp Web.
  - Session state and expiry are available via API endpoints.

### 2.5 Admin Controls
- **Admin endpoints:**
  - Set global or per-user message delays.
  - View system metrics, all campaigns, users, and sessions.
  - All admin endpoints require an API key in the `X-Admin-Token` header.

---

## 3. API Endpoints (Summary)
- `POST /api/send` — Submit a new campaign (user or admin)
- `GET /api/status/{campaign_id}` — Get campaign status
- `GET /api/campaigns` — List campaigns for sender or all (admin)
- `GET/POST /api/user-delays` — Get/set per-user message delay
- `GET/POST /api/admin/delays` — Get/set global message delay (admin)
- `GET /api/admin/metrics` — System stats (admin)
- `GET /api/admin/users` — List all senders (admin)
- `GET /api/admin/campaigns` — List all campaigns (admin)
- `GET /api/admin/sessions` — List all sessions (admin)
- `POST /api/sessions` — Create/start a session
- `GET /api/sessions/{phone_number}/qr` — Get QR code for WhatsApp Web
- `GET /api/sessions/{phone_number}` — Get session status

---

## 4. Configuration
- `.env` file contains all runtime settings:
  - Database URL, pool sizes
  - Daily message limits
  - Logging config
  - Message delay defaults
  - Admin API key

---

## 5. Logging & Debugging
- All key actions (campaign start, message send, rate limiting, errors) are logged to `logs/whatsapp_backend.log`.
- Rate limiting logs include sender, base delay, chosen delay, and actual sleep time.

---

## 6. Security
- Only admin endpoints require authentication (API key).
- User endpoints are open but scoped by sender number.

---

## 7. Extensibility
- The backend is designed for easy scaling (add more Celery workers).
- All delays and limits are configurable at runtime via API or `.env`.
- The logic is modular for future enhancements (e.g., per-recipient delays, advanced scheduling).

---

## 8. Not Included
- No frontend delay logic: all timing is enforced server-side.
- No direct WhatsApp API integration: all messaging is via WAHA API.

---

## 9. Contact
For support, see the project README or contact the backend maintainer.
