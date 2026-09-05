import math

# Town-centre GPS coordinates for every mandi that appears in
# clean_mandi_prices.csv. Keeping this in sync with the price dataset is
# what lets /api/nearby-mandis show a farmer's real nearest mandi with a
# real price, not just their nearest town.
#
# Original 22 entries: coordinates sourced from Google Places lookups
# (Sep 2026). The remaining 88 entries were added when statewide
# Tomato/Potato/Onion coverage was merged in (raw_agmarknet CSVs) and were
# geocoded via Google Places lookups on each town/mandi name; district
# assignments are each town's actual administrative district in Punjab.
# If a new mandi is added to clean_mandi_prices.csv, add its coordinates
# here too, or it will simply be skipped by /api/nearby-mandis (not an
# error).
PUNJAB_MANDI_COORDINATES = {
    "Abohar": {"lat": 30.158377, "lon": 74.193288, "district": "Fazilka"},
    "Adampur": {"lat": 31.432949, "lon": 75.713901, "district": "Jalandhar"},
    "Ahmedgarh": {"lat": 30.648977, "lon": 75.749523, "district": "Malerkotla"},
    "Ajnala": {"lat": 31.842825, "lon": 74.762966, "district": "Amritsar"},
    "Amritsar(Amritsar Mewa Mandi)": {"lat": 31.633979, "lon": 74.872264, "district": "Amritsar"},
    "Baghapurana": {"lat": 30.684121, "lon": 75.098409, "district": "Moga"},
    "Balachaur": {"lat": 31.069891, "lon": 76.276241, "district": "Shahid Bhagat Singh Nagar"},
    "Banga": {"lat": 31.188488, "lon": 75.984966, "district": "Shahid Bhagat Singh Nagar"},
    "Banur": {"lat": 30.559584, "lon": 76.698199, "district": "Patiala"},
    "Bariwala": {"lat": 30.537915, "lon": 74.652486, "district": "Muktsar"},
    "Bassi Pathana": {"lat": 30.686119, "lon": 76.404240, "district": "Fatehgarh Sahib"},
    "Batala": {"lat": 31.837006, "lon": 75.199018, "district": "Gurdaspur"},
    "Bathinda": {"lat": 30.198661, "lon": 74.945285, "district": "Bathinda"},
    "Bhagta Bhai Ka": {"lat": 30.481108, "lon": 75.093235, "district": "Bathinda"},
    "Bhawanigarh": {"lat": 30.261393, "lon": 76.033269, "district": "Sangrur"},
    "Bhogpur": {"lat": 31.551395, "lon": 75.640902, "district": "Jalandhar"},
    "Bhucho": {"lat": 30.213222, "lon": 75.090315, "district": "Bathinda"},
    "Bhulath": {"lat": 31.542324, "lon": 75.505832, "district": "Kapurthala"},
    "Bhulath (Nadala)": {"lat": 31.545754, "lon": 75.438843, "district": "Kapurthala"},
    "Bilga": {"lat": 31.050154, "lon": 75.654660, "district": "Jalandhar"},
    "Budalada": {"lat": 29.925883, "lon": 75.554690, "district": "Mansa"},
    "Chamkaur Sahib": {"lat": 30.891559, "lon": 76.413397, "district": "Rupnagar"},
    "Dasuya": {"lat": 31.812696, "lon": 75.661412, "district": "Hoshiarpur"},
    "Dera Baba Nanak": {"lat": 32.032186, "lon": 75.030448, "district": "Gurdaspur"},
    "Dera Bassi": {"lat": 30.588684, "lon": 76.847096, "district": "SAS Nagar (Mohali)"},
    "Dharamkot": {"lat": 30.938085, "lon": 75.230404, "district": "Moga"},
    "Dhariwal": {"lat": 31.958504, "lon": 75.323245, "district": "Gurdaspur"},
    "Dhilwan": {"lat": 31.509998, "lon": 75.335388, "district": "Kapurthala"},
    "Dhuri": {"lat": 30.369286, "lon": 75.860655, "district": "Sangrur"},
    "Dinanagar": {"lat": 32.126566, "lon": 75.463603, "district": "Gurdaspur"},
    "Doraha": {"lat": 30.798648, "lon": 76.030179, "district": "Ludhiana"},
    "Dudhansadhan": {"lat": 30.153705, "lon": 76.528591, "district": "Patiala"},
    "F.G.Churian": {"lat": 31.864464, "lon": 74.955923, "district": "Gurdaspur"},
    "Faridkot": {"lat": 30.593204, "lon": 74.827318, "district": "Faridkot"},
    "Fazilka": {"lat": 30.403648, "lon": 74.027962, "district": "Fazilka"},
    "Ferozepur Cantt.": {"lat": 30.910738, "lon": 74.622476, "district": "Ferozepur"},
    "Firozepur City": {"lat": 30.933135, "lon": 74.622476, "district": "Ferozepur"},
    "Garh Shankar": {"lat": 31.217507, "lon": 76.140693, "district": "Shahid Bhagat Singh Nagar"},
    "Garh Shankar(Mahalpur)": {"lat": 31.363037, "lon": 76.036290, "district": "Shahid Bhagat Singh Nagar"},
    "GarhShankar (Kotfatuhi)": {"lat": 31.275225, "lon": 75.970554, "district": "Shahid Bhagat Singh Nagar"},
    "Gehri": {"lat": 31.499870, "lon": 74.666391, "district": "Amritsar"},
    "Gehri(Jandiala mandi)": {"lat": 31.558655, "lon": 75.029059, "district": "Amritsar"},
    "Ghanaur": {"lat": 30.332021, "lon": 76.611009, "district": "Patiala"},
    "Giddarbaha": {"lat": 30.207437, "lon": 74.657609, "district": "Muktsar"},
    "Goraya": {"lat": 31.124144, "lon": 75.771348, "district": "Jalandhar"},
    "Gurdaspur": {"lat": 32.041392, "lon": 75.403086, "district": "Gurdaspur"},
    "Jagraon": {"lat": 30.792334, "lon": 75.467019, "district": "Ludhiana"},
    "Jalalabad": {"lat": 30.604981, "lon": 74.255787, "district": "Fazilka"},
    "Jalandhar City": {"lat": 31.326015, "lon": 75.576183, "district": "Jalandhar"},
    "Jalandhar City(Jalandhar)": {"lat": 31.326015, "lon": 75.576183, "district": "Jalandhar"},
    "Kalanaur": {"lat": 32.012838, "lon": 75.147241, "district": "Gurdaspur"},
    "Kapurthala": {"lat": 31.372257, "lon": 75.401765, "district": "Kapurthala"},
    "Khamano": {"lat": 30.815237, "lon": 76.346362, "district": "Fatehgarh Sahib"},
    "Khanna": {"lat": 30.707077, "lon": 76.216991, "district": "Ludhiana"},
    "Kharar": {"lat": 30.749868, "lon": 76.641109, "district": "SAS Nagar (Mohali)"},
    "Kot ise Khan": {"lat": 30.952184, "lon": 75.129078, "district": "Moga"},
    "Kotkapura": {"lat": 30.582825, "lon": 74.815043, "district": "Faridkot"},
    "Kurali": {"lat": 30.830768, "lon": 76.580099, "district": "Rupnagar"},
    "Lalru": {"lat": 30.492863, "lon": 76.801968, "district": "SAS Nagar (Mohali)"},
    "Lehra Gaga": {"lat": 29.935359, "lon": 75.810563, "district": "Sangrur"},
    "Lohian Khas": {"lat": 31.161979, "lon": 75.204147, "district": "Jalandhar"},
    "Ludhiana": {"lat": 30.900965, "lon": 75.857276, "district": "Ludhiana"},
    "Machhiwara": {"lat": 30.914135, "lon": 76.192864, "district": "Ludhiana"},
    "Majitha": {"lat": 31.757399, "lon": 74.953026, "district": "Amritsar"},
    "Makhu": {"lat": 31.107153, "lon": 74.976402, "district": "Ferozepur"},
    "Malerkotla": {"lat": 30.524581, "lon": 75.878344, "district": "Malerkotla"},
    "Malout": {"lat": 30.189020, "lon": 74.499696, "district": "Muktsar"},
    "Mamdot": {"lat": 30.870107, "lon": 74.420474, "district": "Ferozepur"},
    "Mansa": {"lat": 29.999507, "lon": 75.393681, "district": "Mansa"},
    "Maur": {"lat": 30.064585, "lon": 75.231740, "district": "Bathinda"},
    "Mehatpur": {"lat": 31.047327, "lon": 75.472341, "district": "Jalandhar"},
    "Mehta": {"lat": 31.675853, "lon": 75.250823, "district": "Amritsar"},
    "Moga": {"lat": 30.823011, "lon": 75.173447, "district": "Moga"},
    "Morinda": {"lat": 30.791232, "lon": 76.502549, "district": "Rupnagar"},
    "Mukerian": {"lat": 31.950154, "lon": 75.617453, "district": "Hoshiarpur"},
    "Mukerian(Talwara)": {"lat": 31.931129, "lon": 75.894059, "district": "Hoshiarpur"},
    "Muktsar": {"lat": 30.476177, "lon": 74.512160, "district": "Muktsar"},
    "Nabha": {"lat": 30.373018, "lon": 76.146955, "district": "Patiala"},
    "Nakodar": {"lat": 31.127019, "lon": 75.481773, "district": "Jalandhar"},
    "Nawan Shahar(Subzi Mandi)": {"lat": 31.125558, "lon": 76.118642, "district": "Shahid Bhagat Singh Nagar"},
    "Nihal Singh Wala": {"lat": 30.592740, "lon": 75.279988, "district": "Moga"},
    "Noor Mehal": {"lat": 31.094261, "lon": 75.588799, "district": "Jalandhar"},
    "Patiala": {"lat": 30.339781, "lon": 76.386880, "district": "Patiala"},
    "Pathankot": {"lat": 32.273335, "lon": 75.652207, "district": "Pathankot"},
    "Patran": {"lat": 29.957085, "lon": 76.052340, "district": "Patiala"},
    "Patti": {"lat": 31.274586, "lon": 74.856561, "district": "Tarn Taran"},
    "Phagwara": {"lat": 31.223159, "lon": 75.767047, "district": "Kapurthala"},
    "Phillaur": {"lat": 31.018990, "lon": 75.787940, "district": "Jalandhar"},
    "Phillaur(Apra Mandi)": {"lat": 31.086111, "lon": 75.878086, "district": "Jalandhar"},
    "Quadian": {"lat": 31.819064, "lon": 75.379110, "district": "Gurdaspur"},
    "Raikot": {"lat": 30.653562, "lon": 75.591709, "district": "Ludhiana"},
    "Rajpura": {"lat": 30.476580, "lon": 76.590532, "district": "Patiala"},
    "Raman": {"lat": 29.950043, "lon": 74.961792, "district": "Bathinda"},
    "Rampuraphul(Nabha Mandi)": {"lat": 30.270130, "lon": 75.239800, "district": "Bathinda"},
    "Rayya": {"lat": 31.540238, "lon": 75.235864, "district": "Amritsar"},
    "Ropar": {"lat": 30.966100, "lon": 76.523096, "district": "Rupnagar"},
    "Sahnewal": {"lat": 30.837532, "lon": 75.972090, "district": "Ludhiana"},
    "Samana": {"lat": 30.154138, "lon": 76.197736, "district": "Patiala"},
    "Samrala": {"lat": 30.835668, "lon": 76.191028, "district": "Ludhiana"},
    "Sangrur": {"lat": 30.245796, "lon": 75.842072, "district": "Sangrur"},
    "Shahkot": {"lat": 31.082359, "lon": 75.338303, "district": "Jalandhar"},
    "Sirhind": {"lat": 30.624512, "lon": 76.386302, "district": "Fatehgarh Sahib"},
    "Sri Har Gobindpur": {"lat": 31.690560, "lon": 75.472341, "district": "Gurdaspur"},
    "Sri Har Gobindpur(Harechowal)": {"lat": 31.780193, "lon": 75.451952, "district": "Gurdaspur"},
    "Sultanpur": {"lat": 31.214128, "lon": 75.195393, "district": "Kapurthala"},
    "Sunam": {"lat": 30.130558, "lon": 75.801398, "district": "Sangrur"},
    "Talwandi Sabo": {"lat": 29.987507, "lon": 75.090315, "district": "Bathinda"},
    "Tanda Urmur": {"lat": 31.675295, "lon": 75.633951, "district": "Hoshiarpur"},
    "Tarantaran": {"lat": 31.453867, "lon": 74.926760, "district": "Tarn Taran"},
    "Zira": {"lat": 30.968531, "lon": 74.988090, "district": "Ferozepur"},
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
