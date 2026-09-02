import re
from rapidfuzz import fuzz


# ============================================================
# CROP KEYWORDS
# English + Hindi + Punjabi
# Canonical value MUST match clean_mandi_prices.csv
# ============================================================

CROP_KEYWORDS = {

    "Apple": ["apple", "सेब", "ਸੇਬ"],

    "Ashgourd": [
        "ashgourd", "ash gourd",
        "पेठा", "पेठे", "ਪੇਠਾ"
    ],

    "Banana": ["banana", "केला", "ਕੇਲਾ"],

    "Beetroot": ["beetroot", "चुकंदर", "ਚੁਕੰਦਰ"],

    "Bhindi(Ladies Finger)": [
        "bhindi", "ladies finger", "okra",
        "भिंडी", "ਭਿੰਡੀ"
    ],

    "Bitter gourd": [
        "bitter gourd", "karela",
        "करेला", "ਕਰੇਲਾ"
    ],

    "Bottle gourd": [
        "bottle gourd", "lauki", "ghiya",
        "लौकी", "घिया", "ਲੌਕੀ", "ਘੀਆ"
    ],

    "Brinjal": [
        "brinjal", "eggplant", "baingan",
        "बैंगन", "ਬੈਂਗਣ"
    ],

    "Cabbage": [
        "cabbage", "पत्तागोभी", "ਬੰਦ ਗੋਭੀ"
    ],

    "Capsicum": [
        "capsicum", "bell pepper",
        "शिमला मिर्च", "ਸ਼ਿਮਲਾ ਮਿਰਚ"
    ],

    "Carrot": ["carrot", "गाजर", "ਗਾਜਰ"],

    "Cauliflower": [
        "cauliflower", "फूलगोभी", "ਫੁੱਲ ਗੋਭੀ"
    ],

    "Chilly Capsicum": [
        "chilly capsicum",
        "चिली कैप्सिकम",
        "ਚਿੱਲੀ ਕੈਪਸਿਕਮ"
    ],

    "Colacasia": [
        "colacasia", "arbi",
        "अरबी", "ਅਰਬੀ"
    ],

    "Coriander(Leaves)": [
        "coriander", "धनिया", "ਧਨੀਆ"
    ],

    "Cucumbar(Kheera)": [
        "cucumber", "kheera",
        "खीरा", "ਖੀਰਾ"
    ],

    "French Beans(Frasbean)": [
        "french beans", "frasbean",
        "फ्रेंच बीन्स", "ਫਰੈਂਚ ਬੀਨਜ਼"
    ],

    "Garlic": [
        "garlic", "लहसुन", "ਲਸਣ"
    ],

    "Ginger(Green)": [
        "ginger", "green ginger",
        "अदरक", "ਅਦਰਕ"
    ],

    "Green Chilli": [
        "green chilli", "green chili",
        "हरी मिर्च", "ਹਰੀ ਮਿਰਚ"
    ],

    "Guava": ["guava", "अमरूद", "ਅਮਰੂਦ"],

    "Lemon": ["lemon", "नींबू", "ਨਿੰਬੂ"],

    "Mango": ["mango", "आम", "ਅੰਬ"],

    "Mint(Pudina)": [
        "mint", "pudina",
        "पुदीना", "ਪੁਦੀਨਾ"
    ],

    "Mousambi(Sweet Lime)": [
        "mousambi", "sweet lime",
        "मौसंबी", "ਮੌਸਮੀ"
    ],

    "Onion": ["onion", "प्याज", "ਪਿਆਜ਼"],

    "Papaya": ["papaya", "पपीता", "ਪਪੀਤਾ"],

    "Pea Pod/Pea Cod/हरी मटर": [
        "pea", "pea pod", "peas",
        "मटर", "ਮਟਰ"
    ],

    "Pear(Marasebu)": [
        "pear", "marasebu",
        "नाशपाती", "ਨਾਸ਼ਪਾਤੀ"
    ],

    "Peas Wet": [
        "peas wet", "हरी मटर", "ਹਰੀ ਮਟਰ"
    ],

    "Pineapple": [
        "pineapple", "अनानास", "ਅਨਾਨਾਸ"
    ],

    "Plum": [
        "plum", "आलूबुखारा", "ਆਲੂ ਬੁਖਾਰਾ"
    ],

    "Pomegranate": [
        "pomegranate", "अनार", "ਅਨਾਰ"
    ],

    "Potato": [
        "potato", "आलू", "ਆਲੂ"
    ],

    "Pumpkin": [
        "pumpkin", "कद्दू", "ਕੱਦੂ"
    ],

    "Raddish": [
        "raddish", "radish",
        "मूली", "ਮੂਲੀ"
    ],

    "Ridgeguard(Tori)": [
        "ridgeguard", "ridge gourd", "tori",
        "तुरई", "ਤੋਰੀ"
    ],

    "Spinach": [
        "spinach", "पालक", "ਪਾਲਕ"
    ],

    "Tender Coconut": [
        "tender coconut",
        "नारियल पानी",
        "ਕੱਚਾ ਨਾਰੀਅਲ"
    ],

    "Tinda": [
        "tinda", "टिंडा", "ਟਿੰਡਾ"
    ],

    "Tomato": [
        "tomato", "टमाटर", "ਟਮਾਟਰ"
    ],

    "Water Melon": [
        "water melon", "watermelon",
        "तरबूज", "ਤਰਬੂਜ਼"
    ],
}


# ============================================================
# MANDI KEYWORDS
# English + Hindi + Punjabi
# ============================================================

MANDI_KEYWORDS = {

    "Ajnala": [
        "ajnala", "अजनाला", "ਅਜਨਾਲਾ"
    ],

    "Amritsar(Amritsar Mewa Mandi)": [
        "amritsar",
        "amritsar mewa mandi",
        "अमृतसर",
        "अमृतसर मेवा मंडी",
        "ਅੰਮ੍ਰਿਤਸਰ",
        "ਅੰਮ੍ਰਿਤਸਰ ਮੇਵਾ ਮੰਡੀ"
    ],

    "Doraha": [
        "doraha", "दोराहा", "ਦੋਰਾਹਾ"
    ],

    "Dudhansadhan": [
        "dudhansadhan",
        "दुधानसाधन",
        "ਦੁਧਾਨਸਾਧਨ"
    ],

    "Gehri": [
        "gehri", "गेहरी", "ਗਹਿਰੀ"
    ],

    "Gehri(Jandiala mandi)": [
        "gehri jandiala",
        "jandiala mandi",
        "गेहरी जंडियाला मंडी",
        "ਗਹਿਰੀ ਜੰਡਿਆਲਾ ਮੰਡੀ"
    ],

    "Ghanaur": [
        "ghanaur", "घनौर", "ਘਨੌਰ"
    ],

    "Jagraon": [
        "jagraon", "जगराओं", "ਜਗਰਾਉਂ"
    ],

    "Khanna": [
        "khanna", "खन्ना", "ਖੰਨਾ"
    ],

    "Ludhiana": [
        "ludhiana", "लुधियाना", "ਲੁਧਿਆਣਾ"
    ],

    "Machhiwara": [
        "machhiwara", "माछीवाड़ा", "ਮਾਛੀਵਾੜਾ"
    ],

    "Mehta": [
        "mehta", "मेहता", "ਮੇਹਤਾ"
    ],

    "Nabha": [
        "nabha", "नाभा", "ਨਾਭਾ"
    ],

    "Patiala": [
        "patiala",
        "पटियाला",
        "ਪਟਿਆਲਾ"
    ],

    "Patran": [
        "patran", "पातरां", "ਪਾਤੜਾਂ"
    ],

    "Raikot": [
        "raikot", "रायकोट", "ਰਾਏਕੋਟ"
    ],

    "Rajpura": [
        "rajpura", "राजपुरा", "ਰਾਜਪੁਰਾ"
    ],

    "Rayya": [
        "rayya", "रैया", "ਰਈਆ"
    ],

    "Sahnewal": [
        "sahnewal", "साहनेवाल", "ਸਾਹਨੇਵਾਲ"
    ],

    "Samana": [
        "samana", "समाना", "ਸਮਾਣਾ"
    ],

    "Samrala": [
        "samrala", "समराला", "ਸਮਰਾਲਾ"
    ],
}


# ============================================================
# NORMALIZATION
# ============================================================

# Generic words meaning "market" that appear both as ordinary spoken
# vocabulary ("...mandi ch" = "...in the market") and, confusingly, as part
# of several compound mandi names ("Amritsar Mewa Mandi", "Gehri Jandiala
# Mandi"). Left in, a farmer just saying "mandi" fuzzy-matches those compound
# names better than the mandi they actually meant. Stripped as whole words
# (not substrings) so real place names like "Mehta" are untouched.
MARKET_STOPWORDS = {
    "mandi", "market", "apmc",
    "मंडी", "मार्किट", "मार्केट",
    "ਮੰਡੀ", "ਮਾਰਕੀਟ",
}


def normalize_text(text: str) -> str:
    """
    Normalize English/Hindi/Punjabi speech-to-text output.
    """

    if not text:
        return ""

    text = str(text).casefold().strip()

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)

    # Remove common punctuation
    text = re.sub(r"[,.!?;:\"'()\[\]{}]", " ", text)

    # Normalize whitespace again
    text = re.sub(r"\s+", " ", text).strip()

    # Drop generic "market" words as whole tokens (see MARKET_STOPWORDS above)
    text = " ".join(w for w in text.split() if w not in MARKET_STOPWORDS)

    return text


# ============================================================
# BUILD ALIAS LIST
# ============================================================

def build_alias_list(keyword_map: dict):
    """
    Convert dictionary into:
    [(alias, canonical_name), ...]
    """

    aliases = []

    for canonical_name, alias_list in keyword_map.items():

        for alias in alias_list:

            alias = normalize_text(alias)

            if alias:
                aliases.append(
                    (alias, canonical_name)
                )

    # Longer aliases first.
    # Prevents "pea" from beating "pea pod".
    aliases.sort(
        key=lambda x: len(x[0]),
        reverse=True
    )

    return aliases


CROP_ALIASES = build_alias_list(CROP_KEYWORDS)
MANDI_ALIASES = build_alias_list(MANDI_KEYWORDS)


# ============================================================
# CREATE TEXT CHUNKS
# ============================================================

def generate_chunks(text: str, max_words: int = 5):
    """
    Generate 1-5 word chunks from the spoken sentence.

    Example:

    "ਆਲੂ ਦਾ ਕੀ ਭਾਅ ਹੈ ਪਟਿਆਲੇ ਵਾਲੀ ਮੰਡੀ ਚ"

    produces chunks such as:

    "ਪਟਿਆਲੇ"
    "ਪਟਿਆਲੇ ਵਾਲੀ"
    "ਪਟਿਆਲੇ ਵਾਲੀ ਮੰਡੀ"
    """

    words = text.split()

    chunks = set()

    for size in range(1, max_words + 1):

        for i in range(len(words) - size + 1):

            chunk = " ".join(
                words[i:i + size]
            )

            chunks.add(chunk)

    return chunks


# ============================================================
# FUZZY MATCHING
# ============================================================

def fuzzy_find_keyword(
    text: str,
    aliases: list,
    threshold: int = 82
):
    """
    Find the closest crop/mandi alias.

    Exact matching is attempted first.

    If exact matching fails, RapidFuzz is used to
    handle speech-to-text variations.
    """

    text = normalize_text(text)

    if not text:
        return None

    # --------------------------------------------------------
    # STEP 1 — EXACT MATCH
    # --------------------------------------------------------

    for alias, canonical_name in aliases:

        if alias in text:
            return canonical_name

    # --------------------------------------------------------
    # STEP 2 — FUZZY MATCH
    # --------------------------------------------------------

    chunks = generate_chunks(text)

    best_score = 0
    best_match = None
    best_alias = None

    for alias, canonical_name in aliases:

        for chunk in chunks:

            # WRatio works well for short phrases
            # inside longer sentences.
            score = fuzz.WRatio(
                alias,
                chunk
            )

            if score > best_score:

                best_score = score
                best_match = canonical_name
                best_alias = alias

    # --------------------------------------------------------
    # SHORT WORD SAFETY
    # --------------------------------------------------------

    # Very short aliases such as "pea" should require a stronger
    # similarity to avoid false matches. This must key off the alias
    # that actually won the match, not the shortest alias anywhere in
    # the whole list — otherwise one short alias (e.g. "Rayya") forces
    # every unrelated, unambiguous match in the list to clear the same
    # strict bar for no reason.

    if best_alias is not None and len(best_alias) <= 4:
        required_threshold = 88
    else:
        required_threshold = threshold

    if best_score >= required_threshold:

        print(
            f"FUZZY MATCH: {best_match} "
            f"(score={best_score:.1f})"
        )

        return best_match

    return None


# ============================================================
# EXTRACT CROP + MANDI
# ============================================================

def extract_crop_and_mandi(question: str):
    """
    Extract crop and mandi from an English,
    Hindi, or Punjabi farmer question.

    Matching strategy:

    1. Exact multilingual keyword match.
    2. Fuzzy matching for speech variations.
    """

    question = normalize_text(question)

    crop = fuzzy_find_keyword(
        question,
        CROP_ALIASES,
        threshold=82
    )

    mandi = fuzzy_find_keyword(
        question,
        MANDI_ALIASES,
        threshold=82
    )

    result = {
        "crop": crop,
        "mandi": mandi,
    }

    print("VOICE EXTRACTION")
    print("Question:", question)
    print("Crop:", crop)
    print("Mandi:", mandi)

    return result


# ============================================================
# TESTING
# ============================================================

if __name__ == "__main__":

    test_questions = [

        "when should I sell potato in rayya mandi?",

        "मुझे आलू रैया मंडी में कब बेचना चाहिए?",

        "ਆਲੂ ਦਾ ਕੀ ਭਾਅ ਹੈ ਪਟਿਆਲੇ ਵਾਲੀ ਮੰਡੀ ਚ",

        "what is the price of tomato in rajpura mandi",

        "ਮੈਨੂੰ ਰਾਜਪੁਰੇ ਮੰਡੀ ਵਿੱਚ ਆਲੂ ਦਾ ਭਾਅ ਦੱਸੋ",
    ]

    for question in test_questions:

        print("\n" + "=" * 60)

        print(
            extract_crop_and_mandi(question)
        )