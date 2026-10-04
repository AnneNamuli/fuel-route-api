# Design decisions

This covers the choices behind the API: what I picked, what else I considered, and what each one costs. The numbers come from running the app against the real data and OpenRouteService.

## Keeping routing calls to one

The brief asks for as few calls to the routing API as possible. A request makes one call, to get the route. Everything else is answered locally:

- Start and finish written as "City, ST" are resolved from US Census place data that ships with the app. Only full street addresses go to the geocoder, which costs one more call each.
- Routes are cached in Postgres for a day, so asking for the same trip again makes no call. The cache is in the database rather than in memory, so every gunicorn worker shares it and it survives restarts.
- The map link in each response reuses the cached route, so opening it is free too.

The trade-off is about 3 MB of bundled Census files, and city lookups that are only as precise as the Census reference point for each place. More on that below.

## Where the stations are

The CSV gives each station's address but no coordinates, and the addresses are mostly highway exits ("I-44, EXIT 283 & US-69"). I needed coordinates to tell which stations are on a route.

Geocoding each station through the API was the obvious option, but it doesn't fit: there are about 3,600 distinct cities, and the free key's geocoding quota ran out after roughly 200 lookups. Instead, stations are placed at their city's Census reference point, offline. That covers 97% of US stations (6,442 of 6,626). Townships come from the Census county subdivision file, but only where the name is unique within its state, to avoid placing a station in the wrong Washington Township. The remaining 3% can be geocoded through the API over a few days, and the command saves progress so it can stop and resume.

Because the positions are only accurate to the city, a station counts as on the route if it's within 10 miles of it.

## Duplicate rows in the CSV

The file has 8,151 rows but only 6,738 distinct OPIS IDs. Rows sharing an ID always have the same address, city, state and rack ID; only the name and price differ. I kept every row as a price, attached to one station per ID, and plan with each station's lowest price. The file has no dates, so there's no way to tell which price is current; the lowest one is what a driver could plausibly get.

## Choosing stops

Picking stops on price alone gives silly plans. Coast to coast, the cheapest plan made 19 stops, several of them buying a gallon or two to save a fraction of a cent. Nobody drives like that.

Two things fix that. Each stop carries the fuel for its detour off the route (twice the station's distance from it), which makes far-off bargains lose to stations on the highway. And each stop is treated as costing $5 of time and hassle on top. On New York to Los Angeles, the price-only plan made 19 stops before detours were counted. With detours it makes 9 stops for $869.56, and with the $5 stop cost as well, 7 stops for $872.76. `FUEL_STOP_COST=0` drops the stop cost.

The planner works in two steps. A dynamic program over (station, fuel level) picks which stations to stop at, counting each stop's fixed cost (the $5 plus its detour fuel); it works in 5-mile fuel steps and rounds distances up, so whatever it picks is reachable. A greedy pass then works out exactly how much to buy at each stop, and the detour fuel is added to that stop's purchase. For a given set of stops, that greedy rule (buy just enough to reach a cheaper station, otherwise fill up) gives the cheapest amounts possible.

One speed-up comes from the data: many stations share their city's coordinates, so they sit at exactly the same place (the same point along the route and the same distance from it), and only the cheapest of those can ever be the best place to buy. Dropping the others leaves the plan unchanged, which I checked on real routes, and cuts New York to Los Angeles from 444 candidates to 223 and planning time from 76 ms to 20 ms.

## What "total money spent on fuel" means

If the car started with a free full tank, a 400-mile trip would cost $0 and longer trips would leave out the first 500 miles. So the car starts empty, fills up at the best-value station within 25 miles of the start, and arrives empty. Gallons bought always equal (distance + detours) ÷ 10. "Best value" for that first stop counts its detour too, spread over the tankful it typically buys: price × (1 + detour ÷ range).

If no station is that close, it fills up at the first one along the route, and the fuel for the miles before it is priced there. The response's `notes` field says when that happens.

## Returning a map

An API can't return a picture inside JSON, so the response has the route as GeoJSON (the route line plus a point per stop), which any map library can draw. The line is simplified with Douglas–Peucker to within about 50 m of the real route, so New York to Los Angeles goes from 21,760 points to 2,620 and its response from 160 KB to 24 KB compressed, with no visible difference. Station matching still uses the full-detail route. It also has a `map_url` that opens an interactive Leaflet map of the same trip. I decided against rendering a static image on the server: it would be slower, and OpenStreetMap's tile servers don't allow automated use at that rate.

## GET only

Planning a route is a lookup, so it's a GET. That makes a trip a URL you can bookmark or share, something a cache could store, and something Swagger can describe with plain query parameters. Each trip is also recorded as a saved trip for the dropdowns. That's a side effect, but it's comparable to a view counter and doesn't change the answer.

## Rejecting places outside the US

Names are only ever resolved within the US. Raw coordinates are checked against the actual US outline from the Census, since a simple bounding box would accept Toronto and Tijuana. The outline is simplified, so there's a one-mile tolerance at the border, which means Windsor, Ontario (across the river from Detroit) gets through.

Some Census reference points aren't near a road at all; San Francisco's is offshore. The handful in the dropdown list use city-hall coordinates. For anything else, if routing can't find a road near the point, the API geocodes the city and retries once, then caches the corrected point.

## Protecting the routing quota

The free key allows a few hundred routes a day and the API is public, so planning is rate-limited to 30 trips an hour per client IP. The counters live in the shared cache. If the cache fails, requests are let through: rate limiting protects the quota, but it isn't worth an outage.

## Failures

Every error comes back as JSON with a status that says what went wrong: 400 for bad input, 422 when part of the route has no station within range, 502 or 503 when the routing service fails or the quota runs out, and 429 for the rate limit. Unexpected errors are logged with a traceback and return a plain 500 without internal details. Bad configuration, such as a zero mpg or a missing secret key, stops the app at startup rather than on the first request.

## Known limitations and what I'd do next

- **The vehicle is routed as a car.** The brief doesn't name a vehicle type, so I used OpenRouteService's car profile. If it's a truck (the data is truck stops, and 10 mpg fits), switching `ROUTING_PROFILE` to `driving-hgv` would avoid low bridges and weight-restricted roads.
- **Station positions are city-level,** and so are the detour estimates built on them: a truck stop at an exit may be closer to the highway than its city's centre. The biggest accuracy gain would be matching each station's address ("I-44, EXIT 283") to the highway the route actually uses.
- **Range checks measure along the route.** Detour fuel is counted in the choice of stops and in the cost, but a stop's detour miles (up to about 20) aren't counted against the 500-mile range.
- **Prices have no dates,** so the lowest listed price is used.
- **Canadian stations aren't located,** so routes through Canada (Alaska to the lower 48, for example) report a gap in fuel coverage.
- **At larger scale,** I'd move the station search to PostGIS. The current approach (a bounding-box query plus a grid match in Python) takes about 30 ms, which is fine for 6,700 stations but wouldn't hold up for a much larger dataset.
