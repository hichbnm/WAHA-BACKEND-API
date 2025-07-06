# WhatsApp Bulk Messaging Backend

A FastAPI-based backend service for sending bulk WhatsApp messages using the WAHA (WhatsApp HTTP API) service.

## Features

- Send bulk WhatsApp messages using WAHA
- Campaign management and tracking
- Message queue with rate limiting
- Session management
- Admin monitoring and metrics
- Support for media messages
- Secure admin access with API key

## Requirements

- Python 3.8+
- WAHA running in Docker
- SQLite or PostgreSQL database
- Environment variables configured

## Setup

1. Clone the repository
2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Copy .env.example to .env and configure:
   ```bash
   cp .env.example .env
   ```

5. Configure your .env file with appropriate values:
   - WAHA_HOST and WAHA_PORT
   - DATABASE_URL
   - ADMIN_API_KEY
   - Message delays and logging settings

## Running the Service

Start the FastAPI server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at http://localhost:8000

API Documentation will be available at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## API Endpoints

### Messaging (User-Level)

- `POST /api/send` - Submit new campaign
- `GET /api/status/:campaign_id` - Get campaign status
- `GET /api/status/:campaign_id?details=true` - Get detailed status
- `GET /api/campaigns/:sender_number` - List campaigns

### Session Management

- `GET /api/session/:sender_number/status` - Get session status
- `DELETE /api/session/:sender_number` - Delete session (Admin)

### Admin Monitoring

- `GET /api/admin/metrics` - System-wide statistics
- `GET /api/admin/users` - List registered numbers

## Security

Admin endpoints require an API key passed in the X-API-Key header.

## Error Handling

The service includes comprehensive error handling and logging:
- Input validation
- Database errors
- WAHA API communication errors
- Queue processing errors

## Architecture

- FastAPI for the web framework
- SQLAlchemy for database operations
- Pydantic for data validation
- AsyncIO for asynchronous operations
- Message queue for bulk processing
