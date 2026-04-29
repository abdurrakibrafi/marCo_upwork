# MySportsNest Backend

A personalized sports feed platform with multi-sport API integrations, live scores via WebSocket, streak tracking, and AI-powered features — built with Django REST Framework and Docker.

---

## Tech Stack

- **Backend:** Python, Django, Django REST Framework
- **Task Queue:** Celery + Celery Beat
- **Cache / Broker:** Redis
- **Database:** PostgreSQL
- **Monitoring:** Flower
- **AI:** OpenAI
- **Sports APIs:** BallDontLie, API-Sports, API-Cricket, TheSportsDB
- **Containerization:** Docker, Docker Compose

---

## Services

| Service       | Description                        | Port          |
|---------------|------------------------------------|---------------|
| `api`         | Django REST API                    | `8007`        |
| `celery`      | Async task worker                  | —             |
| `celery_beat` | Periodic task scheduler            | —             |
| `flower`      | Celery monitoring dashboard        | `5557`        |
| `db`          | PostgreSQL 15                      | `5437`        |
| `redis`       | Redis 7                            | `6387`        |

---

## Getting Started

### 1. Clone the repo

```bash
git clone https://github.com/MySportsNest-Inc/mysportsnest-backend.git
cd mysportsnest-backend
```

### 2. Set up environment variables

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

See [Environment Variables](#environment-variables) below for all required keys.

### 3. Build and run

```bash
docker-compose up --build
```

API will be available at: `http://localhost:8007`

### 4. Run migrations

```bash
docker exec -it mysportsnest_backend python manage.py migrate
```

### 5. Create superuser

```bash
docker exec -it mysportsnest_backend python manage.py createsuperuser
```

### 6. Collect static files

```bash
docker exec -it mysportsnest_backend python manage.py collectstatic --noinput
```

---

## Useful Commands

```bash
# Stop all containers
docker-compose down

# View API logs
docker logs -f mysportsnest_backend

# View Celery logs
docker logs -f mysportsnest_celery

# Access Flower dashboard
http://localhost:5557

# Django shell
docker exec -it mysportsnest_backend python manage.py shell
```

---

## Environment Variables

Create a `.env` file in the root directory with the following keys:

```env
DEBUG=True
SECRET_KEY=your-secret-key
ALLOWED_HOSTS=localhost,127.0.0.1

DOMAIN_NAME=mysportsnest.com
BASE_URL=https://api.mysportsnest.com

# PostgreSQL
POSTGRES_DB=your_db_name
POSTGRES_USER=your_db_user
POSTGRES_PASSWORD=your_db_password
POSTGRES_HOST=db
POSTGRES_PORT=5432

# Email (Mailgun - production)
EMAIL_HOST=smtp.mailgun.org
MAILGUN_API_KEY=your-mailgun-key
MAILGUN_DOMAIN=your-mailgun-domain
MAILGUN_FROM_EMAIL=no-reply@yourdomain.com
MAILGUN_FROM_NAME=MySportsNest Team

# Email (Dev - optional)
EMAIL_HOST_DEV=sandbox.smtp.mailtrap.io
EMAIL_HOST_USER_DEV=your-mailtrap-user
EMAIL_HOST_PASSWORD_DEV=your-mailtrap-password
EMAIL_PORT_DEV=2525
EMAIL_USE_TLS_DEV=False
DEFAULT_FROM_EMAIL_DEV=no-reply@example.com

RESEND_API_KEY=your-resend-key

# Sports APIs
BALLDONTLIE_KEY=your-key
API_SPORTS_KEY=your-key
API_CRICKET_KEY=your-key
THESPORTSDB_KEY=your-key

# AI / Search
OPENAI_API_KEY=your-key
BRAVESEARCH_KEY=your-key

# Cache TTL (seconds)
LIVE_SCORES_TTL=30
UPCOMING_FIXTURES_TTL=3600
STANDINGS_TTL=3600
TEAM_ROSTER_TTL=604800
PLAYER_PROFILE_TTL=3600
TEAM_LOGO_TTL=2592000
NEWS_ARTICLES_TTL=300

# API Docs basic auth
DOCS_USERNAME=your-username
DOCS_PASSWORD=your-password
```

---

## API Documentation

API docs are available at:

```
http://localhost:8007/api/docs/
```

Protected with basic auth — use `DOCS_USERNAME` and `DOCS_PASSWORD` from your `.env`.

---

## Project Structure

```
mysportsnest/
├── apps/               # Django apps
├── config/             # Project settings, URLs, Celery config
├── nginx/              # Nginx config for production
├── templates/          # HTML templates
├── staticfiles/        # Collected static files
├── media/              # User-uploaded media
├── docker-compose.yml
├── docker-compose-dev.yml
├── docker-compose-prod.yml
├── Dockerfile
├── entrypoint.sh
├── requirements.txt
└── manage.py
```
