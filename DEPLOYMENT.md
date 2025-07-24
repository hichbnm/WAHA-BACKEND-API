# Simple Deployment Guide

## 1. Pull the WAHA Image and Start the Container (Docker Compose)
- Log in to Docker, pull the WAHA image, and log out:

```bash
docker login -u devlikeapro -p 'private key get from waha plus portal'  #this key private
docker pull devlikeapro/waha-plus:latest
docker logout
```
- Then use the provided `docker-compose.yml` to run the WAHA API container:

```bash
docker compose up -d 
```
- Make sure the WAHA API is accessible from your FastAPI backend.

## 2. Install PostgreSQL on the Server
```bash
# On Ubuntu/Debian
sudo apt update
sudo apt install postgresql postgresql-contrib
```
- Create a database and user:
```bash
sudo -u postgres psql
CREATE DATABASE whatsapp_campaigns;
CREATE USER whatsapp_user WITH PASSWORD 'yourpassword';
GRANT ALL PRIVILEGES ON DATABASE whatsapp_campaigns TO whatsapp_user;
\q
```

## 3. Set Up FastAPI Backend with Virtual Environment
```bash
# Clone your repository
git clone <your-repo-url>
cd <your-repo-folder>

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```
- Edit `.env` to set your WAHA and PostgreSQL connection details:
  ```
  DATABASE_URL=postgresql+asyncpg://whatsapp_user:yourpassword@localhost:5432/whatsapp_campaigns
  WAHA_API_KEY=waha_api_123
  WAHA_HOST=localhost
  WAHA_PORT=3000
  ```

## 4. Run the FastAPI Server (Auto-Creates Tables)
```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 5000 --workers 4
celery -A celery_app.celery_app worker --loglevel=debug --concurrency=4
```
- On first run, the app will automatically create all tables in the database.

## 5. Verify Database and Tables
- Connect to PostgreSQL and check tables:
```bash
sudo -u postgres psql whatsapp_campaigns
\dt
```
- You should see tables: `campaigns`, `messages`, `sessions`.
- To view rows:
```sql
SELECT * FROM campaigns;
SELECT * FROM messages;
SELECT * FROM sessions;
```
