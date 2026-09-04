"""Latin→Cyrillic transliteration for vehicle brand + model names.

Used by scripts/generate_aliases.py (Wave 8) to seed the vehicle_aliases table
so that customer utterances like "Дастер", "Фольксваген", "Тойота Камри" can
be resolved to (brand_id, model_id) by the fitting-flow bot without needing
the LLM prompt to memorise 228 brands.

Two-tier approach:

1. ``BRAND_CYRILLIC_ALIASES`` — hand-curated table for the ~70 brands that
   customers actually pronounce in Ukrainian/Russian tire shops. The Latin
   spelling and the Cyrillic pronunciation often diverge (Peugeot → Пежо,
   Volkswagen → Фольксваген) so char-by-char rules can't solve this alone.
   Multiple accepted variants per brand ("Хёндай", "Хендай", "Хундай") are
   kept in the list — each becomes a separate alias row.

2. ``translit_lat_to_cyr()`` — char-by-char fallback for exotic brands
   (motorcycles, obscure Chinese/Indian makes) not in the hand table.
   Deliberately conservative: ambiguity handling in vehicle_alias_lookup
   catches wrong-brand matches at query time.

Ukrainian and Russian variants are folded into a single list where they
differ only by orthography ("Кіа"/"Киа", "Ніссан"/"Ниссан"). Both go in.
"""

from __future__ import annotations

import re

# --- Hand-crafted brand mappings ---
# Key: Latin brand name as it appears in vehicle_brands.name.
# Value: list of Cyrillic pronunciation variants (ru + uk merged; keep all
# forms customers actually say).
BRAND_CYRILLIC_ALIASES: dict[str, list[str]] = {
    "Acura": ["Акура"],
    "Alfa Romeo": ["Альфа Ромео", "Альфа-Ромео"],
    "Aston Martin": ["Астон Мартін", "Астон Мартин"],
    "Audi": ["Ауді", "Ауди"],
    "Bentley": ["Бентлі", "Бентли"],
    "BMW": ["БМВ", "Бэ-Эм-Вэ", "Би-Эм-Ви"],
    "Buick": ["Бьюік", "Бьюик"],
    "Cadillac": ["Кадиллак", "Кадилак"],
    "Changan": ["Чанган"],
    "Chery": ["Чері", "Чери"],
    "Chevrolet": ["Шевроле"],
    "Chrysler": ["Крайслер"],
    "Citroen": ["Сітроен", "Ситроен"],
    "Citroën": ["Сітроен", "Ситроен"],
    "Dacia": ["Дачія", "Дачия"],
    "Daewoo": ["Дэу", "Део", "Деу"],
    "Daihatsu": ["Дайхатсу", "Дайхацу"],
    "Dodge": ["Додж"],
    "Ferrari": ["Феррарі", "Феррари"],
    "Fiat": ["Фіат", "Фиат"],
    "Ford": ["Форд"],
    "Geely": ["Джилі", "Джили", "Гілі"],
    "Genesis": ["Дженесис", "Дженесіс"],
    "GMC": ["Джи-Эм-Си", "Джи-Ем-Сі"],
    "Great Wall": ["Грейт Волл", "Ґрейт Волл"],
    "Haval": ["Хавал", "Хавейл"],
    "Honda": ["Хонда"],
    "Hummer": ["Хаммер", "Хамер"],
    "Hyundai": ["Хёндай", "Хюндай", "Хендай", "Хундай", "Хендэ", "Хьюндай"],
    "Infiniti": ["Инфинити", "Інфініті"],
    "Isuzu": ["Исузу", "Ісузу"],
    "Iveco": ["Івеко", "Ивеко"],
    "Jaguar": ["Ягуар", "Джагуар"],
    "Jeep": ["Джип"],
    "Kia": ["Кіа", "Киа", "КИА", "КІА"],
    "Lada": ["Лада", "ВАЗ", "Жигули", "Жигулі"],
    "Lamborghini": ["Ламборгіні", "Ламборгини", "Ламборджині"],
    "Lancia": ["Лянча", "Ланчия", "Ланча"],
    "Land Rover": ["Лэнд Ровер", "Ленд Ровер", "Ленд-Ровер", "Ленд ровер"],
    "Lexus": ["Лексус"],
    "Lifan": ["Лифан", "Ліфан"],
    "Lincoln": ["Линкольн", "Лінкольн"],
    "Maserati": ["Мазерати", "Мазераті"],
    "Maybach": ["Майбах"],
    "Mazda": ["Мазда"],
    "Mercedes": ["Мерседес", "Мерс"],
    "Mercedes-Benz": ["Мерседес", "Мерседес-Бенц", "Мерс"],
    "MG": ["Эм-Джи", "Ем-Джі"],
    "MINI": ["Мини", "Міні", "Мини Купер"],
    "Mitsubishi": ["Мицубиси", "Міцубісі", "Мицубиши", "Міцубіші"],
    "Nissan": ["Ниссан", "Ніссан"],
    "Opel": ["Опель"],
    "Peugeot": ["Пежо"],
    "Pontiac": ["Понтиак", "Понтіак"],
    "Porsche": ["Порше"],
    "Ram": ["Рам", "Додж Рам"],
    "Renault": ["Рено"],
    "Rolls-Royce": ["Роллс-Ройс", "Ролс-Ройс"],
    "Saab": ["Сааб"],
    "SEAT": ["Сеат", "Сіат"],
    "Škoda": ["Шкода"],
    "Skoda": ["Шкода"],
    "Smart": ["Смарт"],
    "SsangYong": ["Санг Йонг", "Сангйонг", "Санг-Йонг"],
    "Subaru": ["Субару"],
    "Suzuki": ["Сузукі", "Сузуки", "Судзуки"],
    "Tesla": ["Тесла"],
    "Toyota": ["Тойота"],
    "Volkswagen": ["Фольксваген", "Фольцваген", "Вольксваген", "Вольцваген", "ВВ"],
    "Volvo": ["Вольво"],
    "ZAZ": ["ЗАЗ"],
    "УАЗ": ["УАЗ"],
    "ГАЗ": ["ГАЗ", "Газель"],
    "ВАЗ": ["ВАЗ", "Лада", "Жигули", "Жигулі"],
    "ЗИЛ": ["ЗИЛ", "ЗІЛ"],
    "Москвич": ["Москвич", "Москвіч"],
}

# --- Hand-curated model pronunciation aliases ---
# Char-by-char translit is imperfect for models where English pronunciation
# diverges from spelling (Duster → "Дастер", not "Дустер"; Tucson → "Туссан").
# This table only lists models where the popular Cyrillic pronunciation
# differs meaningfully from what translit_lat_to_cyr() produces.
# Applied to ALL brands' models with matching name — model names are usually
# unique enough within the fitting flow (ambiguity handling catches edge cases).

MODEL_CYRILLIC_ALIASES: dict[str, list[str]] = {
    # Renault / Dacia / etc.
    "Duster": ["Дастер"],
    "Fluence": ["Флюенс"],
    "Kadjar": ["Каджар"],
    "Kangoo": ["Кенго", "Канго"],
    "Koleos": ["Колеос"],
    "Laguna": ["Лагуна"],
    "Latitude": ["Латитюд"],
    "Megane": ["Меган"],
    "Sandero": ["Сандеро"],
    "Scenic": ["Сценік", "Сценик"],
    "Symbol": ["Симбол"],
    "Trafic": ["Трафік"],
    "Vel Satis": ["Вель Сатіс"],
    # Ford
    "Fiesta": ["Фієста", "Фиеста"],
    "Fusion": ["Ф'южн", "Фьюжн", "Фьюжен"],
    "Galaxy": ["Гелексі", "Гэлакси"],
    "Kuga": ["Куга"],
    "Mondeo": ["Мондео"],
    "Ranger": ["Рейнджер"],
    "Transit": ["Транзит"],
    "Explorer": ["Експлорер", "Эксплорер"],
    "Escape": ["Ескейп"],
    "Edge": ["Едж", "Эдж"],
    # Toyota
    "Auris": ["Аурис"],
    "Avensis": ["Авенсіс", "Авенсис"],
    "Highlander": ["Хайлендер"],
    "Land Cruiser": ["Ленд Крузер", "Ленд-Крузер"],
    "Prado": ["Прадо"],
    "RAV4": ["Рав 4", "Рав4", "Рав-4"],
    "Yaris": ["Ярис", "Яріс"],
    "C-HR": ["С-ЧР", "Ц-ГР"],
    "Auris Verso": ["Аурис Версо"],
    "Verso": ["Версо"],
    # Volkswagen
    "Passat": ["Пассат"],
    "Golf": ["Гольф"],
    "Polo": ["Поло"],
    "Jetta": ["Джетта"],
    "Touareg": ["Туарег"],
    "Touran": ["Туран"],
    "Sharan": ["Шаран"],
    "Amarok": ["Амарок"],
    "Caddy": ["Кедді", "Кэдди"],
    "Multivan": ["Мультиван"],
    "Transporter": ["Транспортер"],
    "T5": ["Т5"], "T6": ["Т6"], "T4": ["Т4"],
    # Skoda
    "Fabia": ["Фабіа", "Фабия"],
    "Octavia": ["Октавіа", "Октавия"],
    "Rapid": ["Рапід", "Рапид"],
    "Superb": ["Суперб"],
    "Kodiaq": ["Кодіак", "Кодиак"],
    "Karoq": ["Карок"],
    "Yeti": ["Єті", "Ети"],
    "Roomster": ["Румстер"],
    # Kia
    "Rio": ["Ріо", "Рио"],
    "Ceed": ["Сід", "Сид"],
    "Cee'd": ["Сід", "Сид"],
    "Sportage": ["Спортейдж", "Спортаж", "Спортидж"],
    "Sorento": ["Соренто"],
    "Cerato": ["Керато"],
    "Optima": ["Оптима"],
    "Picanto": ["Піканто", "Пиканто"],
    "Venga": ["Венга"],
    "Soul": ["Соул"],
    "Stinger": ["Стінгер", "Стингер"],
    # Hyundai
    "Accent": ["Акцент"],
    "Elantra": ["Елантра", "Элантра"],
    "Getz": ["Гетц"],
    "i10": ["і10", "и10"],
    "i20": ["і20", "и20"],
    "i30": ["і30", "и30"],
    "i40": ["і40", "и40"],
    "Solaris": ["Соларіс", "Солярис"],
    "Sonata": ["Соната"],
    "Tucson": ["Туссан", "Туксон", "Тусан", "Туссон"],
    "Santa Fe": ["Санта Фе", "Санта-Фе", "Санта фе"],
    "Creta": ["Крета"],
    "H-1": ["Аш-1", "Хендай Аш 1"],
    # BMW
    "3 Series": ["3 серія", "3 серия", "трійка", "тройка"],
    "5 Series": ["5 серія", "5 серия", "п'ятірка", "пятерка"],
    "7 Series": ["7 серія", "7 серия"],
    "X1": ["Ікс1", "Икс1"],
    "X3": ["Ікс3", "Икс3"],
    "X5": ["Ікс5", "Икс5"],
    "X6": ["Ікс6", "Икс6"],
    # Mercedes
    "A-Class": ["А-клас", "А-класс", "А клас"],
    "B-Class": ["Б-клас", "Б-класс"],
    "C-Class": ["Ц-клас", "Ц-класс", "цешка"],
    "E-Class": ["Е-клас", "Е-класс", "ешка"],
    "S-Class": ["С-клас", "С-класс", "эска"],
    "GLA": ["ГЛА"],
    "GLC": ["ГЛЦ"],
    "GLE": ["ГЛЕ"],
    "GLS": ["ГЛС"],
    "Sprinter": ["Спринтер"],
    "Vito": ["Віто", "Вито"],
    "Viano": ["Віано", "Виано"],
    # Chevrolet
    "Aveo": ["Авео"],
    "Cruze": ["Круз"],
    "Captiva": ["Каптіва", "Каптива"],
    "Epica": ["Епіка", "Эпика"],
    "Lacetti": ["Лачетті", "Лачетти"],
    "Lanos": ["Ланос"],
    "Malibu": ["Малібу", "Малибу"],
    "Niva": ["Нива", "Нива Шевроле"],
    "Orlando": ["Орландо"],
    "Spark": ["Спарк"],
    "Tahoe": ["Тахо"],
    "Trailblazer": ["Трейлблейзер"],
    "Traverse": ["Траверс"],
    "Volt": ["Вольт"],
    # Peugeot
    "Partner": ["Партнер"],
    "Boxer": ["Боксер"],
    "3008": ["3008"], "5008": ["5008"], "308": ["308"], "508": ["508"],
    "Expert": ["Експерт", "Эксперт"],
    "Traveller": ["Тревеллер", "Тревелер"],
    # Nissan
    "Almera": ["Альмера"],
    "Juke": ["Джук"],
    "Leaf": ["Ліф", "Лиф"],
    "Micra": ["Мікра", "Микра"],
    "Murano": ["Мурано"],
    "Note": ["Ноут"],
    "Pathfinder": ["Патфайндер"],
    "Patrol": ["Патрол"],
    "Primera": ["Прімера", "Примера"],
    "Qashqai": ["Кашкай"],
    "Rogue": ["Роуг"],
    "Sentra": ["Сентра"],
    "Terrano": ["Террано"],
    "Tiida": ["Тіда", "Тида"],
    "X-Trail": ["Ікс-Трейл", "Икс-Трейл", "Ex-Trail"],
    # Mazda
    "CX-3": ["ЦХ-3", "Ц-Х-3", "СиИкс-3"],
    "CX-5": ["ЦХ-5", "Ц-Х-5", "СиИкс-5"],
    "CX-7": ["ЦХ-7", "Ц-Х-7"],
    "CX-9": ["ЦХ-9", "Ц-Х-9"],
    "MX-5": ["МХ-5", "МиксВ-5"],
    # Honda
    "Accord": ["Аккорд"],
    "Civic": ["Цивік", "Цивик"],
    "CR-V": ["ЦР-В", "СиАр-Ви"],
    "HR-V": ["ХР-В"],
    "Fit": ["Фіт", "Фит"],
    "Jazz": ["Джаз"],
    "Odyssey": ["Одіссей", "Одиссей"],
    "Pilot": ["Пілот", "Пилот"],
    "Insight": ["Інсайт", "Инсайт"],
    # Subaru
    "Forester": ["Форестер"],
    "Impreza": ["Імпреза", "Импреза"],
    "Legacy": ["Легасі", "Легаси"],
    "Outback": ["Аутбек"],
    "XV": ["Ікс-Ві", "Икс-Ви"],
    "Tribeca": ["Трайбека"],
    # Mitsubishi
    "ASX": ["АСХ"],
    "Lancer": ["Лансер", "Ленсер"],
    "Pajero": ["Паджеро"],
    "Outlander": ["Аутлендер", "Оутлендер"],
    "Colt": ["Кольт"],
    "Space Star": ["Спейс Стар"],
    "L200": ["Л200"],
    "Eclipse": ["Еклипс"],
    # Suzuki
    "Grand Vitara": ["Гранд Вітара", "Гранд Витара"],
    "Vitara": ["Вітара", "Витара"],
    "Swift": ["Свіфт", "Свифт"],
    "SX4": ["ЕсІкс4", "СиИкс4"],
    "Jimny": ["Джимні", "Джимни"],
    # Lada
    "Priora": ["Пріора", "Приора"],
    "Kalina": ["Калина"],
    "Vesta": ["Веста"],
    "Granta": ["Гранта"],
    "Largus": ["Ларгус"],
    "XRAY": ["Ікс-Рей", "ИксРей"],
}


# --- Char-by-char translit (fallback for brands not in the hand table) ---
# Deliberately conservative — a "best guess" transliteration. Customers who
# say the exotic brand names will typically use Latin anyway.

_LAT_TO_CYR_DIGRAM: dict[str, str] = {
    "shch": "щ",
    "sch": "щ",
    "sh": "ш",
    "ch": "ч",
    "zh": "ж",
    "kh": "х",
    "ts": "ц",
    "yu": "ю",
    "ju": "ю",
    "ya": "я",
    "ja": "я",
    "yo": "ё",
    "jo": "ё",
    "ph": "ф",
    "th": "т",
    "gh": "г",
}

_LAT_TO_CYR_SINGLE: dict[str, str] = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф",
    "g": "г", "h": "х", "i": "и", "j": "й", "k": "к", "l": "л",
    "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р",
    "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс",
    "y": "и", "z": "з",
    " ": " ", "-": "-", "'": "",
}


def translit_lat_to_cyr(name: str) -> str:
    """Best-guess char-by-char Latin→Cyrillic transliteration.

    Handles common digrams (sh→ш, ch→ч, zh→ж) before falling back to single-
    char rules. Preserves spaces and hyphens. Digits pass through unchanged.
    Capitalisation of the first letter is preserved for display.

    Not intended for brands in ``BRAND_CYRILLIC_ALIASES`` — the hand table
    always wins for those. This is the fallback for exotic makes.
    """
    if not name:
        return ""

    lower = name.lower()
    result: list[str] = []
    i = 0
    n = len(lower)
    while i < n:
        matched = False
        # Try 4-, 3-, 2-char digrams (in that order)
        for length in (4, 3, 2):
            if i + length <= n:
                digram = lower[i : i + length]
                if digram in _LAT_TO_CYR_DIGRAM:
                    result.append(_LAT_TO_CYR_DIGRAM[digram])
                    i += length
                    matched = True
                    break
        if matched:
            continue
        ch = lower[i]
        if ch in _LAT_TO_CYR_SINGLE:
            result.append(_LAT_TO_CYR_SINGLE[ch])
        elif ch.isdigit():
            result.append(ch)  # e.g., BMW X5 → keep 5
        # else: unknown char (Cyrillic already, punctuation) → skip silently
        i += 1

    translit = "".join(result)
    # Preserve leading capital for display form
    if name and name[0].isupper() and translit:
        translit = translit[0].upper() + translit[1:]
    return translit


# --- Normalization for lookup ---


def normalize_alias(text: str) -> str:
    """Normalize an alias for exact-match lookup.

    Applied both when storing an alias (``alias_normalized`` column) and when
    querying with a customer utterance. Ensures "Дастер", "дастер", "Да́стер"
    all collide to the same lookup key.

    Rules:
    1. Trim + lowercase
    2. ё → е / Ё → Е (Cyrillic ё-normalization, matches Wave 5 color_detect)
    3. Strip combining acute (U+0301) and grave (U+0300) — used in bot TTS
       emphasis marks like "да́стер"; do NOT full-NFD-decompose since that
       breaks precomposed Cyrillic letters (й → и + combining brief).
    4. Collapse whitespace runs to single spaces
    """
    if not text:
        return ""
    text = text.strip().lower()
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = text.replace("́", "").replace("̀", "")
    text = re.sub(r"\s+", " ", text)
    return text


def generate_brand_aliases(brand_name: str) -> list[tuple[str, str]]:
    """Generate a list of (alias, source) tuples for a brand.

    Always includes the lowercase Latin form (source='auto_import').
    Adds Cyrillic variants from the hand table if available, else falls back
    to char-by-char translit (source='auto_translit').

    Duplicates within the returned list are eliminated by normalized form.
    """
    result: list[tuple[str, str]] = []
    seen_normalized: set[str] = set()

    def _add(alias: str, source: str) -> None:
        key = normalize_alias(alias)
        if key and key not in seen_normalized:
            seen_normalized.add(key)
            result.append((alias, source))

    # Layer 1: Latin form (as-is, from source CSV)
    _add(brand_name, "auto_import")

    # Layer 2: hand-curated Cyrillic variants OR translit fallback
    if brand_name in BRAND_CYRILLIC_ALIASES:
        for cyr in BRAND_CYRILLIC_ALIASES[brand_name]:
            _add(cyr, "auto_translit")
    else:
        translit = translit_lat_to_cyr(brand_name)
        if translit and normalize_alias(translit) != normalize_alias(brand_name):
            _add(translit, "auto_translit")

    return result


def generate_model_aliases(model_name: str) -> list[tuple[str, str]]:
    """Generate a list of (alias, source) tuples for a model.

    Layer 1: lowercase Latin (source='auto_model_name') — customer saying
             just "Camry" resolves to Toyota Camry.
    Layer 2: char-by-char Cyrillic translit (source='auto_translit') —
             customer saying "Камри" also resolves.

    Model names are usually short (single word or model code like "X5")
    so hand-curated overrides aren't practical here — the char-by-char
    fallback covers Camry→Камри, Duster→Дустер, Focus→Фокус acceptably.
    Ambiguity handling in vehicle_alias_lookup catches wrong matches.
    """
    result: list[tuple[str, str]] = []
    seen_normalized: set[str] = set()

    def _add(alias: str, source: str) -> None:
        key = normalize_alias(alias)
        if key and key not in seen_normalized:
            seen_normalized.add(key)
            result.append((alias, source))

    _add(model_name, "auto_model_name")

    # Hand-curated Cyrillic pronunciations that char-translit can't produce
    # ("Duster" → "Дастер", not "Дустер")
    if model_name in MODEL_CYRILLIC_ALIASES:
        for cyr in MODEL_CYRILLIC_ALIASES[model_name]:
            _add(cyr, "auto_translit")

    # Char-by-char translit — default (Russian "и") variant
    translit_ru = translit_lat_to_cyr(model_name)
    if translit_ru and normalize_alias(translit_ru) != normalize_alias(model_name):
        _add(translit_ru, "auto_translit")

    # Ukrainian variant: swap "и" (Russian i) → "і" (Ukrainian i) so customers
    # saying "Тігуан" instead of "Тигуан" still resolve.
    if translit_ru and "и" in translit_ru.lower():
        translit_uk = translit_ru.replace("и", "і").replace("И", "І")
        if normalize_alias(translit_uk) != normalize_alias(translit_ru):
            _add(translit_uk, "auto_translit")

    # Simplified-consonant variants: Ukrainian speakers often drop doubled
    # consonants ("Пассат" → "Пасат", "Тоскана" already fine). Only handle
    # common tire-shop pairs.
    for doubled in ("сс", "лл", "нн", "тт", "мм", "рр"):
        single = doubled[0]
        if doubled in (translit_ru or "").lower():
            simplified = translit_ru.replace(doubled, single).replace(doubled.upper(), single.upper())
            if normalize_alias(simplified) not in {normalize_alias(a) for a, _ in result}:
                _add(simplified, "auto_translit")

    return result
