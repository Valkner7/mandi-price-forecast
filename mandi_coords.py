import math

PUNJAB_MANDI_COORDINATES = {
    "Abohar": {"lat": 30.1452, "lon": 74.1993, "district": "Fazilka"},
    "Amritsar": {"lat": 31.6340, "lon": 74.8723, "district": "Amritsar"},
    "Anandpur Sahib": {"lat": 31.2370, "lon": 76.4996, "district": "Rupnagar"},
    "Barnala": {"lat": 30.3819, "lon": 75.5468, "district": "Barnala"},
    "Batala": {"lat": 31.8186, "lon": 75.2028, "district": "Gurdaspur"},
    "Bathinda": {"lat": 30.2110, "lon": 74.9455, "district": "Bathinda"},
    "Budhlada": {"lat": 29.9284, "lon": 75.5658, "district": "Mansa"},
    "Dasuya": {"lat": 31.8153, "lon": 75.6582, "district": "Hoshiarpur"},
    "Dharamkot": {"lat": 30.9472, "lon": 75.2343, "district": "Moga"},
    "Faridkot": {"lat": 30.6769, "lon": 74.7583, "district": "Faridkot"},
    "Fatehgarh Sahib": {"lat": 30.6450, "lon": 76.3980, "district": "Fatehgarh Sahib"},
    "Fazilka": {"lat": 30.4037, "lon": 74.0254, "district": "Fazilka"},
    "Firozpur": {"lat": 30.9237, "lon": 74.6136, "district": "Ferozepur"},
    "Gurdaspur": {"lat": 32.0419, "lon": 75.4053, "district": "Gurdaspur"},
    "Hoshiarpur": {"lat": 31.5273, "lon": 75.9142, "district": "Hoshiarpur"},
    "Jagraon": {"lat": 30.7844, "lon": 75.4748, "district": "Ludhiana"},
    "Jalalabad": {"lat": 30.6120, "lon": 74.2562, "district": "Fazilka"},
    "Jalandhar": {"lat": 31.3260, "lon": 75.5762, "district": "Jalandhar"},
    "Jaitu": {"lat": 30.4358, "lon": 74.8386, "district": "Faridkot"},
    "Kapurthala": {"lat": 31.3802, "lon": 75.3818, "district": "Kapurthala"},
    "Khanna": {"lat": 30.7022, "lon": 76.2163, "district": "Ludhiana"},
    "Kharar": {"lat": 30.7460, "lon": 76.6472, "district": "SAS Nagar"},
    "Kurali": {"lat": 30.8242, "lon": 76.5739, "district": "SAS Nagar"},
    "Ludhiana": {"lat": 30.9010, "lon": 75.8573, "district": "Ludhiana"},
    "Malerkotla": {"lat": 30.5250, "lon": 75.8841, "district": "Sangrur"},
    "Malout": {"lat": 30.1903, "lon": 74.4988, "district": "Sri Muktsar Sahib"},
    "Mandi Gobindgarh": {"lat": 30.6631, "lon": 76.2974, "district": "Fatehgarh Sahib"},
    "Mansa": {"lat": 29.9883, "lon": 75.3923, "district": "Mansa"},
    "Maur": {"lat": 30.0809, "lon": 75.2443, "district": "Bathinda"},
    "Moga": {"lat": 30.8165, "lon": 75.1717, "district": "Moga"},
    "Mohali": {"lat": 30.7046, "lon": 76.7179, "district": "SAS Nagar"},
    "Mukerian": {"lat": 31.9538, "lon": 75.6173, "district": "Hoshiarpur"},
    "Muktsar": {"lat": 30.4762, "lon": 74.5122, "district": "Sri Muktsar Sahib"},
    "Nabha": {"lat": 30.3752, "lon": 76.1528, "district": "Patiala"},
    "Nakodar": {"lat": 31.1278, "lon": 75.4748, "district": "Jalandhar"},
    "Nawanshahr": {"lat": 31.1256, "lon": 76.1186, "district": "SBS Nagar"},
    "Pathankot": {"lat": 32.2686, "lon": 75.6499, "district": "Pathankot"},
    "Patiala": {"lat": 30.3398, "lon": 76.3869, "district": "Patiala"},
    "Patti": {"lat": 31.2820, "lon": 74.8569, "district": "Tarn Taran"},
    "Phagwara": {"lat": 31.2240, "lon": 75.7708, "district": "Kapurthala"},
    "Rajpura": {"lat": 30.4842, "lon": 76.5932, "district": "Patiala"},
    "Raman": {"lat": 29.9678, "lon": 74.9622, "district": "Bathinda"},
    "Ropar": {"lat": 30.9664, "lon": 76.5231, "district": "Rupnagar"},
    "Sangrur": {"lat": 30.2458, "lon": 75.8421, "district": "Sangrur"},
    "Shahkot": {"lat": 31.0822, "lon": 75.3398, "district": "Jalandhar"},
    "Sunam": {"lat": 30.1284, "lon": 75.8016, "district": "Sangrur"},
    "Tarn Taran": {"lat": 31.4518, "lon": 74.9254, "district": "Tarn Taran"},
    "Zira": {"lat": 30.9708, "lon": 74.9818, "district": "Ferozepur"}
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
