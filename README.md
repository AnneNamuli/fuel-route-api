
## Running with Docker

```bash
cp .env.example .env   # set DJANGO_SECRET_KEY
docker compose up --build
```

The API is served by gunicorn at http://localhost:8000. Migrations run on startup, and the SQLite database is kept in the `sqlite_data` volume.
