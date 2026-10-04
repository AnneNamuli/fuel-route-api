"""US place coordinates from the Census gazetteers (offline, no API calls).

Cities and towns come from the places file. Townships and other county subdivisions
(e.g. Mahwah, NJ) fill the gaps, but only where the name is unique within its state.
"""

import csv
import gzip
import re
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from .text import collapse_whitespace

Coordinates = tuple[float, float]  # (latitude, longitude)


class PlaceMatch(TypedDict):
    """A place resolved offline from a 'City, ST' query."""

    label: str
    latitude: float
    longitude: float


DATA_DIR = Path(__file__).parent / 'data'
PLACES_FILE = DATA_DIR / '2025_Gaz_place_national.txt.gz'
SUBDIVISIONS_FILE = DATA_DIR / '2025_Gaz_cousubs_national.txt.gz'

# Census place types appended to names ("Tomah city", "Big Cabin town", "Abanda CDP").
PLACE_SUFFIX = re.compile(
    r'\s+(cdp|city|town|village|borough|municipality|comunidad|zona urbana|city and borough|'
    r'(unified|consolidated|metro|metropolitan) government|urban county)$'
)
# County subdivision types ("Mahwah township", "Autaugaville CCD").
SUBDIVISION_SUFFIX = re.compile(
    r'\s+(charter township|township|town|ccd|city|village|borough|plantation|gore|grant|location|'
    r'purchase|district|precinct|unorganized territory|barrio)$'
)

STATE_NAMES = {
    'alabama': 'AL',
    'alaska': 'AK',
    'arizona': 'AZ',
    'arkansas': 'AR',
    'california': 'CA',
    'colorado': 'CO',
    'connecticut': 'CT',
    'delaware': 'DE',
    'district of columbia': 'DC',
    'florida': 'FL',
    'georgia': 'GA',
    'hawaii': 'HI',
    'idaho': 'ID',
    'illinois': 'IL',
    'indiana': 'IN',
    'iowa': 'IA',
    'kansas': 'KS',
    'kentucky': 'KY',
    'louisiana': 'LA',
    'maine': 'ME',
    'maryland': 'MD',
    'massachusetts': 'MA',
    'michigan': 'MI',
    'minnesota': 'MN',
    'mississippi': 'MS',
    'missouri': 'MO',
    'montana': 'MT',
    'nebraska': 'NE',
    'nevada': 'NV',
    'new hampshire': 'NH',
    'new jersey': 'NJ',
    'new mexico': 'NM',
    'new york': 'NY',
    'north carolina': 'NC',
    'north dakota': 'ND',
    'ohio': 'OH',
    'oklahoma': 'OK',
    'oregon': 'OR',
    'pennsylvania': 'PA',
    'rhode island': 'RI',
    'south carolina': 'SC',
    'south dakota': 'SD',
    'tennessee': 'TN',
    'texas': 'TX',
    'utah': 'UT',
    'vermont': 'VT',
    'virginia': 'VA',
    'washington': 'WA',
    'west virginia': 'WV',
    'wisconsin': 'WI',
    'wyoming': 'WY',
}
# Census reference points that aren't near a drivable road (San Francisco's is offshore, others
# are in parks or mountains), replaced with city-hall coordinates so routing can start there.
CITY_CENTRES: dict[tuple[str, str], Coordinates] = {
    ('CA', 'sanfrancisco'): (37.7793, -122.4193),
    ('AZ', 'tucson'): (32.2217, -110.9265),
    ('SC', 'columbia'): (34.0007, -81.0348),
    ('AK', 'anchorage'): (61.2181, -149.9003),
}
CITY_STATE = re.compile(r'^\s*([^,]+?)\s*,\s*([a-z .]+?)\s*(?:,\s*(?:usa|us|united states))?\s*$', re.I)


def place_key(name: str) -> str:
    """Normalise a place name for matching: 'Saint Mc Calla' -> 'stmccalla'."""
    name = name.lower()
    name = re.sub(r'\bsaint\b', 'st', name)
    name = re.sub(r'\bfort\b', 'ft', name)
    name = re.sub(r'\bmount\b', 'mt', name)
    return re.sub(r'[^a-z]', '', name)


def place_aliases(census_name: str) -> set[str]:
    """Names a city might go by for a Census place.

    'Indianapolis city (balance)' -> indianapolis; 'Nashville-Davidson metropolitan government (balance)'
    -> nashville; 'Carson City' -> carsoncity; 'Town of Pecos city' -> pecos.
    """
    name = census_name.lower().replace(' (balance)', '')
    name = re.sub(r'^town of ', '', name)
    base = PLACE_SUFFIX.sub('', name)
    variants = {name, base, base.split('-')[0], base.split(' county')[0], re.sub(r'\s+city$', '', base)}
    return {place_key(v) for v in variants if v}


def read_gazetteer(path: Path) -> Iterator[tuple[str, str, Coordinates]]:
    """Yield (state, census name, (latitude, longitude)) for each row of a gzipped Census gazetteer file."""
    with gzip.open(path, 'rt', newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f, delimiter='|'):
            yield row['USPS'], row['NAME'].strip(), (float(row['INTPTLAT']), float(row['INTPTLONG'].strip()))


@lru_cache(maxsize=1)
def places() -> dict[tuple[str, str], Coordinates]:
    """{(state, place_key): (latitude, longitude)} for every Census place (city, town, village, CDP)."""
    result: dict[tuple[str, str], Coordinates] = {}
    for state, name, coordinates in read_gazetteer(PLACES_FILE):
        for alias in place_aliases(name):
            result.setdefault((state, alias), coordinates)
    return result


@lru_cache(maxsize=1)
def subdivisions() -> dict[tuple[str, str], Coordinates]:
    """{(state, place_key): (latitude, longitude)} for county subdivisions whose name is unique within the state."""
    found: dict[tuple[str, str], set[Coordinates]] = {}
    for state, name, coordinates in read_gazetteer(SUBDIVISIONS_FILE):
        name = name.lower()
        for alias in {place_key(name), place_key(SUBDIVISION_SUFFIX.sub('', name))}:
            found.setdefault((state, alias), set()).add(coordinates)
    return {key: next(iter(coordinates)) for key, coordinates in found.items() if len(coordinates) == 1}


def us_states() -> set[str]:
    """Two-letter codes of all states (and DC/PR) in the gazetteer."""
    return {state for state, _ in places()}


def preload() -> None:
    """Load the gazetteers now rather than on the first lookup (called once at server start)."""
    places()
    subdivisions()


def lookup(city: str, state: str) -> Coordinates | None:
    """(latitude, longitude) for a city and two-letter state, or None."""
    key = (state.upper(), place_key(city))
    return CITY_CENTRES.get(key) or places().get(key) or subdivisions().get(key)


def lookup_query(query: str) -> PlaceMatch | None:
    """Resolve 'City, ST' or 'City, State name' to {'label', 'latitude', 'longitude'}, or None."""
    match = CITY_STATE.match(query)
    if not match:
        return None
    city = collapse_whitespace(match[1])
    state = match[2].replace('.', '').strip()  # "D.C." -> "DC"
    state = STATE_NAMES.get(state.lower(), state.upper())
    found = lookup(city, state)
    if not found:
        return None
    if city.islower() or city.isupper():  # "denver" -> "Denver"; "McAllen" is left as typed
        city = city.title()
    latitude, longitude = found
    return {'label': f'{city}, {state}', 'latitude': latitude, 'longitude': longitude}
