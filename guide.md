# WhatsApp Bulk Messaging API Guide

## Authentication

- **Admin endpoints:** Require `X-Admin-API-Key` header with your admin key.
- **User endpoints:** No authentication, but must provide `sender_number`.

---

## Messaging Endpoints

### `POST /api/send`
- **Purpose:** Submit a new campaign (send messages).
- **Body Example:**
  ```json
  {
    "sender_number": "42108077",
    "recipients": ["21642108101"],
    "template": "Hello, this is a test message!",
    "media_url": null
  }
  ```
- **Test:** Use curl or Postman. No auth needed for users.

### `GET /api/status/{campaign_id}`
- **Purpose:** Get campaign status.
- **Test:** Replace `{campaign_id}` with your campaign’s ID.

### `GET /api/campaigns`
- **Purpose:** List campaigns for the sender (or all, if admin).
- **Test:** For admin, add `X-Admin-API-Key` header.

---

## Session Management

### `POST /api/sessions`
- **Purpose:** Start a new WhatsApp session.
- **Body:** `{ "phone_number": "<number>" }`

### `GET /api/sessions/{phone_number}/qr`
- **Purpose:** Get QR code for WhatsApp Web login.

### `GET /api/sessions/{phone_number}`
- **Purpose:** Get session status.

### `DELETE /api/sessions/{sender_number}`
- **Purpose:** Remove a session (admin only).
- **Test:** Add `X-Admin-API-Key` header.

---

## Rate Limiting (Delays)

### `GET /api/admin/delays`
- **Purpose:** Get global message/sender switch delays.
- **Test:** Add `X-Admin-API-Key` header.

### `POST /api/admin/delays`
- **Purpose:** Set global delays.
- **Body:** `{ "MESSAGE_DELAY": 2, "SENDER_SWITCH_DELAY": 5 }`

### `GET /api/admin/user-delays?sender_number=...`
- **Purpose:** Get per-user delays.

### `POST /api/admin/user-delays?sender_number=...&message_delay=3&sender_switch_delay=7`
- **Purpose:** Set per-user delays.

---

## Worker Management (Admin Only)

All require `X-Admin-API-Key` header.

- `POST /api/admin/workers` — Register worker.
- `GET /api/admin/workers` — List workers.
- `PATCH /api/admin/workers/{worker_id}` — Update worker.
- `DELETE /api/admin/workers/{worker_id}` — Delete worker.
- `DELETE /api/admin/workers/cleanup` — Remove all workers and sessions.

---

## Admin Monitoring

All require `X-Admin-API-Key` header.

- `GET /api/admin/metrics` — System stats.
- `GET /api/admin/users` — List all sender numbers.
- `GET /api/admin/campaigns` — List all campaigns.
- `GET /api/admin/sessions` — List all active sessions.
- `GET /api/admin/session-numbers` — List all session phone numbers.

---

## Testing Tips

- Use Swagger UI (`/docs`) for interactive testing.
- Use curl/Postman for custom requests.
- For admin endpoints, always set the `X-Admin-API-Key` header.
- For per-user delays, set via `/api/admin/user-delays` and verify with `/api/admin/user-delays?sender_number=...`.

---

## Testing manually 

1/make 3 waha workers similar  as docker-compose.yml
2/use first port for dashboad and add all workers to it
3/add the workers credentials registre worker on the endpoint `/api/admin/workers`/
4/limite active session to 1 per worker and 1 max live session on .env
5/now create a session using the endpoint `/api/sessions` with a phone number
6/scan the QR code from the endpoint `/api/sessions/{phone_number}/qr`
7/after scanning, check the session status with `/api/sessions/{phone_number}`
8/ when the session is active , create a new session using the endpoint `/api/sessions` with a different phone number
9/scan the QR code for the new session
10/ check the status of both sessions using `/api/sessions/{phone_number}` for each
11/ check the dashboard u will see the 2 sessions active on 2 different workers
12/ now create a campaign using the endpoint `/api/send` with the first session's phone number
13/ check the status of the campaign using `/api/status/{campaign_id}`
14/ now u can add any number of workers u want even 1 milion , that depending on your server capacity and the number of active sessions you want to handle





Let me know if you want a more detailed example for any specific endpoint or a ready-to-use curl command!
