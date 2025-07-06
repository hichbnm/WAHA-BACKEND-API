# WhatsApp Bulk Messaging API Documentation

## Overview
This API allows users to send bulk WhatsApp messages via their own numbers using the WAHA API. It supports multi-session management, campaign creation, real-time status tracking, and admin/user separation. The API is designed for integration with a Chrome extension or other frontend clients.

---

## Authentication & Access Model
- **User-level endpoints:** No authentication required. Each request must include the sender's phone number. Data is scoped to that number.
- **Admin-only endpoints:** Require a secure API key in the `X-Admin-Token` header.

---

## Messaging Endpoints

### `POST /api/send`
**Description:** Submit a new campaign to the queue.
- **Input:**
  - `sender_number` (string, required)
  - `template` (string, required)
  - `variables` (object, optional)
  - `recipients` (array of strings, required)
  - `media_url` (string, optional)
- **Returns:** Campaign details and status.
- **Exemple:** : 
{
    "sender_number": "42108077",
    "recipients": ["21642108101"],
    "template": "Hello, this is a test message from FastAPI backend after the fix!",
    "media_url": null
}
- **Access:** User or Admin

### `GET /api/status/{campaign_id}`
**Description:** Get overall status of a campaign.
- **Returns:** Total sent, pending, failed, start/end time, progress state.
- **Access:** User (only their campaigns) or Admin (all)

### `GET /api/status/{campaign_id}?details=true`
**Description:** Get full breakdown of messages in a campaign.
- **Exemple:** Input : The Id returned from /api/send
- **Returns:** Per-recipient status, message text, timestamp, error (if any).
- **Access:** User or Admin

### `GET /api/campaigns`
**Description:** List campaigns for the sender (or all, if admin).
- **Returns:** List of campaigns with summary info with optional filters
- **Access:** User (only their campaigns) or Admin (all)

---

## Session Management Endpoints

### `GET /api/sessions/{sender_number}/status`
**Description:** Get session state for a sender.
- **Returns:** `active in memory`, `saved in DB`, or `not found`.
- **Access:** User or Admin

### `DELETE /api/sessions/{sender_number}`
**Description:** Remove a session for the given number.
- **Returns:** Confirmation and status.
- **Access:** Admin only

### `POST /api/sessions`
**Description:** Create/start a new session for a phone number.
- **Input:** `{ "phone_number": "<number>" }`
- **Returns:** Session status and info.
- **Access:** User or Admin

### `GET /api/sessions/{phone_number}/qr`
**Description:** Get a QR code for WhatsApp Web authentication.
- **Returns:**
  - If connected: `{ "status": "CONNECTED" }`
  - If not: `{ "status": "WAITING_FOR_SCAN", "qr_code": "<base64>", "expires_at": "<datetime>" }`
- **Access:** User or Admin

### `GET /api/sessions/{phone_number}`
**Description:** Get current session status (poll this after requesting QR code).
- **Returns:** Status (`WAITING_FOR_SCAN`, `SCANNED`, `CONNECTED`, `EXPIRED`, etc.) and details.
- **Access:** User or Admin

---

## Internal Monitoring & Admin Endpoints

### `GET /api/admin/metrics`
**Description:** Get system-wide stats (active sessions, messages sent today, queue size, uptime, etc.).
- **Access:** Admin only

### `GET /api/admin/users`
**Description:** List all registered sender numbers in the system.
- **Access:** Admin only

### `GET /api/admin/campaigns`
**Description:** List all campaigns (admin view).
- **Access:** Admin only

### `GET /api/admin/sessions`
**Description:** List all active WhatsApp sessions (admin view).
- **Access:** Admin only

### `POST /api/admin/delays`
**Description:** Set message and sender switch delays (rate limiting config).
- **Input:** `{ "MESSAGE_DELAY": int, "SENDER_SWITCH_DELAY": int }`
- **Access:** Admin only

### `GET /api/admin/delays`
**Description:** Get current message and sender switch delays.
- **Access:** Admin only

---

## ⚠️ Session Expiry & Re-Authentication (Warning)
- WhatsApp will automatically expire sessions that are inactive for 14 days.
- The backend runs a background task to "ping" sessions every 12 days to keep them alive, but if a session is not reachable or the phone is offline, expiry may still occur.
- The session status endpoint (`GET /api/sessions/{phone_number}`) now returns:
  - `last_active`: The last time the session was active (UTC).
  - `expires_at`: The estimated expiry time (14 days after last activity).
  - `requires_auth`: `true` if the session requires re-authentication (e.g., expired or not connected).
- **Frontend should warn users if their session is about to expire or has expired, and prompt for re-authentication (QR code scan).**

---

## Error Handling
- All endpoints return clear error messages and HTTP status codes.
- Common errors: invalid input, unauthorized, session expired, WAHA API errors.

---

## Logging
- Log verbosity and file location are configurable in the backend settings.
- Logs are stored at the path specified in the config (default: `./logs/whatsapp_backend.log`).

---

## Security
- Admin endpoints require the `X-Admin-Token` header with the configured API key.
- User endpoints are open but scoped by sender number.

---

## Contact & Support
For questions or issues, contact the backend maintainer or open an issue in the project repository.
