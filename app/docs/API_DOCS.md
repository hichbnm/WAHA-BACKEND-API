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
**Exemple:** : 
{
  "sender_number": "2",
  "recipients": ["21642108101","21642108101","21642108101","21642108101"],
  "template": "Hello, this is a test message from FastAPI backend after the fix!",
  "media_url": "files/2025/08/10/19/a913aa3535b0eed721140b78472a3b52b8236f79.png"
}
- **Access:** User or Admin

### `GET /api/metrics`
**Description:** Get user-level metrics for a specific sender.
- **Query:** `sender_number` (string, required)
- **Returns:**
```
{
  "active_sessions": 1,
  "messages_sent_today": 42,
  "current_queue_size": 3,
  "server_uptime": 12345.67,
  "total_campaigns": 10,
  "total_users": 1,
  "total_sessions": null
}
```
- **Access:** User or Admin

### `POST /api/upload`
**Description:** Upload a media file (image/video/file) to local storage and get a path to use in `media_url`.
- **Input:** multipart/form-data with field `file`
- **Returns:** `{ "path": "files/YYYY/MM/DD/HH/<sha1>.<ext>" }`
- **Notes:**
  - Enforced size limit via `MAX_UPLOAD_MB` (default 15MB)
  - Files are deleted automatically when the campaign completes
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
- **Query (optional):** `sender_number`, `status`, `start_date`, `end_date`
- **Returns:** List of campaigns with summary info
- **Example:**
```
GET /api/campaigns?sender_number=21642108101&status=COMPLETED
[
  {
    "id": 75,
    "sender_number": "21642108101",
    "status": "COMPLETED",
    "total_messages": 10,
    "sent_messages": 10,
    "failed_messages": 0,
    "created_at": "2025-08-10T17:00:00Z"
  }
]
```
- **Access:** User (only their campaigns) or Admin (all)

### `POST /api/cancel/{campaign_id}`
**Description:** Cancel a PENDING/IN_PROGRESS campaign. Remaining PENDING/IN_PROGRESS messages are marked CANCELLED and dispatch stops.
- **Returns:** Updated campaign status.
- **Access:** User (their campaign) or Admin (any)

### `GET /api/campaigns/{campaign_id}/failed`
**Description:** List the actual recipients that failed in a campaign, with error message if available.
- **Returns:**
```
{
  "campaign_id": 123,
  "count": 2,
  "recipients": [
    { "recipient": "21650000001", "error": "WAHA 500: unsupported mime" },
    { "recipient": "21650000002", "error": "Recipient blocked" }
  ]
}
```
- **Access:** User (their campaign) or Admin

---

## Session Management Endpoints

### `GET /api/sessions`
**Description:** List WAHA sessions. Admins see all; non-admins get an empty list (privacy).
- **Returns:**
```
[
  {
    "phone_number": "21642108101",
    "status": "CONNECTED",
    "message": "OK",
    "last_active": null,
    "data": { /* raw WAHA session data */ }
  }
]
```
- **Access:** Admin (non-admin: [])

### `GET /api/sessions/{phone_number}`
**Description:** Get current session status for a phone number.
- **Returns:**
```
{
  "status": "CONNECTED",
  "message": "Session active",
  "phone_number": "21642108101",
  "last_active": "2025-08-10T17:00:00Z",
  "expires_at": null,
  "requires_auth": false,
  "data": { }
}
```
- **Access:** User or Admin

### `POST /api/sessions/{phone_number}/start`
**Description:** Start a session (allocates a worker and starts WAHA for this number).
- **Returns:** `SessionInfo`
- **Access:** User or Admin

### `POST /api/sessions/{phone_number}/stop`
**Description:** Stop a session.
- **Returns:** `SessionInfo`
- **Access:** User or Admin

### `POST /api/sessions/{phone_number}/logout`
**Description:** Log out WhatsApp Web for this number.
- **Returns:** `SessionInfo`
- **Access:** User or Admin

### `DELETE /api/sessions/{phone_number}`
**Description:** Delete session (admin only).
- **Returns:** `SessionInfo`
- **Access:** Admin

### `GET /api/sessions/{phone_number}/qr`
**Description:** Get a QR code for authentication.
- **Returns:**
```
{
  "status": "WAITING_FOR_SCAN",
  "message": "Scan the QR code",
  "qr_code": "<base64>",
  "expires_at": "2025-08-10T18:00:00Z"
}
```
- **Access:** User or Admin

### `GET /api/sessions/{phone_number}/me`
**Description:** Get account info for the connected number.
- **Returns:** `MeInfo`
- **Access:** User or Admin

---

## Internal Monitoring & Admin Endpoints

### `GET /api/admin/metrics`
**Description:** Get system-wide stats (active sessions, messages sent today, queue size, uptime, etc.).
- **Access:** Admin only
**Example:**
```
{
  "active_sessions": 5,
  "messages_sent_today": 120,
  "current_queue_size": 4,
  "server_uptime": 54321.0,
  "total_campaigns": 50,
  "total_users": 7,
  "total_sessions": 9
}
```

### `GET /api/admin/users`
**Description:** List all registered sender numbers in the system.
- **Access:** Admin only
**Example:**
```
[
  {
    "phone_number": "21642108101",
    "total_campaigns": 12,
    "total_messages": 300,
    "sent_messages": 280,
    "failed_messages": 20,
    "active_session": true,
    "last_active": "2025-08-10T17:55:00Z",
    "last_campaign": "2025-08-10T16:00:00Z"
  }
]
```

### `GET /api/admin/campaigns`
**Description:** List all campaigns (admin view).
- **Access:** Admin only
**Example:**
```
{
  "campaign_status": { "PENDING": 3, "IN_PROGRESS": 1, "COMPLETED": 20 },
  "message_status": { "PENDING": 10, "SENT": 200, "FAILED": 5 },
  "recent_campaigns": [
    {
      "id": 75,
      "sender_number": "21642108101",
      "status": "COMPLETED",
      "total_messages": 10,
      "sent_messages": 10,
      "failed_messages": 0,
      "created_at": "2025-08-10T17:00:00Z"
    }
  ],
  "last_refresh": "2025-08-10T18:00:00Z"
}
```

### `GET /api/admin/sessions`
**Description:** List all active WhatsApp sessions (admin view).
- **Access:** Admin only
**Example:**
```
[
  { "status": "CONNECTED", "last_active": "2025-08-10T17:58:00Z", "requires_auth": false },
  { "status": "WORKING", "last_active": "2025-08-10T17:57:00Z", "requires_auth": false }
]
```

### `GET /api/admin/session-numbers`
**Description:** List all session phone numbers (admin only).
- **Example:** `{ "count": 2, "numbers": ["21642108101", "216..."] }`

### `POST /api/admin/delays`
**Description:** Set message and sender switch delays (rate limiting config).
- **Input:** `{ "MESSAGE_DELAY": int, "SENDER_SWITCH_DELAY": int }`
- **Access:** Admin only
**Example:** `{ "MESSAGE_DELAY": 2 }`

### `GET /api/admin/delays`
**Description:** Get current message and sender switch delays.
- **Access:** Admin only
**Example:** `{ "MESSAGE_DELAY": 2 }`

### `GET /api/admin/user-delays?sender_number=...`
**Description:** Get per-user message delay.
- **Returns:** `{ "MESSAGE_DELAY": 2 }`
- **Access:** Admin

### `POST /api/admin/user-delays?sender_number=...&message_delay=...`
**Description:** Set per-user message delay.
- **Returns:** `{ "MESSAGE_DELAY": 5 }`
- **Access:** Admin

### Worker Management (Admin)

#### `POST /api/admin/workers`
Add a worker.
- Input:
```
{ "url": "http://host:3002", "api_key": "...", "capacity": 10, "name": "w1" }
```
- Returns: worker object

#### `GET /api/admin/workers`
List workers.
- Returns: `[ { id, url, api_key, capacity, name } ]`

#### `PATCH /api/admin/workers/{worker_id}`
Update a worker.
- Input: partial `WorkerUpdate` fields
- Returns: updated worker

#### `DELETE /api/admin/workers/{worker_id}`
Delete a worker (optional `force` to remove sessions).
- Returns: 204

#### `DELETE /api/admin/workers/cleanup`
Remove all workers and sessions.
- Returns: 204

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

## Frontend Quickstart (Step-by-step)

1) Upload media (optional)

- Endpoint: `POST /api/upload`
- Request: multipart/form-data with `file`
- Response: `{ "path": "files/2025/08/10/14/<sha1>.png" }`

2) Create campaign

- Endpoint: `POST /api/send`
- JSON body:
```
{
  "sender_number": "+216...",
  "recipients": ["+21642108101", "+216..."],
  "template": "Hello from frontend!",
  "media_url": "files/2025/08/10/14/<sha1>.png"  // optional, from step 1
}
```
- Response: `{ "id": 123, "status": "PENDING", "total_messages": 2, "created_at": "..." }`

3) Poll campaign status

- Endpoint: `GET /api/status/{id}` or `GET /api/status/{id}?details=true`
- Response (summary): `{ id, status, total_messages, sent_messages, failed_messages, created_at }`
- Message statuses (details=true): PENDING, IN_PROGRESS, SENT, FAILED, CANCELLED

4) Optional: Cancel a campaign

- Endpoint: `POST /api/cancel/{id}`
- Effect: all remaining messages cancelled; dispatch stops for that campaign

Notes:
- The backend enforces per-sender rate limits. Multiple senders can send in parallel; each sender sends serially.
- Uploaded files are deleted automatically after the campaign completes.
- Size limit for uploads is governed by `MAX_UPLOAD_MB` (default 15MB).

## Webhook Endpoint

### `POST /api/webhook`
Receive events from WAHA (incoming messages, delivery/read acks, connection updates, QR events).
- Request example:
```
{
  "event": "message.ack",
  "payload": { "id": "ABCD1234", "ack": 1 }
}
```
- Response: `{ "status": "success" }` or error with details

For questions or issues, contact the backend maintainer or open an issue in the project repository.
