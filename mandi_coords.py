import math

# Town-centre GPS coordinates for every mandi that actually appears in
# clean_mandi_prices.csv (see /meta for the authoritative list). Keeping
# this in sync with the price dataset — rather than a generic list of
# Punjab towns — is what lets /api/nearby-mandis show a farmer's real
# nearest mandi with a real price, not just their nearest town.
#
# Coordinates sourced from Google Places lookups on each town name (Sep
# 2026); district assignments are each town's actual administrative
# district in Punjab. If a new mandi is added to the CSV, add its
# coordinates here too, or it will simply be skipped by /api/nearby-mandis
# (not an error).
PUNJAB_MANDI_COORDINATES = {
    "Ajnala": {"lat": 31.842825, "lon": 74.762966, "district": "Amritsar"},
    "Amritsar(Amritsar Mewa Mandi)": {"lat": 31.633979, "lon": 74.872264, "district": "Amritsar"},
    "Doraha": {"lat": 30.798648, "lon": 76.030179, "district": "Ludhiana"},
    "Dudhansadhan": {"lat": 30.153705, "lon": 76.528591, "district": "Patiala"},
    "Gehri": {"lat": 31.499870, "lon": 74.666391, "district": "Amritsar"},
    "Gehri(Jandiala mandi)": {"lat": 31.558655, "lon": 75.029059, "district": "Amritsar"},
    "Ghanaur": {"lat": 30.332021, "lon": 76.611009, "district": "Patiala"},
    "Jagraon": {"lat": 30.792334, "lon": 75.467019, "district": "Ludhiana"},
    "Khanna": {"lat": 30.707077, "lon": 76.216991, "district": "Ludhiana"},
    "Ludhiana": {"lat": 30.900965, "lon": 75.857276, "district": "Ludhiana"},
    "Machhiwara": {"lat": 30.914135, "lon": 76.192864, "district": "Ludhiana"},
    "Majitha": {"lat": 31.757399, "lon": 74.953026, "district": "Amritsar"},
    "Mehta": {"lat": 31.675853, "lon": 75.250823, "district": "Amritsar"},
    "Nabha": {"lat": 30.373018, "lon": 76.146955, "district": "Patiala"},
    "Patiala": {"lat": 30.339781, "lon": 76.386880, "district": "Patiala"},
    "Patran": {"lat": 29.957085, "lon": 76.052340, "district": "Patiala"},
    "Raikot": {"lat": 30.653562, "lon": 75.591709, "district": "Ludhiana"},
    "Rajpura": {"lat": 30.476580, "lon": 76.590532, "district": "Patiala"},
    "Rayya": {"lat": 31.540238, "lon": 75.235864, "district": "Amritsar"},
    "Sahnewal": {"lat": 30.837532, "lon": 75.972090, "district": "Ludhiana"},
    "Samana": {"lat": 30.154138, "lon": 76.197736, "district": "Patiala"},
    "Samrala": {"lat": 30.835668, "lon": 76.191028, "district": "Ludhiana"},
}


def calculate_haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2.0) ** 2)

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)
