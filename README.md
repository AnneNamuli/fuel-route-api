# fuel-route-api

Give it a start and finish anywhere in the US and it plans the drive. It returns the route on a map, where to stop for fuel, and what the fuel will cost, assuming a 500-mile range and 10 mpg.

Routing comes from [OpenRouteService](https://openrouteservice.org), fuel prices from the OPIS truck stop CSV, and station locations from US Census data bundled with the app. A request usually makes one routing call. Routes are cached in Postgres for a day, so repeat trips make none.

The reasoning behind the main choices, and the known limitations, are in [DECISIONS.md](DECISIONS.md).

## Running it

You need Docker, a free [OpenRouteService API key](https://openrouteservice.org/dev/#/signup), and the fuel price CSV saved as `data/fuel-prices-for-be-assessment.csv`.

```bash
cp .env.example .env
```

In `.env`, set `ORS_API_KEY` to your key and `DJANGO_SECRET_KEY` to any long random string. You can generate one with `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`. The other values can stay as they are.

```bash
docker compose up --build
```

On first start, the `setup` service runs migrations, loads the CSV and works out station coordinates. That takes about 15 seconds, then the API comes up on port 8000:

- http://localhost:8000/map/ is a map you can plan trips on
- http://localhost:8000/api/docs/ has the Swagger docs
- http://localhost:8000/admin/ is the Django admin (create a user first, see below)

Other useful commands:

```bash
docker compose exec route python manage.py createsuperuser
docker compose run --rm setup          # reload after replacing the CSV
docker compose logs -f route
docker compose down                    # add -v to also delete the database
docker compose config                  # show the merged setup Compose actually runs
```

`docker compose config` prints the values from your `.env` too, including the secret key and API key, so don't paste its output anywhere public.

There are three services in `docker-compose.yml`: `db` (Postgres), `setup` and `route`. The last two run the same app image, so they share their settings through the `x-app: &app` block at the top of the file. Compose ignores top-level keys starting with `x-`, and `&app` names the block so each service can pull it in with `<<: *app` and add its own settings. The merge is shallow, though: a key a service sets replaces the shared one rather than combining with it. That's why `route` repeats the `db` condition in its own `depends_on`.

About 3% of stations are in small unincorporated places that aren't in the Census files. To locate those through OpenRouteService, run the command below. It takes a couple of minutes and can be re-run if you hit the daily geocoding limit. Until then, those stations are just left out of planning.

```bash
docker compose exec route python manage.py geocode_stations --ors
```

If you'd rather run Django directly while working on the code, keep the database in Docker and use [uv](https://docs.astral.sh/uv/). This uses the database the steps above set up, so run `docker compose up` once first:

```bash
docker compose up -d db
docker compose stop route
uv sync
uv run python manage.py runserver
```

## Using the API

```bash
curl 'http://localhost:8000/api/trips/?start=Chicago,+IL&finish=Denver,+CO'
```

Planning is a GET, so a trip's URL can be bookmarked or shared. Start and finish can be:

- a city and state, like `Chicago, IL` or `Chicago, Illinois`, which are looked up offline;
- coordinates, like `41.88,-87.63`;
- any US address, which costs a geocoding call.

The response, trimmed:

```json
{
  "start": {"query": "Chicago, IL", "label": "Chicago, IL", "latitude": 41.837045, "longitude": -87.684939},
  "finish": {"query": "Denver, CO", "label": "Denver, CO", "latitude": 39.76185, "longitude": -104.881105},
  "distance_miles": 1000.7,
  "duration_hours": 15.93,
  "vehicle": {"range_miles": 500.0, "mpg": 10.0},
  "fuel_stops": [
    {"opis_id": 72438, "name": "QUIKTRIP #7208", "city": "Bellwood", "state": "IL", "miles_from_start": 12.9,
     "detour_miles": 1.7, "price_per_gallon": 3.079, "gallons": 32.31, "cost": 99.5},
    {"opis_id": 70333, "name": "KWIK STAR #932", "city": "Altoona", "state": "IA", "miles_from_start": 321.5,
     "detour_miles": 1.7, "price_per_gallon": 2.959, "gallons": 23.89, "cost": 70.69},
    {"opis_id": 68368, "name": "AKAL TRAVEL CENTER", "city": "Waco", "state": "NE", "miles_from_start": 558.7,
     "detour_miles": 10.4, "price_per_gallon": 2.799, "gallons": 45.25, "cost": 126.64}
  ],
  "notes": [],
  "total_gallons": 101.45,
  "total_fuel_cost": 296.83,
  "map": {"type": "FeatureCollection", "features": ["..."]},
  "map_url": "http://localhost:8000/map/?start=Chicago%2C+IL&finish=Denver%2C+CO"
}
```

`map` is GeoJSON with the route (simplified to within about 50 m, which keeps responses small) and a point for each stop. `map_url` opens the same trip on the map page. `notes` explains anything unusual about the cost, such as there being no station near the start.

Every trip that plans successfully is saved. `GET /api/trips/saved/` lists saved trips, and the map page has a dropdown of them. In Swagger, start and finish suggest major US cities and saved locations, and you can type any other place.

Errors come back as JSON with a `detail` message. Invalid input also lists the problem with each field:

```json
{"detail": "Invalid input.", "errors": {"finish": ["This field is required."]}}
```

- 400 for invalid input, a place outside the US or one that can't be found, or two places with no road between them (Hawaii to the mainland, say).
- 404 for an unknown API URL, and 405 for any method other than GET.
- 422 when part of the route has no station within 500 miles. In practice that means routes through Canada, since Canadian stations aren't located.
- 429 when a client plans more than 30 trips an hour. The free OpenRouteService key only allows a few hundred routes a day, so this stops one client using them all up. Change it with `TRIP_THROTTLE_RATE`, e.g. `100/hour`.
- 502 or 503 when OpenRouteService is down, rejects the key, or the quota is used up.
- 500 for anything unexpected. The error is logged, and the response doesn't expose internal details.

`GET /health/` returns 200 if the app can reach the database and 503 if not. Docker Compose uses it to mark the container healthy.

## Assumptions

The results depend on these, so they're worth knowing:

- **The vehicle** has a 500-mile range, gets 10 mpg, and is routed as a car (OpenRouteService's `driving-car` profile). The brief doesn't say what kind of vehicle it is; a truck would need the `driving-hgv` profile, which avoids low bridges and weight limits.
- **The tank starts empty.** The car fills up at the best-value station within 25 miles of the start (or the first station along the route, if none is that close) and arrives empty, so the total covers the fuel for the whole trip: (distance + detours) ÷ mpg.
- **Prices** are US dollars per gallon. The CSV has no dates, so each station is priced at its lowest listed price.
- **Stations are placed at their city's Census point,** so one counts as on the route if it's within 10 miles of it. The detour to reach it and get back (`detour_miles`, twice the distance from the route) is counted in the plan and the cost, but it's only an estimate, since a truck stop at an exit may be closer to the highway than its city's centre.
- **Each stop costs $5** of time and hassle. That keeps plans from stopping for a gallon to save a fraction of a cent; [DECISIONS.md](DECISIONS.md) has the numbers.
- **Start and finish are in the US** and connected by road, so not Hawaii to the mainland. Routes through Canada can't be fuelled, because Canadian stations aren't located.
- **A route doesn't change within a day,** so routes are cached for 24 hours.

`VEHICLE_RANGE_MILES`, `VEHICLE_MPG`, `STATION_MAX_DISTANCE_MILES` and `FUEL_STOP_COST` change the numbers above (`FUEL_STOP_COST=0` plans on price alone).

## Reloading the data

Each CSV row is stored as a price, and rows with the same OPIS ID share one station. Re-importing replaces the prices and updates the stations in place, so their coordinates aren't lost; stations no longer in the file are removed. The import runs in a single transaction, so a bad file changes nothing.

## Code layout

```
config/                  settings and root URLs
fuel_route/
  views.py, serializers.py, urls.py
  models.py              FuelStation, FuelPrice, SavedTrip
  schema.py              API docs and the start/finish suggestions in Swagger
  exceptions.py          JSON error responses
  throttling.py          rate limit for trip planning
  services/
    trip_planner.py      plans a trip from start to finish
    route_stations.py    finds the stations along a route
    fuel_optimizer.py    picks where to stop and how much to buy
    price_import.py      validates and loads the fuel price CSV
    openrouteservice.py  routing and geocoding client
    places.py            offline place lookup
    geo.py               distances and the US border check
    text.py              shared text helpers
    data/                bundled Census files
  management/commands/   load_fuel_stations, geocode_stations
  templates/             the map page
  tests/
scripts/setup.sh         what the setup service runs
gunicorn.conf.py         gunicorn settings (GUNICORN_WORKERS, GUNICORN_THREADS)
.github/workflows/ci.yml lint, type-check, tests, Dockerfile lint and build on every push
```

## Tests and checks

```bash
docker compose up -d db                                          # the tests need Postgres
docker compose run --rm --no-deps route python manage.py test
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

The tests mock OpenRouteService, so they run without a key or network access. The service layer is fully type-annotated and checked with mypy. CI runs all of these, plus a migrations check and a Docker build, on every push.
