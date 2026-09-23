from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory
import json, re
from statistics import median
from urllib.parse import urljoin, quote_plus
import requests
from bs4 import BeautifulSoup

app = Flask(__name__, static_folder="static")

# Limits (also defined later in freemium; kept early for safety)
FREE_DAILY_LIMIT = 2
FREE_ADS_LIMIT = 6
PREMIUM_ADS_LIMIT = 20
BASE = "https://www.ouedkniss.com"
GRAPHQL = "https://api.ouedkniss.com/graphql"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8,en;q=0.7",
}
GQL_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://www.ouedkniss.com",
    "Referer": "https://www.ouedkniss.com/",
}

AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٬٫", "0123456789,.")
BRANDS = ["kia","hyundai","chevrolet","renault","peugeot","seat","volkswagen","skoda","toyota","dacia","fiat","ford","opel","nissan","suzuki","chery","geely","changan","jetour","mg","citroen","mercedes","bmw","audi"]

BIDI_CHARS = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff"

def clean(s):
    x = str(s or "").replace("\xa0", " ")
    for ch in BIDI_CHARS:
        x = x.replace(ch, "")
    x = re.sub(r"^\s*#{1,6}\s*", "", x)
    x = x.replace("**", "").replace("__", "")
    x = re.sub(r"^\s*[-*+]\s+", "", x)
    return " ".join(x.split())


def norm(s):
    return clean(s).translate(AR_DIGITS)


def num(raw):
    if raw is None:
        return None
    x = norm(raw)
    x = re.sub(r"[^0-9.,]", "", x)
    if not x:
        return None
    if "." in x and "," in x:
        x = x.replace(".", "").replace(",", ".")
    elif "," in x:
        parts = x.split(",")
        x = ".".join(parts) if len(parts[-1]) <= 2 else "".join(parts)
    elif "." in x:
        parts = x.split(".")
        x = ".".join(parts) if len(parts[-1]) <= 2 else "".join(parts)
    try:
        return float(x)
    except ValueError:
        return None


def ad_url(href):
    return urljoin(BASE, href or "")


def is_ad_url(url):
    return bool(re.search(r"ouedkniss\.com/.+-d\d+", url, re.I) or re.search(r"ouedkniss\.com/.+d\d+", url, re.I))


def parse_price(text):
    t = norm(text)
    patterns = [
        r"(?<!\d)([0-9][0-9\s.,]*)\s*(?:مليون|ملايين|million|millions)(?![A-Za-z])",
        r"(?<!\d)([0-9][0-9\s.,]*)\s*(?:DA|DZD|دج)(?![A-Za-z])",
    ]
    for i, pat in enumerate(patterns):
        m = re.search(pat, t, re.I)
        if m:
            v = num(m.group(1))
            if v is not None:
                return v if i == 0 else v / 100000
    return None


def parse_km(text):
    t = norm(text)
    m = re.search(r"([0-9][0-9\s.,]*)\s*(?:km|كم|kilom[eè]tres?|kilometrage)(?![A-Za-z])", t, re.I)
    if m:
        v = num(m.group(1))
        return int(v) if v is not None else None
    return None


def parse_year(text):
    m = re.search(r"\b(19\d{2}|20\d{2})\b", norm(text))
    return int(m.group(1)) if m else None


def extract_ad_id(url):
    m = re.search(r"(?:-d|/d)(\d{5,})\b", url or "", re.I)
    return m.group(1) if m else None


def gql(query, variables=None, timeout=30):
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    # Detect operationName if present
    m = re.search(r"(?:query|mutation)\s+(\w+)", query)
    if m:
        payload["operationName"] = m.group(1)
    r = requests.post(GRAPHQL, json=payload, headers=GQL_HEADERS, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        msg = data["errors"][0].get("message", "GraphQL error")
        raise requests.RequestException(msg)
    return data.get("data") or {}


# ---------- GraphQL: single listing ----------
ANN_QUERY = """
query AnnouncementGet($id: ID!) {
  announcement: announcementDetails(id: $id) {
    id
    reference
    title
    slug
    description
    createdAt: refreshedAt
    price
    pricePreview
    priceUnit
    priceType
    cities { name region { name } }
    category { name slug }
    viewCount
    specs {
      specification { label codename }
      value
      valueText
    }
  }
}
"""

def _spec_map(specs):
    out = {}
    if not specs:
        return out
    for item in specs:
        spec = item.get("specification") or {}
        codename = (spec.get("codename") or "").lower()
        label = (spec.get("label") or "").lower()
        texts = item.get("valueText") or item.get("value") or []
        if isinstance(texts, list):
            val = clean(texts[0]) if texts else ""
        else:
            val = clean(texts)
        key = None
        if codename in ("annee", "year") or "année" in label or "annee" in label:
            key = "year"
        elif codename in ("marque-voiture", "marque") or "marque" in label or "brand" in label:
            key = "brand"
        elif codename in ("modele", "model") or "modèle" in label or "modele" in label:
            key = "model"
        elif codename in ("nom_special", "finition") or "finition" in label or "version" in label:
            key = "trim"
        elif codename in ("car-engine", "moteur") or "moteur" in label or "engine" in label:
            key = "engine"
        elif codename in ("energie", "fuel") or "energie" in label or "énergie" in label or "fuel" in label:
            key = "fuel"
        elif codename in ("transmission", "boite") or "boite" in label or "boîte" in label or "transmission" in label:
            key = "gear"
        elif codename in ("kilometrage", "km") or "kilométrage" in label or "kilometrage" in label:
            key = "km"
        if key and val:
            out[key] = val
    return out


def _price_from_ann(ann):
    """Return price in 'مليون' units used by the UI."""
    unit = (ann.get("priceUnit") or "").upper()
    preview = ann.get("pricePreview")
    raw = ann.get("price")
    if preview is not None and preview not in (0, "0", ""):
        try:
            return float(preview)
        except (TypeError, ValueError):
            pass
    if raw is not None:
        try:
            v = float(raw)
            # Ouedkniss stores full DA; cars are usually shown as millions.
            if unit == "MILLION" or v >= 50000:
                return round(v / 10000, 2) if v >= 100000 else round(v / 1000000, 2) if v >= 1000000 else round(v, 2)
            return float(v)
        except (TypeError, ValueError):
            pass
    return None


def extract_listing(url):
    ad_id = extract_ad_id(url)
    if not ad_id:
        raise ValueError("رابط إعلان غير صالح — لم يتم العثور على رقم الإعلان")

    data = gql(ANN_QUERY, {"id": str(ad_id)})
    ann = data.get("announcement")
    if not ann:
        raise requests.RequestException("تعذر العثور على الإعلان")

    specs = _spec_map(ann.get("specs"))
    title = clean(ann.get("title") or "")
    slug = clean(ann.get("slug") or "")

    # Fallbacks from title/slug
    tf_brand, tf_model, tf_trim, tf_year = title_fields(title, slug)

    brand = specs.get("brand") or tf_brand
    model = specs.get("model") or tf_model
    trim = specs.get("trim") or tf_trim
    year = specs.get("year") or tf_year
    if year:
        ym = re.search(r"\b(19\d{2}|20\d{2})\b", norm(year))
        year = ym.group(1) if ym else year

    engine = specs.get("engine") or ""
    fuel = specs.get("fuel") or ""
    gear = specs.get("gear") or ""
    km_raw = specs.get("km") or ""
    km_val = parse_km(km_raw) or parse_km(title)
    km = str(km_val) if km_val is not None else ""

    price_val = _price_from_ann(ann)
    if price_val is None:
        price_val = parse_price(title)

    cities = ann.get("cities") or []
    location = ""
    if cities:
        c = cities[0]
        location = clean(f"{c.get('name','')} - {(c.get('region') or {}).get('name','')}".strip(" -"))

    result = {
        "ok": True,
        "url": url,
        "title": title,
        "brand": brand,
        "model": model,
        "trim": trim,
        "year": str(year) if year else "",
        "engine": engine,
        "fuel": fuel,
        "gear": gear,
        "km": km,
        "price": str(round(price_val, 2)) if price_val is not None else "",
        "ad_id": str(ann.get("id") or ad_id),
        "date": clean(ann.get("createdAt") or ""),
        "views": str(ann.get("viewCount") or ""),
        "location": location,
        "description": clean(ann.get("description") or ""),
        "images": [],
        "similar_ads": [],
    }
    return result


def title_fields(title, slug):
    h = clean((title or "") + " " + (slug or "")).replace("_", " ").replace("-", " ")
    brand = ""
    for b in BRANDS:
        if re.search(rf"(?<![A-Za-z]){re.escape(b)}(?![A-Za-z])", h, re.I):
            brand = b.title()
            break
    model = ""
    if brand:
        m = re.search(rf"{re.escape(brand)}\s+(.+?)(?=\s+(?:19\d{{2}}|20\d{{2}})\b)", h, re.I)
        if m:
            candidate = clean(m.group(1))
            candidate = re.split(r"\b(?:full|option|options|luxur|luxury|style|toute|toutes|battle|super\s+power)\b", candidate, maxsplit=1, flags=re.I)[0].strip()
            if candidate:
                model = candidate
    year = ""
    ym = re.search(r"\b(19\d{2}|20\d{2})\b", h)
    if ym:
        year = ym.group(1)
    trim = ""
    tm = re.search(r"\b(full\s*option(?:s)?|full|toute\s+option(?:s)?|luxur(?:y)?|style|battle|super\s+power)\b", h, re.I)
    if tm:
        trim = clean(tm.group(1))
    return brand, model, trim, year


# ---------- GraphQL: search ----------
SEARCH_QUERY = """
query SearchQuery($q: String, $filter: SearchFilterInput) {
  search(q: $q, filter: $filter) {
    announcements {
      data {
        id
        title
        slug
        price
        pricePreview
        priceUnit
        description
        createdAt: refreshedAt
        cities { name region { id name } }
      }
      paginatorInfo { lastPage hasMorePages total }
    }
  }
}
"""

# Official Algerian wilaya codes (match Ouedkniss region ids)
WILAYAS = {
    1: "Adrar", 2: "Chlef", 3: "Laghouat", 4: "Oum El Bouaghi", 5: "Batna",
    6: "Béjaïa", 7: "Biskra", 8: "Béchar", 9: "Blida", 10: "Bouira",
    11: "Tamanrasset", 12: "Tébessa", 13: "Tlemcen", 14: "Tiaret", 15: "Tizi Ouzou",
    16: "Alger", 17: "Djelfa", 18: "Jijel", 19: "Sétif", 20: "Saïda",
    21: "Skikda", 22: "Sidi Bel Abbès", 23: "Annaba", 24: "Guelma", 25: "Constantine",
    26: "Médéa", 27: "Mostaganem", 28: "M'Sila", 29: "Mascara", 30: "Ouargla",
    31: "Oran", 32: "El Bayadh", 33: "Illizi", 34: "Bordj Bou Arréridj", 35: "Boumerdès",
    36: "El Tarf", 37: "Tindouf", 38: "Tissemsilt", 39: "El Oued", 40: "Khenchela",
    41: "Souk Ahras", 42: "Tipaza", 43: "Mila", 44: "Aïn Defla", 45: "Naâma",
    46: "Aïn Témouchent", 47: "Ghardaïa", 48: "Relizane",
    49: "Timimoun", 50: "Bordj Badji Mokhtar", 51: "Ouled Djellal", 52: "Béni Abbès",
    53: "In Salah", 54: "In Guezzam", 55: "Touggourt", 56: "Djanet",
    57: "El M'Ghair", 58: "El Meniaa",
}

FUEL_KEYWORDS = {
    "essence": ["essence", "بنزين", "petrol", "gasoline"],
    "diesel": ["diesel", "ديزل", "غازوال", "gasoil", "gazole"],
    "gpl": ["gpl", "lpg", "غاز"],
    "hybrid": ["hybrid", "hybride", "هجين"],
    "electric": ["electric", "électrique", "كهرب"],
}

def _ann_to_ad(ann):
    title = clean(ann.get("title") or "")
    slug = clean(ann.get("slug") or "")
    ad_id = str(ann.get("id") or "")
    url = f"{BASE}/{slug}-d{ad_id}" if slug else f"{BASE}/d{ad_id}"
    price = _price_from_ann(ann)
    year = parse_year(title)
    km = parse_km(title) or parse_km(ann.get("description") or "")
    return {
        "title": title[:900],
        "url": url,
        "price": price,
        "year": year,
        "km": km,
    }


def fetch_search(query, limit=60, min_price=None, max_price=None, region_id=None, fuel=None):
    """Search real Ouedkniss vehicle listings via GraphQL.

    min_price / max_price are in «مليون» units (same as the UI).
    region_id: official wilaya code (1-58).
    fuel: essence|diesel|gpl|hybrid|electric.
    """
    out, seen = [], set()
    categories = ["automobiles-voitures", "automobiles"]
    pages_needed = max(1, (limit + 19) // 20)

    price_range = [None, None]
    price_unit = None
    if min_price is not None or max_price is not None:
        lo = float(min_price) if min_price is not None else None
        hi = float(max_price) if max_price is not None else None
        price_range = [lo, hi]
        price_unit = "MILLION"

    region_ids = []
    if region_id not in (None, "", "0"):
        try:
            region_ids = [int(region_id)]
        except (TypeError, ValueError):
            region_ids = []

    fuel_key = (fuel or "").lower().strip()
    q = (query or "").strip()

    for cat in categories:
        for page in range(1, pages_needed + 1):
            try:
                data = gql(SEARCH_QUERY, {
                    "q": q or None,
                    "filter": {
                        "categorySlug": cat,
                        "origin": None,
                        "connected": False,
                        "delivery": None,
                        "regionIds": region_ids,
                        "cityIds": [],
                        "priceRange": price_range,
                        "exchange": None,
                        "hasPictures": False,
                        "hasPrice": True,
                        "priceUnit": price_unit,
                        "fields": [],
                        "page": page,
                        "orderByField": {"field": "REFRESHED_AT"},
                        "count": 20,
                    }
                })
            except Exception:
                continue
            anns = (((data.get("search") or {}).get("announcements") or {}).get("data")) or []
            if not anns:
                break
            for ann in anns:
                ad = _ann_to_ad(ann)
                if not ad["url"] or ad["url"] in seen:
                    continue
                if ad["price"] is None:
                    continue
                if min_price is not None and ad["price"] < float(min_price) * 0.95:
                    continue
                if max_price is not None and ad["price"] > float(max_price) * 1.05:
                    continue
                if ad["price"] < 15 or ad["price"] > 2000:
                    continue
                if fuel_key in FUEL_KEYWORDS:
                    blob = norm((ad.get("title") or "") + " " + (ann.get("description") or "")).lower()
                    keys = FUEL_KEYWORDS[fuel_key]
                    if not any(k in blob for k in keys):
                        any_fuel = any(k in blob for ks in FUEL_KEYWORDS.values() for k in ks)
                        if any_fuel:
                            continue
                cities = ann.get("cities") or []
                if cities:
                    c0 = cities[0]
                    reg = (c0.get("region") or {}).get("name") or ""
                    ad["location"] = clean(f"{c0.get('name', '')} - {reg}".strip(" -"))
                    ad["region"] = reg
                seen.add(ad["url"])
                out.append(ad)
                if len(out) >= limit:
                    return out
        if len(out) >= min(10, limit):
            break
    return out


def plausible_ads(ads, brand, model, year, source_url):
    model_l = norm(model).lower().strip()
    brand_l = norm(brand).lower().strip()
    y = str(year).strip()
    out, seen = [], set()
    for ad in ads:
        if ad.get("price") is None:
            continue
        ad_url_value = ad.get("url") or ""
        if ad_url_value and ad_url_value in seen:
            continue
        if source_url and ad_url_value and ad_url_value.rstrip("/") == source_url.rstrip("/"):
            continue
        t = ad.get("title", "").lower()
        # Flexible matching: require model if provided, brand is soft
        if model_l and model_l not in t and not any(p in t for p in model_l.split() if len(p) > 2):
            # allow partial model match (e.g. "clio" in "Clio 5")
            parts = [p for p in re.split(r"\s+", model_l) if len(p) > 2]
            if parts and not any(p in t for p in parts):
                continue
        if brand_l and brand_l not in t:
            # soft: skip only if brand clearly different and present
            pass
        if y and ad.get("year") and str(ad["year"]) != y:
            continue
        # Reject obvious placeholder / outlier prices
        if ad["price"] < 20 or ad["price"] > 2000:
            continue
        if ad_url_value:
            seen.add(ad_url_value)
        out.append(ad)
    return out


def stats(ads):
    try:
        priced = [a for a in (ads or []) if a.get("price") is not None]
        prices = sorted(float(a["price"]) for a in priced)
        if len(prices) < 3:
            return None
        med = median(prices)
        deviations = sorted(abs(x - med) for x in prices)
        mad = median(deviations) or 1
        filtered = [a for a in priced if abs(float(a["price"]) - med) <= max(3 * mad, med * 0.15)]
        if len(filtered) >= 3:
            prices = sorted(float(a["price"]) for a in filtered)
            med = median(prices)
            priced = filtered
        return {
            "count": len(priced),
            "min": min(prices),
            "max": max(prices),
            "median": med,
            "ads": priced,
        }
    except Exception:
        return None


@app.get("/")
def home():
    return send_from_directory("static", "index.html")


@app.post("/api/analyze_url")
def analyze_url():
    data = request.get_json(silent=True) or {}
    url = clean(data.get("url"))
    if not url:
        return jsonify({"ok": False, "error": "ضع رابط الإعلان أولاً"}), 400
    # Auto-read is fully supported for Ouedkniss. Other sites: guide user to fill manually.
    if not re.match(r"^https?://(?:www\.)?ouedkniss\.com/", url, re.I):
        return jsonify({
            "ok": False,
            "error": "القراءة التلقائية من الرابط متاحة حالياً لـ Ouedkniss. "
                     "للمواقع الأخرى: انسخ بيانات الإعلان (الماركة، الموديل، السنة، السعر...) "
                     "واملأ الحقول يدوياً ثم اضغط «تحليل الإعلان». البحث عن بدائل والأسعار المرجعية يعمل من بياناتك مباشرة.",
            "manual_ok": True,
        }), 400
    try:
        return jsonify(extract_listing(url))
    except requests.RequestException as e:
        return jsonify({"ok": False, "error": f"تعذر الوصول إلى Ouedkniss: {e}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": f"تعذر استخراج الإعلان: {e}"}), 500


@app.get("/api/wilayas")
def api_wilayas():
    return jsonify({"ok": True, "wilayas": [{"id": k, "name": v} for k, v in sorted(WILAYAS.items())]})



def _detect_anomalies(brand, model, year, price, mileage, st):
    """Premium: notes and unusual price/mileage flags (always returns useful notes when possible)."""
    notes = []
    try:
        y = int(year) if str(year).strip().isdigit() else None
    except Exception:
        y = None
    try:
        age = (datetime.utcnow().year - y) if y else None
    except Exception:
        age = None
    try:
        med = None
        diff = None
        if st and st.get("median") and price:
            med = float(st["median"])
            if med:
                diff = (float(price) - med) / med * 100
                if diff <= -18:
                    notes.append({
                        "type": "price_low",
                        "text": f"السعر أقل من السوق بحوالي {abs(diff):.0f}% (الوسيط {med:.0f} M). راجع الحالة والمستندات جيداً قبل الشراء.",
                    })
                elif diff >= 20:
                    notes.append({
                        "type": "price_high",
                        "text": f"السعر أعلى من السوق بحوالي {diff:.0f}% (الوسيط {med:.0f} M). قد يكون هناك مجال للتفاوض أو تجهيزات إضافية.",
                    })
                else:
                    notes.append({
                        "type": "price_ok",
                        "text": f"السعر ضمن نطاق السوق تقريباً (فرق حوالي {diff:+.0f}% عن الوسيط {med:.0f} M).",
                    })
                if st.get("min") is not None and st.get("max") is not None:
                    notes.append({
                        "type": "range",
                        "text": f"نطاق السوق بعد التصفية: {st['min']:.0f} – {st['max']:.0f} M اعتماداً على {st.get('count', '?')} إعلاناً.",
                    })
        if age is not None and mileage:
            try:
                mileage_f = float(mileage)
            except Exception:
                mileage_f = 0
            if mileage_f > 0:
                expected = max(age, 1) * 15000
                if mileage_f > expected * 1.8 and mileage_f > 60000:
                    notes.append({
                        "type": "km_high",
                        "text": f"الكيلومترات ({int(mileage_f):,}) مرتفعة نسبياً لعمر {age} سنة (المتوقع تقريباً {int(expected):,} km).",
                    })
                elif age >= 7 and mileage_f < 25000:
                    notes.append({
                        "type": "km_low",
                        "text": f"الكيلومترات ({int(mileage_f):,}) منخفضة لسيارة عمرها {age} سنة — تحقق من العداد وسجل الصيانة.",
                    })
                else:
                    notes.append({
                        "type": "km_ok",
                        "text": f"الكيلومترات ({int(mileage_f):,}) ضمن المتوقع تقريباً لسيارة عمرها {age} سنة.",
                    })
        if price and float(price) < 50:
            notes.append({
                "type": "price_suspicious",
                "text": "السعر منخفض بشكل غير معتاد؛ تحقق أن الوحدة بالمليون سنتيم وأن الإعلان حقيقي.",
            })
        if not notes:
            notes.append({
                "type": "general",
                "text": "راجع الوثائق (البطاقة الرمادية، التأمين) وافحص السيارة عند ميكانيكي قبل إتمام الشراء.",
            })
    except Exception:
        if not notes:
            notes.append({
                "type": "general",
                "text": "راجع الوثائق وافحص السيارة عند مختص قبل الشراء.",
            })
    return notes


@app.get("/api/valuation")
def valuation():
    try:
        return _valuation_impl()
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": f"خطأ في التحليل: {e}", "ads": []}), 500


def _valuation_impl():
    brand = clean(request.args.get("brand"))
    model = clean(request.args.get("model"))
    year = clean(request.args.get("year"))
    source = clean(request.args.get("source_url"))
    engine = clean(request.args.get("engine"))
    fuel = clean(request.args.get("fuel"))
    try:
        mileage = float(request.args.get("km", "0"))
    except ValueError:
        mileage = 0
    try:
        price = float(request.args.get("price", "0"))
    except ValueError:
        price = 0
    if not model or not year or price <= 0:
        return jsonify({"ok": False, "error": "أدخل الموديل والسنة والسعر أولاً", "ads": []}), 400

    # Tier for response richness (client should call /api/consume_analysis first)
    did = _device_id(request)
    prem, _ = _is_premium(did)
    ads_cap = int(PREMIUM_ADS_LIMIT if prem or _single_credits(did) > 0 else FREE_ADS_LIMIT)
    ads_cap = max(3, min(ads_cap, 30))

    errors = []
    candidates = []

    # Search Ouedkniss with progressive queries
    queries = []
    if brand and model and year:
        queries.append(f"{brand} {model} {year}")
    if brand and model:
        queries.append(f"{brand} {model}")
    if model and year:
        queries.append(f"{model} {year}")
    if model:
        queries.append(model)

    for q in queries:
        try:
            candidates.extend(fetch_search(q, 80))
        except Exception as e:
            errors.append(str(e))
        if len(plausible_ads(candidates, brand, model, year, source)) >= 12:
            break

    all_ads = plausible_ads(candidates, brand, model, "", source)
    y = int(year) if str(year).strip().isdigit() else None
    exact, nearby, wider = [], [], []
    model_l = norm(model).lower().strip()
    brand_l = norm(brand).lower().strip()
    model_parts = [p for p in re.split(r"\s+", model_l) if len(p) > 2]

    for ad in all_ads:
        title = norm(ad.get("title", "")).lower()
        if model_parts and not any(p in title for p in model_parts):
            continue
        if brand_l and brand_l not in title:
            # still allow if model matches strongly
            if not (model_parts and sum(1 for p in model_parts if p in title) >= max(1, len(model_parts) - 1)):
                continue
        ay = ad.get("year")
        ay_i = None
        if ay is not None:
            try:
                ay_i = int(float(str(ay).strip()))
            except (TypeError, ValueError):
                ay_i = None
        if y and ay_i is not None:
            delta = abs(ay_i - y)
            if delta == 0:
                exact.append(ad)
            elif delta <= 2:
                nearby.append(ad)
            elif delta <= 5:
                wider.append(ad)
        else:
            wider.append(ad)

    if len(exact) >= 4:
        selected = exact
        tier = "same_year"
        tier_text = "نفس الماركة والموديل والسنة" if request.args.get("lang", "ar") == "ar" else "same brand, model and year"
    elif len(exact) + len(nearby) >= 4:
        selected = exact + nearby
        tier = "nearby_years"
        tier_text = "نفس الماركة والموديل، وسنوات قريبة" if request.args.get("lang", "ar") == "ar" else "same brand/model with nearby years"
    elif len(exact) + len(nearby) + len(wider) >= 3:
        selected = exact + nearby + wider
        tier = "wider_years"
        tier_text = "نفس الماركة والموديل، مع توسيع سنوات المقارنة" if request.args.get("lang", "ar") == "ar" else "same brand/model with a wider year range"
    else:
        selected = exact + nearby + wider
        tier = "insufficient"
        tier_text = "بيانات غير كافية" if request.args.get("lang", "ar") == "ar" else "insufficient data"

    st = stats(selected)
    if not st or st["count"] < 3:
        return jsonify({
            "ok": True,
            "reference": None,
            "count": len(selected),
            "ads": sorted(selected, key=lambda a: abs((a["price"] or 0) - price))[:ads_cap],
            "tier_access": "premium" if prem else "free",
            "tier": tier,
            "tier_text": tier_text,
            "errors": errors,
        })

    med = st["median"]
    diff = (price - med) / med * 100 if med else 0
    if diff <= -15:
        verdict = "أقل بوضوح من أسعار الإعلانات المماثلة"
    elif diff <= -5:
        verdict = "أقل قليلًا من أسعار الإعلانات المماثلة"
    elif diff < 5:
        verdict = "قريب من أسعار الإعلانات المماثلة"
    elif diff < 15:
        verdict = "أعلى قليلًا من أسعار الإعلانات المماثلة"
    else:
        verdict = "أعلى بوضوح من أسعار الإعلانات المماثلة"

    ordered = sorted(st["ads"], key=lambda a: abs(a["price"] - med))
    return jsonify({
        "ok": True,
        "reference": round(med, 1),
        "min": round(st["min"], 1),
        "max": round(st["max"], 1),
        "count": st["count"],
        "difference_pct": diff,
        "verdict": verdict,
        "tier": tier,
        "tier_text": tier_text,
        "ads": ordered[:ads_cap],
        "tier_access": "premium" if prem else "free",
        "anomalies": _detect_anomalies(brand, model, year, price, mileage, st) if (prem or _single_credits(did) > 0) else [],
        "errors": errors,
    })


CLASS_MAP = {
    # city cars / mini
    "i10": ["Hyundai i10", "Chevrolet Spark", "Hyundai Atos", "Renault Twingo", "Kia Picanto"],
    "spark": ["Chevrolet Spark", "Hyundai i10", "Hyundai Atos", "Renault Twingo", "Kia Picanto"],
    "atos": ["Hyundai Atos", "Hyundai i10", "Chevrolet Spark", "Kia Picanto"],
    "picanto": ["Kia Picanto", "Hyundai i10", "Chevrolet Spark", "Renault Twingo"],
    "twingo": ["Renault Twingo", "Hyundai i10", "Chevrolet Spark", "Kia Picanto"],
    # supermini / B-segment
    "clio": ["Renault Clio", "Peugeot 208", "Seat Ibiza", "Volkswagen Polo", "Skoda Fabia", "Citroen C3", "Hyundai i20"],
    "208": ["Peugeot 208", "Renault Clio", "Seat Ibiza", "Volkswagen Polo", "Skoda Fabia", "Citroen C3"],
    "207": ["Peugeot 207", "Peugeot 208", "Renault Clio", "Citroen C3", "Seat Ibiza", "Volkswagen Polo"],
    "206": ["Peugeot 206", "Peugeot 207", "Renault Clio", "Citroen C3"],
    "ibiza": ["Seat Ibiza", "Renault Clio", "Peugeot 208", "Volkswagen Polo", "Skoda Fabia"],
    "polo": ["Volkswagen Polo", "Seat Ibiza", "Renault Clio", "Peugeot 208", "Skoda Fabia"],
    "fabia": ["Skoda Fabia", "Seat Ibiza", "Renault Clio", "Peugeot 208", "Volkswagen Polo"],
    "c3": ["Citroen C3", "Peugeot 208", "Renault Clio", "Peugeot 207"],
    "i20": ["Hyundai i20", "Renault Clio", "Peugeot 208", "Kia Rio"],
    "rio": ["Kia Rio", "Hyundai i20", "Renault Clio", "Peugeot 208"],
    "sandero": ["Dacia Sandero", "Renault Sandero", "Peugeot 208", "Renault Clio", "Logan"],
    "logan": ["Dacia Logan", "Renault Logan", "Dacia Sandero", "Peugeot 301"],
    # compact / C-segment
    "megane": ["Renault Megane", "Peugeot 308", "Volkswagen Golf", "Seat Leon", "Hyundai Elantra"],
    "308": ["Peugeot 308", "Renault Megane", "Volkswagen Golf", "Seat Leon", "Citroen C4"],
    "golf": ["Volkswagen Golf", "Renault Megane", "Peugeot 308", "Seat Leon", "Skoda Octavia"],
    "leon": ["Seat Leon", "Volkswagen Golf", "Renault Megane", "Peugeot 308"],
    "c4": ["Citroen C4", "Peugeot 308", "Renault Megane"],
    "focus": ["Ford Focus", "Renault Megane", "Peugeot 308", "Volkswagen Golf"],
    "octavia": ["Skoda Octavia", "Volkswagen Golf", "Renault Megane", "Seat Leon"],
    "elantra": ["Hyundai Elantra", "Kia Cerato", "Toyota Corolla", "Renault Megane"],
    "corolla": ["Toyota Corolla", "Hyundai Elantra", "Kia Cerato", "Renault Megane"],
    "cerato": ["Kia Cerato", "Hyundai Elantra", "Toyota Corolla"],
    "symbol": ["Renault Symbol", "Dacia Logan", "Peugeot 301", "Renault Clio"],
    "301": ["Peugeot 301", "Renault Symbol", "Dacia Logan", "Citroen C-Elysee"],
    # SUV / crossover common in DZ
    "duster": ["Dacia Duster", "Renault Duster", "Hyundai Tucson", "Kia Sportage", "Peugeot 2008"],
    "tucson": ["Hyundai Tucson", "Kia Sportage", "Dacia Duster", "Nissan Qashqai"],
    "sportage": ["Kia Sportage", "Hyundai Tucson", "Dacia Duster", "Nissan Qashqai"],
    "qashqai": ["Nissan Qashqai", "Hyundai Tucson", "Kia Sportage", "Dacia Duster"],
    "2008": ["Peugeot 2008", "Renault Captur", "Dacia Duster", "Citroen C3 Aircross"],
    "captur": ["Renault Captur", "Peugeot 2008", "Dacia Duster"],
    "kx1": ["Kia KX1", "Hyundai Creta", "Kia Sonet", "Renault Captur"],
    "creta": ["Hyundai Creta", "Kia Seltos", "Kia KX1", "Renault Captur"],
}


def _model_key(model):
    """Normalize model name to a CLASS_MAP key."""
    m = norm(model).lower().strip()
    if not m:
        return ""
    # Prefer longer known keys first
    for key in sorted(CLASS_MAP.keys(), key=len, reverse=True):
        if key in m or m in key:
            return key
    # first token (e.g. "207" from "207 Allure")
    return m.split()[0] if m.split() else m


@app.get("/api/alternatives")
def alternatives():
    did = _device_id(request)
    if not _has_paid_access(did):
        return jsonify({
            "ok": False,
            "error": "البحث عن البدائل متاح بعد الدفع (Premium أو تحليل مدفوع).",
            "ads": [],
            "need_premium": True,
        }), 402
    brand = clean(request.args.get("brand"))
    model = clean(request.args.get("model"))
    year = clean(request.args.get("year"))
    source = clean(request.args.get("source_url"))
    typ = request.args.get("type", "same")
    mode = request.args.get("range", "both")
    try:
        target = float(request.args.get("price", "0"))
    except ValueError:
        target = 0
    if target <= 0 or not model:
        return jsonify({"ok": False, "error": "أدخل الموديل والسعر أولاً", "ads": []}), 400

    try:
        return _alternatives_impl(brand, model, year, source, typ, mode, target)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": f"تعذر البحث عن البدائل: {e}", "ads": []}), 500


def _alternatives_impl(brand, model, year, source, typ, mode, target):
    queries = []
    if typ == "same":
        if brand and model and year:
            queries.append(f"{brand} {model} {year}")
        if brand and model:
            queries.append(f"{brand} {model}")
        queries.append(model)
        if brand:
            queries.append(f"{brand} {model.split()[0]}" if model.split() else brand)
    else:
        key = _model_key(model)
        class_list = CLASS_MAP.get(key, [])
        if class_list:
            queries.extend(class_list)
        else:
            # Fallback: same brand + nearby popular models is better than nothing
            queries.append(f"{brand} {model}".strip())
            queries.append(model)

    # Deduplicate queries while preserving order
    seen_q, uniq_queries = set(), []
    for q in queries:
        q = clean(q)
        if q and q.lower() not in seen_q:
            seen_q.add(q.lower())
            uniq_queries.append(q)

    candidates, errors = [], []
    for q in uniq_queries:
        try:
            candidates.extend(fetch_search(q, 50))
        except Exception as e:
            errors.append(str(e))
        # Early exit if we already have plenty of priced ads
        priced = [a for a in candidates if a.get("price") is not None]
        if len(priced) >= 80:
            break

    out, seen = [], set()
    model_l = model.lower()
    model_parts = [p for p in re.split(r"\s+", model_l) if len(p) >= 2]

    for a in candidates:
        url = a.get("url") or ""
        if not url or url in seen or a.get("price") is None:
            continue
        if source and url.rstrip("/") == source.rstrip("/"):
            continue
        p = a["price"]
        if p < 30 or p > 1500:
            continue

        # Price band according to selected mode (slightly wider to avoid empty results)
        if mode == "same" and not (target * 0.92 <= p <= target * 1.08):
            continue
        if mode == "lower" and not (target * 0.85 <= p <= target * 1.02):
            continue
        if mode == "higher" and not (target * 0.98 <= p <= target * 1.15):
            continue
        if mode == "both" and not (target * 0.80 <= p <= target * 1.20):
            continue

        title_l = (a.get("title") or "").lower()
        if typ == "same":
            # Require model match (flexible for "207", "Clio 5", etc.)
            if model_l not in title_l:
                if not model_parts or not any(part in title_l for part in model_parts):
                    continue
        else:
            # Class mode: accept any of the class models, or same model
            key = _model_key(model)
            allowed = CLASS_MAP.get(key, [model])
            ok_class = any(norm(x).lower().split()[0] in title_l for x in allowed if x)
            if not ok_class and model_l not in title_l:
                if not model_parts or not any(part in title_l for part in model_parts):
                    continue

        seen.add(url)
        out.append(a)

    out.sort(key=lambda a: abs(a["price"] - target))
    return jsonify({
        "ok": True,
        "source": "Ouedkniss",
        "ads": out[:20],
        "errors": errors,
        "queries_used": uniq_queries[:6],
        "mode": mode,
        "type": typ,
    })


@app.get("/api/search")
def api_search():
    did = _device_id(request)
    if not _has_paid_access(did):
        return jsonify({
            "ok": False,
            "error": "البحث عن السيارة متاح بعد الاشتراك Premium أو شراء تحليل.",
            "ads": [],
            "need_premium": True,
        }), 402
    q = clean(request.args.get("q"))
    min_p = request.args.get("min_price") or request.args.get("min")
    max_p = request.args.get("max_price") or request.args.get("max")
    region_id = request.args.get("region") or request.args.get("wilaya") or ""
    fuel = clean(request.args.get("fuel") or "")
    try:
        min_price = float(min_p) if min_p not in (None, "") else None
    except ValueError:
        min_price = None
    try:
        max_price = float(max_p) if max_p not in (None, "") else None
    except ValueError:
        max_price = None

    if not q and min_price is None and max_price is None and not region_id and not fuel:
        return jsonify({"ok": False, "error": "أدخل اسم السيارة أو نطاق السعر أو الولاية على الأقل", "ads": []}), 400
    if min_price is not None and max_price is not None and min_price > max_price:
        return jsonify({"ok": False, "error": "الحد الأدنى للسعر أكبر من الحد الأقصى", "ads": []}), 400

    try:
        ads = fetch_search(
            q or "",
            limit=60,
            min_price=min_price,
            max_price=max_price,
            region_id=region_id or None,
            fuel=fuel or None,
        )
        if min_price is not None and max_price is not None:
            mid = (min_price + max_price) / 2
            ads.sort(key=lambda a: abs((a.get("price") or mid) - mid))
        else:
            ads.sort(key=lambda a: a.get("price") or 0)
        return jsonify({
            "ok": True,
            "source": "Ouedkniss",
            "query": q,
            "min_price": min_price,
            "max_price": max_price,
            "region": int(region_id) if str(region_id).isdigit() else None,
            "fuel": fuel or None,
            "count": len(ads),
            "ads": ads,
        })
    except requests.RequestException:
        return jsonify({"ok": False, "error": "تعذر الوصول إلى Ouedkniss", "ads": []}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "ads": []}), 500



# ===================== Freemium / Premium =====================
import os, hashlib, secrets, hmac
from datetime import datetime, timedelta
from pathlib import Path as _Path

DATA_DIR = _Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)
CODES_FILE = DATA_DIR / "premium_codes.json"
USAGE_FILE = DATA_DIR / "usage.json"

PRICES = {
    "single": {"dzd": 150, "label_ar": "تحليل واحد", "label_en": "Single analysis", "days": 0},
    "weekly": {"dzd": 300, "label_ar": "أسبوعي", "label_en": "Weekly", "days": 7},
    "monthly": {"dzd": 900, "label_ar": "شهري", "label_en": "Monthly", "days": 30},
}
FREE_DAILY_LIMIT = 2
FREE_ADS_LIMIT = 6
PREMIUM_ADS_LIMIT = 20


def _load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _today():
    return datetime.utcnow().strftime("%Y-%m-%d")


def _device_id(req):
    did = (req.headers.get("X-Device-Id") or "").strip()
    if did and len(did) <= 80:
        return did
    raw = f"{req.remote_addr}|{req.headers.get('User-Agent','')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _is_premium(device_id):
    codes = _load_json(CODES_FILE, {"codes": {}})
    now = datetime.utcnow()
    for code, info in codes.get("codes", {}).items():
        if info.get("device_id") != device_id:
            continue
        if info.get("plan") == "single":
            continue
        exp = info.get("expires")
        if exp:
            try:
                if datetime.fromisoformat(exp) > now:
                    return True, info
            except Exception:
                pass
    return False, None


def _single_credits(device_id):
    usage = _load_json(USAGE_FILE, {})
    u = usage.get(device_id, {})
    return int(u.get("single_credits", 0))


def _consume_single_credit(device_id):
    usage = _load_json(USAGE_FILE, {})
    u = usage.get(device_id, {})
    c = int(u.get("single_credits", 0))
    if c <= 0:
        return False
    u["single_credits"] = c - 1
    usage[device_id] = u
    _save_json(USAGE_FILE, usage)
    return True



def _features_until(device_id):
    usage = _load_json(USAGE_FILE, {})
    u = usage.get(device_id, {})
    exp = u.get("features_until")
    if not exp:
        return None
    try:
        if datetime.fromisoformat(exp) > datetime.utcnow():
            return exp
    except Exception:
        pass
    return None


def _has_paid_access(device_id):
    """Premium subscription, remaining single credits, or features_until window."""
    prem, _ = _is_premium(device_id)
    if prem:
        return True
    if _single_credits(device_id) > 0:
        return True
    if _features_until(device_id):
        return True
    return False


def _daily_usage(device_id):
    usage = _load_json(USAGE_FILE, {})
    u = usage.get(device_id, {})
    if u.get("day") != _today():
        return 0
    return int(u.get("analyses", 0))


def _inc_daily_usage(device_id):
    usage = _load_json(USAGE_FILE, {})
    u = usage.get(device_id, {})
    credits = int(u.get("single_credits", 0))
    if u.get("day") != _today():
        u = {"day": _today(), "analyses": 0, "single_credits": credits}
    u["analyses"] = int(u.get("analyses", 0)) + 1
    u["single_credits"] = credits
    usage[device_id] = u
    _save_json(USAGE_FILE, usage)
    return u["analyses"]


@app.get("/api/plan")
def api_plan():
    did = _device_id(request)
    prem, info = _is_premium(did)
    used = _daily_usage(did)
    credits = _single_credits(did)
    features_until = _features_until(did)
    # Auto-unlock features for users who already have paid credits but no features_until
    if credits > 0 and not features_until:
        usage = _load_json(USAGE_FILE, {})
        u = usage.get(did, {})
        u["features_until"] = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        usage[did] = u
        _save_json(USAGE_FILE, usage)
        features_until = u["features_until"]
    paid = prem or credits > 0 or bool(features_until)
    return jsonify({
        "ok": True,
        "device_id": did,
        "premium": prem,
        "paid_access": paid,
        "plan": (info or {}).get("plan") if prem else ("single_credit" if credits else ("paid_features" if features_until else "free")),
        "expires": (info or {}).get("expires") if prem else features_until,
        "features_until": features_until,
        "single_credits": credits,
        "daily_used": used,
        "daily_limit": FREE_DAILY_LIMIT,
        "remaining_free": max(0, FREE_DAILY_LIMIT - used) if not paid else None,
        "prices": PRICES,
        "free_ads_limit": FREE_ADS_LIMIT,
        "premium_ads_limit": PREMIUM_ADS_LIMIT,
    })


@app.post("/api/consume_analysis")
def api_consume_analysis():
    did = _device_id(request)
    prem, info = _is_premium(did)
    if prem:
        return jsonify({
            "ok": True, "allowed": True, "tier": "premium",
            "ads_limit": PREMIUM_ADS_LIMIT,
            "features": ["alternatives", "full_report", "anomalies", "deep_compare"],
        })
    if _single_credits(did) > 0:
        _consume_single_credit(did)
        return jsonify({
            "ok": True, "allowed": True, "tier": "single",
            "ads_limit": PREMIUM_ADS_LIMIT,
            "features": ["alternatives", "full_report", "anomalies", "deep_compare"],
            "single_credits_left": _single_credits(did),
        })
    used = _daily_usage(did)
    if used >= FREE_DAILY_LIMIT:
        return jsonify({
            "ok": True, "allowed": False, "tier": "free",
            "error": "استهلكت التحليلين المجانيين لليوم. ترقَّ إلى Premium أو اشترِ تحليلاً واحداً (150 دج).",
            "daily_used": used, "daily_limit": FREE_DAILY_LIMIT,
            "prices": PRICES,
        }), 402
    _inc_daily_usage(did)
    return jsonify({
        "ok": True, "allowed": True, "tier": "free",
        "ads_limit": FREE_ADS_LIMIT,
        "features": ["basic"],
        "daily_used": used + 1,
        "daily_limit": FREE_DAILY_LIMIT,
        "remaining_free": max(0, FREE_DAILY_LIMIT - used - 1),
    })


@app.post("/api/activate")
def api_activate():
    data = request.get_json(silent=True) or {}
    code = clean(data.get("code")).upper().replace(" ", "")
    did = _device_id(request)
    if not code:
        return jsonify({"ok": False, "error": "أدخل رمز التفعيل"}), 400
    codes = _load_json(CODES_FILE, {"codes": {}})
    info = codes.get("codes", {}).get(code)
    if not info:
        return jsonify({"ok": False, "error": "رمز غير صالح"}), 404
    if info.get("used") and info.get("device_id") and info.get("device_id") != did:
        return jsonify({"ok": False, "error": "هذا الرمز مستخدم مسبقاً"}), 400
    plan = info.get("plan", "monthly")
    if plan == "single":
        usage = _load_json(USAGE_FILE, {})
        u = usage.get(did, {})
        u["single_credits"] = int(u.get("single_credits", 0)) + int(info.get("credits", 1))
        u["features_until"] = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        if u.get("day") != _today():
            u["day"] = _today()
            u["analyses"] = 0
        usage[did] = u
        _save_json(USAGE_FILE, usage)
        info["used"] = True
        info["device_id"] = did
        info["used_at"] = datetime.utcnow().isoformat()
        codes["codes"][code] = info
        _save_json(CODES_FILE, codes)
        return jsonify({"ok": True, "plan": "single", "single_credits": u["single_credits"], "features_until": u["features_until"]})
    days = int(info.get("days") or PRICES.get(plan, {}).get("days") or 30)
    expires = (datetime.utcnow() + timedelta(days=days)).isoformat()
    info["used"] = True
    info["device_id"] = did
    info["expires"] = expires
    info["used_at"] = datetime.utcnow().isoformat()
    codes["codes"][code] = info
    _save_json(CODES_FILE, codes)
    return jsonify({"ok": True, "plan": plan, "expires": expires, "premium": True})


@app.post("/api/admin/generate_code")
def api_generate_code():
    data = request.get_json(silent=True) or {}
    admin_key = clean(data.get("admin_key") or request.headers.get("X-Admin-Key"))
    expected = os.environ.get("FAHES_ADMIN_KEY") or os.environ.get("FAHAS_ADMIN_KEY", "fahes-admin")
    if admin_key != expected:
        return jsonify({"ok": False, "error": "غير مصرح"}), 403
    plan = clean(data.get("plan") or "monthly").lower()
    if plan not in PRICES:
        return jsonify({"ok": False, "error": "خطة غير معروفة (single/weekly/monthly)"}), 400
    n = min(int(data.get("count") or 1), 50)
    codes = _load_json(CODES_FILE, {"codes": {}})
    created = []
    for _ in range(n):
        code = "FH-" + secrets.token_hex(3).upper() + "-" + secrets.token_hex(2).upper()
        codes.setdefault("codes", {})[code] = {
            "plan": plan,
            "days": PRICES[plan]["days"],
            "credits": 1 if plan == "single" else 0,
            "dzd": PRICES[plan]["dzd"],
            "created_at": datetime.utcnow().isoformat(),
            "used": False,
        }
        created.append(code)
    _save_json(CODES_FILE, codes)
    return jsonify({"ok": True, "plan": plan, "codes": created, "price_dzd": PRICES[plan]["dzd"]})


@app.get("/api/pricing")
def api_pricing():
    return jsonify({
        "ok": True,
        "currency": "DZD",
        "plans": [
            {"id": "single", "price": 150, "label_ar": "تحليل واحد كامل", "label_en": "One full analysis"},
            {"id": "weekly", "price": 300, "label_ar": "اشتراك أسبوعي", "label_en": "Weekly"},
            {"id": "monthly", "price": 900, "label_ar": "اشتراك شهري", "label_en": "Monthly"},
        ],
        "free": {
            "daily_analyses": FREE_DAILY_LIMIT,
            "search": True,
            "basic_info": True,
            "ads_per_analysis": FREE_ADS_LIMIT,
        },
        "premium_features": [
            "تحليل عدد أكبر من الإعلانات",
            "مقارنة السعر مع سيارات أكثر",
            "البحث عن البدائل",
            "كشف التناقضات والمعلومات غير الطبيعية",
            "تقرير كامل قابل للحفظ",
        ],
        "payment_note_ar": "بعد الدفع عبر بريدي موب أو تحويل بنكي، احصل على رمز التفعيل من الدعم ثم أدخله هنا.",
    })



# ===================== Chargily Pay =====================
CHARGILY_SECRET = os.environ.get("CHARGILY_SECRET_KEY", "").strip()
CHARGILY_MODE = (os.environ.get("CHARGILY_MODE", "test") or "test").lower()
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:5050") or "http://127.0.0.1:5050").rstrip("/")

def _chargily_base():
    if CHARGILY_MODE == "live":
        return "https://pay.chargily.net/api/v2"
    return "https://pay.chargily.net/test/api/v2"


def _chargily_configured():
    return bool(CHARGILY_SECRET)


def _grant_plan_to_device(device_id, plan):
    """Activate plan for a device after successful payment."""
    plan = (plan or "monthly").lower()
    if plan not in PRICES:
        plan = "monthly"
    if plan == "single":
        usage = _load_json(USAGE_FILE, {})
        u = usage.get(device_id, {})
        u["single_credits"] = int(u.get("single_credits", 0)) + 1
        # Unlock paid features (alternatives + car search) for 7 days even after credit is used
        u["features_until"] = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        if u.get("day") != _today():
            u["day"] = _today()
            u["analyses"] = 0
        usage[device_id] = u
        _save_json(USAGE_FILE, usage)
        return {"plan": "single", "single_credits": u["single_credits"], "features_until": u["features_until"]}
    days = int(PRICES[plan]["days"] or 30)
    expires = (datetime.utcnow() + timedelta(days=days)).isoformat()
    codes = _load_json(CODES_FILE, {"codes": {}})
    # synthetic code bound to payment
    code = "PAY-" + secrets.token_hex(4).upper()
    codes.setdefault("codes", {})[code] = {
        "plan": plan,
        "days": days,
        "credits": 0,
        "dzd": PRICES[plan]["dzd"],
        "created_at": datetime.utcnow().isoformat(),
        "used": True,
        "device_id": device_id,
        "expires": expires,
        "used_at": datetime.utcnow().isoformat(),
        "source": "chargily",
    }
    _save_json(CODES_FILE, codes)
    return {"plan": plan, "expires": expires, "premium": True}


@app.post("/api/pay/create")
def pay_create():
    """Create a Chargily checkout and return checkout_url."""
    if not _chargily_configured():
        return jsonify({
            "ok": False,
            "error": "Chargily غير مضبوط. ضع CHARGILY_SECRET_KEY في متغيرات البيئة.",
            "fallback": "activation_code",
        }), 503
    data = request.get_json(silent=True) or {}
    plan = clean(data.get("plan") or "monthly").lower()
    if plan not in PRICES:
        return jsonify({"ok": False, "error": "خطة غير معروفة"}), 400
    did = _device_id(request)
    amount = int(PRICES[plan]["dzd"])
    base = PUBLIC_BASE_URL.rstrip("/")
    # Chargily requires a public https URL for webhooks (not localhost)
    webhook = f"{base}/api/pay/webhook"
    use_webhook = base.startswith("https://") and "127.0.0.1" not in base and "localhost" not in base
    payload = {
        "amount": amount,
        "currency": "dzd",
        "locale": "ar",
        "description": f"FaheS {plan} — {amount} DZD",
        "success_url": f"{base}/pay/success?plan={plan}",
        "failure_url": f"{base}/pay/failure",
        "metadata": {
            "device_id": did,
            "plan": plan,
            "app": "FaheS",
        },
    }
    if use_webhook:
        payload["webhook_endpoint"] = webhook
    try:
        r = requests.post(
            f"{_chargily_base()}/checkouts",
            headers={
                "Authorization": f"Bearer {CHARGILY_SECRET}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        body = r.json() if r.content else {}
        if r.status_code >= 400:
            return jsonify({
                "ok": False,
                "error": body.get("message") or body.get("error") or f"Chargily HTTP {r.status_code}",
                "detail": body,
            }), 502
        checkout_url = body.get("checkout_url")
        if not checkout_url:
            return jsonify({"ok": False, "error": "لم يُرجع Chargily رابط دفع", "detail": body}), 502
        # store pending checkout
        pending = _load_json(DATA_DIR / "pending_checkouts.json", {})
        pending[body.get("id") or checkout_url] = {
            "device_id": did,
            "plan": plan,
            "amount": amount,
            "created_at": datetime.utcnow().isoformat(),
        }
        _save_json(DATA_DIR / "pending_checkouts.json", pending)
        return jsonify({
            "ok": True,
            "checkout_url": checkout_url,
            "checkout_id": body.get("id"),
            "amount": amount,
            "plan": plan,
            "mode": CHARGILY_MODE,
        })
    except requests.RequestException as e:
        return jsonify({"ok": False, "error": f"تعذر الاتصال بـ Chargily: {e}"}), 502


@app.post("/api/pay/webhook")
def pay_webhook():
    """Chargily webhook — verify HMAC signature and grant plan."""
    signature = request.headers.get("signature") or request.headers.get("Signature") or ""
    payload = request.get_data(as_text=True) or ""
    if not CHARGILY_SECRET:
        return jsonify({"ok": False}), 503
    if not signature:
        return jsonify({"ok": False, "error": "missing signature"}), 400
    computed = hmac.new(
        CHARGILY_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, computed):
        return jsonify({"ok": False, "error": "invalid signature"}), 403
    try:
        event = json.loads(payload)
    except Exception:
        return jsonify({"ok": False, "error": "bad json"}), 400
    etype = event.get("type") or ""
    data = event.get("data") or {}
    # status paid / checkout.paid
    status = (data.get("status") or "").lower()
    if etype in ("checkout.paid", "checkout.completed") or status == "paid":
        meta = data.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        device_id = meta.get("device_id") or ""
        plan = meta.get("plan") or "monthly"
        if not device_id:
            # try pending map
            cid = data.get("id")
            pending = _load_json(DATA_DIR / "pending_checkouts.json", {})
            info = pending.get(cid) or {}
            device_id = info.get("device_id") or ""
            plan = info.get("plan") or plan
        if device_id:
            result = _grant_plan_to_device(device_id, plan)
            # log
            logs = _load_json(DATA_DIR / "payment_log.json", {"events": []})
            logs.setdefault("events", []).append({
                "at": datetime.utcnow().isoformat(),
                "type": etype,
                "status": status,
                "device_id": device_id,
                "plan": plan,
                "checkout_id": data.get("id"),
                "amount": data.get("amount"),
                "result": result,
            })
            _save_json(DATA_DIR / "payment_log.json", logs)
    return jsonify({"ok": True})


@app.get("/pay/success")
def pay_success_page():
    plan = clean(request.args.get("plan") or "")
    html = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>تم الدفع — FaheS</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font-family:Cairo,Arial,sans-serif;background:#07110d;color:#edf6f0;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
.card{{background:#112019;border:1px solid #294337;border-radius:18px;padding:32px;max-width:420px;text-align:center}}
a{{color:#c9d83b;font-weight:700}}</style></head><body>
<div class="card">
<h1>✅ تم الدفع بنجاح</h1>
<p>الخطة: <b>{plan or "Premium"}</b></p>
<p>إذا لم يُفعَّل الاشتراك خلال ثوانٍ، حدّث الصفحة الرئيسية — التفعيل يتم عبر إشعار Chargily.</p>
<p><a href="/">العودة إلى FaheS</a></p>
</div>
<script>try{{localStorage.setItem("fahas_pay_ok","1")}}catch(e){{}}</script>
</body></html>"""
    return html


@app.get("/pay/failure")
def pay_failure_page():
    html = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>فشل الدفع — FaheS</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:Cairo,Arial,sans-serif;background:#07110d;color:#edf6f0;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
.card{background:#112019;border:1px solid #294337;border-radius:18px;padding:32px;max-width:420px;text-align:center}
a{color:#c9d83b;font-weight:700}</style></head><body>
<div class="card">
<h1>⚠️ لم يكتمل الدفع</h1>
<p>يمكنك المحاولة مجدداً من صفحة Premium.</p>
<p><a href="/">العودة إلى FaheS</a></p>
</div></body></html>"""
    return html


@app.get("/api/pay/status")
def pay_status():
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    public_ok = base.startswith("https://") and "127.0.0.1" not in base and "localhost" not in base
    return jsonify({
        "ok": True,
        "configured": _chargily_configured(),
        "mode": CHARGILY_MODE,
        "public_base_url": PUBLIC_BASE_URL,
        "public_url_ok_for_webhook": public_ok,
        "secret_prefix": (CHARGILY_SECRET[:12] + "...") if CHARGILY_SECRET else None,
        "hint_ar": (
            "المفتاح غير مضبوط" if not CHARGILY_SECRET else
            ("PUBLIC_BASE_URL يجب أن يكون رابط ngrok https" if not public_ok else
             "الإعداد يبدو جاهزاً — جرّب ادفع الآن")
        ),
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
