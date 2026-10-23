"""Bounded local spelling suggestions, never translations or identity assertions."""
import re
import unicodedata


def tokens(value):
    value = unicodedata.normalize("NFKD", value).casefold().translate(str.maketrans("أإآٱى", "ااااي"))
    value = "".join(c for c in value if not unicodedata.combining(c) and c != "ـ")
    return re.findall(r"[^\W\d_]+", value, re.UNICODE)


# Search suggestions only: transliterations are ambiguous and not legal-name data.
SPELLINGS = [
    ("قصي", "Qusai", "Qusay", "Qussai", "Qussay", "Qusayi"),
    ("محمد", "Mohammad", "Muhammad", "Mohammed", "Mohamed", "Mohamad"),
    ("أحمد", "Ahmad", "Ahmed"), ("محمود", "Mahmoud", "Mahmud"),
    ("حسن", "Hasan", "Hassan"), ("حسين", "Hussein", "Hussain", "Husain"),
    ("علي", "Ali", "Aly"), ("عمر", "Omar", "Umar"), ("عثمان", "Othman", "Uthman"),
    ("يوسف", "Yousef", "Yusuf", "Youssef"), ("ياسر", "Yasser", "Yasir"),
    ("خالد", "Khaled", "Khalid"), ("وليد", "Walid", "Waleed"),
    ("سعيد", "Saeed", "Said"), ("سعد", "Saad"), ("سيف", "Saif", "Sayf"),
    ("سعود", "Saud", "Saoud"), ("فيصل", "Faisal", "Faysal"),
    ("عبدالله", "Abdullah", "Abdallah"), ("عبدالرحمن", "Abdulrahman", "Abdelrahman"),
    ("مصطفى", "Mustafa", "Mostafa"), ("إبراهيم", "Ibrahim", "Ebrahim"),
    ("إسماعيل", "Ismail", "Ismael"), ("يحيى", "Yahya", "Yahia"),
    ("رامي", "Rami", "Ramy"), ("سامي", "Sami", "Samy"),
    ("لؤي", "Louay", "Luay"), ("مهند", "Muhannad", "Mohannad"),
    ("زياد", "Ziad", "Ziyad"), ("زيد", "Zaid", "Zayd"),
    ("طارق", "Tariq", "Tarek"), ("فارس", "Fares", "Faris"),
    ("فاطمة", "Fatima", "Fatimah"), ("عائشة", "Aisha", "Ayesha"),
    ("مريم", "Maryam", "Mariam"), ("نور", "Noor", "Nour"),
    ("سارة", "Sara", "Sarah"), ("ليلى", "Layla", "Laila"),
    ("هند", "Hind", "Hend"), ("إيمان", "Iman", "Eman"),
    ("ريم", "Reem", "Rim"), ("هدى", "Huda", "Hoda"),
    ("أمل", "Amal"), ("جمال", "Jamal", "Gamal"),
    ("العلي", "Alali", "Al-Ali"), ("الهاشمي", "Alhashimi", "Al-Hashimi"),
]

LATIN_TO_ARABIC = {" ".join(tokens(latin)): row[0] for row in SPELLINGS for latin in row[1:]}
ARABIC_TO_LATIN = {" ".join(tokens(row[0])): row[1:] for row in SPELLINGS}
ARABIC_LETTERS = str.maketrans({"ا":"a", "ب":"b", "ت":"t", "ث":"th", "ج":"j", "ح":"h", "خ":"kh",
    "د":"d", "ذ":"dh", "ر":"r", "ز":"z", "س":"s", "ش":"sh", "ص":"s", "ض":"d", "ط":"t", "ظ":"z",
    "ع":"a", "غ":"gh", "ف":"f", "ق":"q", "ك":"k", "ل":"l", "م":"m", "ن":"n", "ه":"h", "و":"w",
    "ي":"y", "ة":"a", "ء":"", "ؤ":"w", "ئ":"y"})


def name_forms(name, supplied=(), expand=True):
    rows, seen = [], set()
    def add(value, origin):
        key = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
        if key and key not in seen:
            seen.add(key)
            rows.append(dict(value=value, origin=origin))
    if name:
        add(name, "supplied")
    for value in supplied:
        add(value, "supplied spelling; ownership unverified")
    parts = tokens(name)
    if not expand or not 2 <= len(parts) <= 8 or len(name) > 200:
        return rows
    if any(re.search(r"[\u0600-\u06ff]", part) for part in parts):
        for index in range(2):
            values = []
            for part in parts:
                variants = ARABIC_TO_LATIN.get(part)
                values.append(variants[min(index, len(variants)-1)] if variants else part.translate(ARABIC_LETTERS).title())
            add(" ".join(values), "automatic transliteration guess; review spelling")
    elif all(part in LATIN_TO_ARABIC for part in parts):
        add(" ".join(LATIN_TO_ARABIC[part] for part in parts), "automatic transliteration guess; review spelling")
    # Accent folding is useful for Latin-script names, including Portuguese.
    folded = " ".join(parts)
    if unicodedata.normalize("NFC", name).casefold() != folded and not re.search(r"[\u0600-\u06ff]", name):
        add(folded, "accent/punctuation-folded search suggestion")
    return rows


def name_match(target, candidate):
    actual = tokens(candidate)
    for row in target.name_forms():
        expected = tokens(row["value"])
        if len(expected) < 2 or len(actual) < len(expected):
            continue
        if not any(actual[i:i+len(expected)] == expected for i in range(len(actual)-len(expected)+1)):
            continue
        kind = "spelling_candidate" if row["origin"].startswith("automatic") else "exact" if actual == expected else "extended"
        return kind, row["value"]
    return None


COUNTRIES = [
    ("السعودية", "Saudi Arabia", "sa,ksa,saudi,المملكة العربية السعودية,Kingdom of Saudi Arabia"),
    ("الأردن", "Jordan", "jo,المملكة الأردنية الهاشمية"),
    ("الإمارات", "United Arab Emirates", "ae,uae,الإمارات العربية المتحدة"), ("فلسطين", "Palestine", "ps"),
    ("مصر", "Egypt", "eg"), ("العراق", "Iraq", "iq"), ("سوريا", "Syria", "sy"),
    ("لبنان", "Lebanon", "lb"), ("الكويت", "Kuwait", "kw"), ("قطر", "Qatar", "qa"),
    ("البحرين", "Bahrain", "bh"), ("عمان", "Oman", "om"), ("اليمن", "Yemen", "ye"),
    ("المغرب", "Morocco", "ma"), ("الجزائر", "Algeria", "dz"), ("تونس", "Tunisia", "tn"),
    ("ليبيا", "Libya", "ly"), ("السودان", "Sudan", "sd"), ("موريتانيا", "Mauritania", "mr"),
    ("الصومال", "Somalia", "so"), ("جيبوتي", "Djibouti", "dj"), ("جزر القمر", "Comoros", "km"),
]


def country_context(country):
    key = " ".join(tokens(country))
    for native, english, aliases in COUNTRIES:
        if key in {" ".join(tokens(value)) for value in [native, english, *aliases.split(",")]}:
            return dict(language="Arabic", terms=list(dict.fromkeys([country, native, english])))
    if key in ("brazil", "brasil", "البرازيل", "br"):
        return dict(language="Portuguese", terms=list(dict.fromkeys([country, "Brasil", "Brazil"])))
    return dict(language="unspecified", terms=[country] if country else [])
