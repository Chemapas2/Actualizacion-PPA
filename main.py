from __future__ import annotations

import io
import json
import os
import hashlib
import zipfile
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import fitz  # PyMuPDF
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None


st.set_page_config(
    page_title="PPA España - Comité de Dirección",
    page_icon="🐗",
    layout="wide",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

CACHE_TTL_SECONDS = 15 * 60  # Consulta cada 15 minutos; actualización manual disponible
APP_VERSION = "3.1 · 30/10/2026 · recuperación y carga PDF"
MAPA_NEWS_URL = (
    "https://www.mapa.gob.es/es/ganaderia/temas/"
    "sanidad-animal-higiene-ganadera/sanidad-animal"
)
MAPA_DISEASE_URL = (
    "https://www.mapa.gob.es/es/ganaderia/temas/"
    "sanidad-animal-higiene-ganadera/sanidad-animal/"
    "enfermedades/peste-porcina-africana/peste_porcina_africana"
)
MERCOLLEIDA_HOME_URL = "https://www.mercolleida.com/"
MERCOLLEIDA_CERDO_URL = (
    "https://www.mercolleida.com/index.php/es/servicios/mercados/"
    "porcino/cerdo-cebado/mercolleida"
)
THREETHREE_PRICES_URL = "https://www.3tres3.com/cotizaciones-de-porcino/"

SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

SPANISH_NUMBER_WORDS = {
    "cero": 0, "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "dieciséis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiuno": 21, "veintiun": 21, "veintiuna": 21, "veintidos": 22,
    "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
    "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
}

# Los comunicados alternan cifras ("5") y palabras ("cinco"). Este patrón admite
# ambas, así como formas del tipo "treinta y dos".
NUMBER_TOKEN_PATTERN = r"(?:[\d\.]+|[A-Za-záéíóúñ]+(?:\s+y\s+[A-Za-záéíóúñ]+)?)"
SPANISH_TENS = {
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
}

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data_ppa"
PDF_DIR = DATA_DIR / "pdf"
SNAPSHOT_FILE = DATA_DIR / "ultima_lectura_verificada.json"
MARKET_FILE = DATA_DIR / "ultimo_mercado_verificado.json"

# Al desplegar en Streamlit Community Cloud el disco puede ser efímero.
# Descargar una copia de seguridad ZIP permite restaurar las últimas lecturas.


def save_json_atomic(path: Path, payload: dict) -> bool:
    """Guarda sólo datos de fuentes verificadas. Fallos de disco no paran la app."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return True
    except OSError:
        return False


def read_snapshot() -> dict | None:
    try:
        data = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        if data.get("version") == 2 and isinstance(data.get("parsed_notes"), list) and data["parsed_notes"]:
            return data
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def deserialize_dates(record: dict, keys=("report_date", "previous_note_date")) -> dict:
    result = dict(record)
    for key in keys:
        if isinstance(result.get(key), str):
            try:
                result[key] = date.fromisoformat(result[key])
            except ValueError:
                result[key] = None
    return result


def serialize_record(record: dict) -> dict:
    return {key: value.isoformat() if isinstance(value, (date, datetime)) else value
            for key, value in record.items() if key not in ("pdf_bytes", "full_text", "previous_comparable_note")}


def store_verified_snapshot(notes: list[dict], parsed_notes: list[dict], source_checked_utc: str):
    """Persistencia de la última lectura con cifras reales, sin inventar valores."""
    if not parsed_notes or not any(
        n.get(k) is not None for n in parsed_notes for k in ("total_foci", "total_positives", "negatives_total", "farms")
    ):
        return False
    payload = {
        "version": 2,
        "last_success_utc": source_checked_utc,
        "notes": [serialize_record(n) for n in notes[:12]],
        "parsed_notes": [serialize_record(n) for n in parsed_notes[:12]],
    }
    return save_json_atomic(SNAPSHOT_FILE, payload)


def restore_snapshot(snapshot: dict) -> tuple[list[dict], list[dict]]:
    notes = [deserialize_dates(n) for n in snapshot["notes"]]
    parsed_notes = [deserialize_dates(n) for n in snapshot["parsed_notes"]]
    for n in parsed_notes:
        cached = pdf_cache_path(n.get("pdf_url", ""))
        n["pdf_bytes"] = cached.read_bytes() if cached.exists() else b""
    return notes, parsed_notes


def pdf_cache_path(url: str) -> Path:
    return PDF_DIR / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".pdf")


def backup_zip() -> bytes | None:
    if not SNAPSHOT_FILE.exists():
        return None
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(SNAPSHOT_FILE, "ultima_lectura_verificada.json")
        if MARKET_FILE.exists():
            z.write(MARKET_FILE, "ultimo_mercado_verificado.json")
        if PDF_DIR.exists():
            for entry in PDF_DIR.glob("*.pdf"):
                if entry.stat().st_size <= 20_000_000:
                    z.write(entry, f"pdf/{entry.name}")
    return output.getvalue()


def restore_backup_zip(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = set(z.namelist())
            if "ultima_lectura_verificada.json" not in names:
                return False
            raw = z.read("ultima_lectura_verificada.json")
            snapshot = json.loads(raw)
            if snapshot.get("version") != 2 or not snapshot.get("parsed_notes"):
                return False
            # No se extraen rutas arbitrarias; sólo PDFs con nombre SHA-256.
            if not save_json_atomic(SNAPSHOT_FILE, snapshot):
                return False
            if "ultimo_mercado_verificado.json" in names:
                try:
                    market = json.loads(z.read("ultimo_mercado_verificado.json"))
                    if market.get("version") == 1 and isinstance(market.get("snapshot"), dict):
                        save_json_atomic(MARKET_FILE, market)
                except (ValueError, TypeError):
                    pass
            PDF_DIR.mkdir(parents=True, exist_ok=True)
            for name in names:
                if re.fullmatch(r"pdf/[0-9a-f]{64}\.pdf", name):
                    info = z.getinfo(name)
                    if info.file_size <= 20_000_000:
                        b = z.read(name)
                        if b.startswith(b"%PDF-"):
                            (PDF_DIR / Path(name).name).write_bytes(b)
            return True
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return False



def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def strip_accents(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def parse_date_generic(value: str) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_spanish_textual_date(value: str) -> date | None:
    value = normalize_text(value).lower()
    match = re.search(r"(\d{1,2}) de ([a-záéíóúñ]+) de (\d{4})", value)
    if not match:
        return None
    day = int(match.group(1))
    month_name = strip_accents(match.group(2)).lower()
    year = int(match.group(3))
    month = SPANISH_MONTHS.get(month_name)
    if not month:
        return None
    return date(year, month, day)


def parse_spanish_number(token: str | None) -> int | None:
    if token is None:
        return None
    token = normalize_text(token).strip(" .,:;()[]{}")
    if not token:
        return None
    if re.fullmatch(r"[\d\.]+", token):
        return int(token.replace(".", ""))
    normalized = strip_accents(token.lower())
    direct = SPANISH_NUMBER_WORDS.get(normalized)
    if direct is not None:
        return direct
    tens_match = re.fullmatch(r"(treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa) y (\w+)", normalized)
    if tens_match:
        unit = SPANISH_NUMBER_WORDS.get(tens_match.group(2))
        if unit is not None and 0 <= unit <= 9:
            return SPANISH_TENS[tens_match.group(1)] + unit
    return None


def int_from_match(pattern: str, text: str, group: int = 1, flags: int = re.IGNORECASE) -> int | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    return parse_spanish_number(match.group(group))


def int_from_patterns(patterns: tuple[str, ...], text: str) -> int | None:
    """Devuelve la primera cifra válida extraída por una lista de redacciones."""
    for pattern in patterns:
        value = int_from_match(pattern, text)
        if value is not None:
            return value
    return None


def extract_positive_counts(full_text: str) -> tuple[int | None, int | None]:
    """Extrae positivos nuevos y acumulados tolerando cambios de estilo del MAPA."""
    number = NUMBER_TOKEN_PATTERN

    # La frase de nuevos casos suele estar en el primer párrafo y puede expresar
    # la cantidad con letras: "un nuevo foco, que incluye ... cinco casos".
    new_positives = int_from_patterns(
        (
            rf"(?:nuevo|nuevos)\s+focos?.{{0,180}}?incluye(?:n)?\s+(?:un\s+)?total\s+de\s+({number})\s+(?:nuevos?\s+)?casos\b",
            rf"(?:han\s+notificado|se\s+ha\s+notificado).{{0,180}}?(?:un\s+)?total\s+de\s+({number})\s+(?:nuevos?\s+)?casos\b",
            rf"detecci[oó]n\s+de\s+({number})\s+nuevos?\s+casos\s+positivos",
            rf"({number})\s+nuevos?\s+casos\s+positivos",
        ),
        full_text,
    )
    if new_positives is None and re.search(
        r"no\s+se\s+han?\s+detectado\s+nuevos?\s+casos\s+positivos",
        full_text,
        re.IGNORECASE,
    ):
        new_positives = 0

    # El acumulado ha aparecido como "incluyen un total" y, desde septiembre,
    # como "lo que suma un total". Se contemplan otros verbos equivalentes.
    total_positives = int_from_patterns(
        (
            rf"(?:lo\s+que\s+)?(?:suma|suman|incluye|incluyen|alcanza|alcanzan|supone|suponen|representa|representan)\s+(?:un\s+)?total\s+de\s+({number})\s+(?:casos\s*/\s*jabal[ií]es|casos|jabal[ií]es|animales)\s+positivos",
            rf"total\s+(?:acumulado\s+)?de\s+({number})\s+(?:casos\s*/\s*jabal[ií]es|casos|jabal[ií]es|animales)\s+positivos",
            rf"({number})\s+(?:casos\s*/\s*jabal[ií]es|casos|jabal[ií]es|animales)\s+positivos\s+(?:acumulados|en\s+total)",
        ),
        full_text,
    )
    return new_positives, total_positives


def make_session() -> requests.Session:
    """HTTPS verificado y reintentos. No se desactiva SSL en ningún caso."""
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=2, connect=2, read=1, backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    # Para certificados de inspección corporativa, instalar su CA de confianza
    # y configurar REQUESTS_CA_BUNDLE o SSL_CERT_FILE en el entorno.
    bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if bundle and Path(bundle).is_file():
        session.verify = bundle
    return session


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_text(url: str) -> str:
    response = make_session().get(url, timeout=(7, 12))
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_bytes(url: str) -> bytes:
    cached = pdf_cache_path(url)
    if cached.exists():
        data = cached.read_bytes()
        if data.startswith(b"%PDF-"):
            return data
    try:
        response = make_session().get(url, timeout=(7, 25))
        response.raise_for_status()
        data = response.content
        if not data.startswith(b"%PDF-"):
            raise ValueError("La dirección no devuelve un PDF válido.")
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
        except OSError:
            pass
        return data
    except Exception:
        if cached.exists():
            data = cached.read_bytes()
            if data.startswith(b"%PDF-"):
                return data
        raise


def load_local_image(filename: str) -> Image.Image | None:
    candidates = [
        BASE_DIR / filename,
        Path.cwd() / filename,
        BASE_DIR / "assets" / filename,
        Path.cwd() / "assets" / filename,
    ]
    for candidate in candidates:
        try:
            if candidate.exists():
                return Image.open(candidate)
        except Exception:
            continue
    return None


def render_brand_header() -> None:
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.0rem;}
        .nutreco-title {font-size: 2.35rem; font-weight: 800; color: #203864; margin: 0.1rem 0 0.15rem 0;}
        .nutreco-subtitle {font-size: 1.0rem; color: #4b5563; margin-bottom: 0.2rem;}
        .brand-note {font-size: 0.85rem; color: #6b7280; margin-top: 0.15rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    logo_nutreco = load_local_image("Logo Nutreco.jpg")
    logo_techteam = load_local_image("Logo TechTeam 2.jpg")
    ribbon = load_local_image("Solapa rosa trasparente.png") or load_local_image("Solapa rosa.jpg")

    left, right = st.columns([5, 1.4])
    with left:
        if logo_nutreco is not None:
            st.image(logo_nutreco, width=300)
        else:
            st.markdown("<div class='nutreco-title'>Nutreco</div>", unsafe_allow_html=True)
        st.markdown("<div class='nutreco-title'>Actualización automática PPA España</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='nutreco-subtitle'>Comité de Dirección · cifras clave, mapas oficiales, medidas administrativas y mercado porcino</div>",
            unsafe_allow_html=True,
        )
    with right:
        if logo_techteam is not None:
            st.image(logo_techteam, width=95)
            st.markdown("<div class='brand-note'>TechTeam</div>", unsafe_allow_html=True)

    if ribbon is not None:
        st.image(ribbon, use_container_width=True)
    st.caption("Panel corporativo Nutreco · actualización automática desde fuentes oficiales y mercado.")
    st.caption(f"**VERSIÓN {APP_VERSION}** · Si esta identificación no aparece en pantalla, GitHub/Streamlit está ejecutando otro archivo o una versión anterior.")


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def get_mapa_notes() -> tuple[list[dict[str, Any]], str]:
    """Prueba dos apartados oficiales; en caso de fallo la UI usa el snapshot."""
    records: dict[str, dict[str, Any]] = {}
    errors = []
    for landing in (MAPA_DISEASE_URL, MAPA_NEWS_URL):
        try:
            page_html = fetch_text(landing)
            soup = BeautifulSoup(page_html, "html.parser")
            for anchor in soup.find_all("a", href=True):
                title = normalize_text(anchor.get_text(" ", strip=True))
                normalized = strip_accents(title).lower()
                if "peste porcina africana" not in normalized or "catalu" not in normalized:
                    continue
                date_match = re.search(r"\((\d{1,2}[./]\d{1,2}[./]\d{4})\)", title)
                report_date = parse_date_generic(date_match.group(1)) if date_match else None
                href = urljoin(landing, anchor["href"])
                parsed = urlparse(href)
                if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith("mapa.gob.es"):
                    continue
                if not parsed.path.lower().endswith(".pdf") or report_date is None:
                    continue
                records[href] = {"title": title, "url": href, "report_date": report_date}
        except Exception as exc:
            errors.append(f"{landing}: {exc}")
            # Ambos destinos del MAPA comparten dominio/certificado. Repetir la
            # conexión en caso de error de confianza TLS no solucionaría nada.
            if any(tag in str(exc) for tag in
                   ("CERTIFICATE_VERIFY_FAILED", "certificate verify failed", "SSLError")):
                break
    if records:
        return (sorted(records.values(), key=lambda n: n["report_date"], reverse=True),
                datetime.now(timezone.utc).isoformat())
    if errors:
        raise RuntimeError("; ".join(errors))
    raise RuntimeError("Las páginas oficiales no contienen enlaces a notas PDF identificables de PPA en Cataluña.")


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def extract_pdf_text(pdf_bytes: bytes, max_pages: int | None = None) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page_count = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
        text_parts = [doc.load_page(i).get_text("text") for i in range(page_count)]
    return "\n".join(text_parts)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def render_pdf_page(pdf_bytes: bytes, page_number: int, zoom: float = 1.7) -> Image.Image:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc.load_page(page_number)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return Image.open(io.BytesIO(pixmap.tobytes("png")))


def split_municipalities(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    cleaned = raw_value.strip().strip(". ")
    cleaned = re.sub(r",\s+y\s+", ", ", cleaned)
    cleaned = cleaned.replace(" y ", ", ")
    return [part.strip(" .") for part in cleaned.split(",") if part.strip(" .")]


def extract_measures(text: str, farms: int | None) -> list[str]:
    text_lower = strip_accents(text.lower())
    measures: list[str] = []

    if "busqueda de cadaveres de jabalies" in text_lower:
        measures.append("Búsqueda intensiva de cadáveres de jabalíes y vigilancia reforzada en la zona restringida y su periferia.")
    if "reduccion de las poblaciones de jabalies" in text_lower or "control poblacional" in text_lower:
        measures.append("Reducción poblacional de jabalíes mediante trampas de captura y control por agentes rurales/cazadores específicamente formados.")
    if "reforzado los vallados" in text_lower or "medidas de aislamiento" in text_lower:
        measures.append("Refuerzo de vallados, barreras y medidas de aislamiento, priorizando corredores de paso de jabalíes.")
    if "medidas de bioseguridad" in text_lower or "vigilancia pasiva reforzada" in text_lower:
        farm_text = f" en {farms} explotaciones comerciales" if farms else " en las explotaciones de porcino"
        measures.append(f"Inspección de bioseguridad y vigilancia pasiva reforzada{farm_text}.")
    if "alto nivel de alerta" in text_lower:
        measures.append("Mantenimiento de un alto nivel de alerta en Cataluña y en el resto de España.")
    if "obligacion de comunicar" in text_lower:
        measures.append("Recordatorio de notificación inmediata a los Servicios Veterinarios Oficiales ante cualquier sospecha en jabalíes o porcino doméstico.")

    return measures


def extract_farms(full_text: str) -> int | None:
    patterns = (
        r"en las\s+([\d\.]+)\s+explotaciones comerciales",
        r"las\s+([\d\.]+)\s+explotaciones comerciales",
        r"([\d\.]+)\s+explotaciones comerciales",
        r"vigilancia pasiva reforzada.*?([\d\.]+)\s+explotaciones comerciales",
        r"realizando la vigilancia pasiva reforzada.*?([\d\.]+)\s+explotaciones comerciales",
    )
    for pattern in patterns:
        farms = int_from_match(pattern, full_text)
        if farms is not None:
            return farms
    return None


def parse_note_content(pdf_bytes: bytes, pdf_url: str) -> dict[str, Any]:
    """Analiza un PDF oficial descargado o aportado por el usuario."""
    full_text = normalize_text(extract_pdf_text(pdf_bytes))

    note_date_match = re.search(r"(\d{2}/\d{2}/\d{4})", full_text)
    note_date = parse_date_generic(note_date_match.group(1)) if note_date_match else None

    previous_note_match = re.search(
        r"Desde la última nota .*? enviada el (\d{1,2} de [a-záéíóúñ]+ de \d{4})",
        full_text,
        re.IGNORECASE,
    )
    previous_note_date = parse_spanish_textual_date(previous_note_match.group(1)) if previous_note_match else None

    new_foci = None
    for pattern in (
        r"detección de ([\d\.]+|[A-Za-záéíóúñ]+) nuevos focos",
        r"detección de ([\d\.]+|[A-Za-záéíóúñ]+) nuevo foco",
        r"han notificado la detección de ([\d\.]+|[A-Za-záéíóúñ]+) nuevos focos",
        r"han notificado la detección de ([\d\.]+|[A-Za-záéíóúñ]+) nuevo foco",
    ):
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            new_foci = parse_spanish_number(match.group(1))
            break

    new_positives, total_positives = extract_positive_counts(full_text)
    total_foci = int_from_match(r"ascienden a ([\d\.]+) los focos notificados", full_text)
    primary_foci = int_from_match(r"([\d\.]+) de ellos primarios y ([\d\.]+) secundarios", full_text, group=1)
    secondary_foci = int_from_match(r"([\d\.]+) de ellos primarios y ([\d\.]+) secundarios", full_text, group=2)
    negatives_total = int_from_match(r"se han analizado otros ([\d\.]+) animales que han resultado negativos", full_text)
    negatives_captured = int_from_match(r"de los cuales ([\d\.]+) corresponden a animales capturados o abatidos", full_text)
    negatives_passive = int_from_match(r"y ([\d\.]+) se han investigado por vigilancia pasiva", full_text)
    farms = extract_farms(full_text)

    municipalities: list[str] = []
    mun_match = re.search(r"en\s+[\d\.]+\s+municipios:\s*(.+?)\s*\(ver mapa 2\)", full_text, re.IGNORECASE)
    if mun_match:
        municipalities = split_municipalities(mun_match.group(1))

    zone = "Zona restringida II" if re.search(r"zona restringida II", full_text, re.IGNORECASE) else None
    domestic_status = None
    if re.search(r"sin haberse detectado ningún caso positivo en cerdo doméstico", full_text, re.IGNORECASE):
        domestic_status = "Sin casos positivos en cerdo doméstico"

    return {
        "pdf_url": pdf_url,
        "pdf_bytes": pdf_bytes,
        "full_text": full_text,
        "report_date": note_date,
        "previous_note_date": previous_note_date,
        "new_foci": new_foci,
        "new_positives": new_positives,
        "total_foci": total_foci,
        "primary_foci": primary_foci,
        "secondary_foci": secondary_foci,
        "total_positives": total_positives,
        "negatives_total": negatives_total,
        "negatives_captured": negatives_captured,
        "negatives_passive": negatives_passive,
        "farms": farms,
        "municipalities": municipalities,
        "zone": zone,
        "domestic_status": domestic_status,
        "measures": extract_measures(full_text, farms),
    }


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def parse_note(pdf_url: str) -> dict[str, Any]:
    return parse_note_content(fetch_bytes(pdf_url), pdf_url)


def save_manual_note(pdf_bytes: bytes) -> tuple[bool, str]:
    """Permite disponer de cifras oficiales verificables pese a fallo HTTPS.

    No inventa indicadores: los extrae del texto del documento y se niega a
    guardarlo si no reconoce el tema, fecha o cifras acumuladas.
    """
    if len(pdf_bytes) > 20_000_000 or not pdf_bytes.startswith(b"%PDF-"):
        return False, "El archivo no es un PDF válido o supera 20 MB."
    checksum = hashlib.sha256(pdf_bytes).hexdigest()
    cache_id = "manual://" + checksum
    try:
        record = parse_note_content(pdf_bytes, cache_id)
    except Exception as exc:
        return False, f"No se pudo extraer texto del PDF: {exc}"

    low = strip_accents(record.get("full_text", "").lower())
    if "peste porcina africana" not in low or "catalu" not in low:
        return False, "El archivo no parece ser una nota sobre PPA en Cataluña."
    if record.get("report_date") is None:
        return False, "No se pudo identificar una fecha verificable en la nota."
    if not any(record.get(k) is not None for k in
               ("total_foci", "total_positives", "negatives_total", "farms")):
        return False, "No se identificaron cifras epidemiológicas en el PDF."

    # Combinar con el histórico ya verificado (si existe), sin retroceder a
    # un dato antiguo ni sustituir otras notas vigentes por un archivo viejo.
    snapshot = read_snapshot()
    old_parsed = restore_snapshot(snapshot)[1] if snapshot else []
    by_date = {r.get("report_date"): r for r in old_parsed if r.get("report_date")}
    by_date[record["report_date"]] = record
    parsed = sorted(by_date.values(), key=lambda r: r["report_date"], reverse=True)[:12]
    notes = [{"title": "Nota PPA Cataluña (PDF aportado por usuario)"
              if r.get("pdf_url", "").startswith("manual://") else
              "Nota PPA Cataluña (MAPA)",
              "url": r.get("pdf_url", ""), "report_date": r["report_date"]}
             for r in parsed]
    try:
        cached = pdf_cache_path(cache_id)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(pdf_bytes)
    except OSError:
        return False, "No se pudo conservar el PDF en disco."

    ok = store_verified_snapshot(notes, parsed, datetime.now(timezone.utc).isoformat())
    if not ok:
        return False, "No se pudo guardar el histórico verificado."
    return True, ("Nota oficial incorporada, fecha " +
                  record["report_date"].strftime("%d/%m/%Y") +
                  ". Si falla Internet, se usarán los datos de esta nota.")


def find_latest_general_note(parsed_notes: list[dict[str, Any]]) -> dict[str, Any]:
    for note in parsed_notes:
        if any(note.get(field) is not None for field in ("total_foci", "total_positives", "negatives_total", "farms")):
            return note
    return parsed_notes[0]


def merge_display_note(latest_note: dict[str, Any], latest_general_note: dict[str, Any], parsed_notes: list[dict[str, Any]]) -> dict[str, Any]:
    display = dict(latest_note)
    source_dates = {}
    for field in (
        "total_foci", "primary_foci", "secondary_foci", "total_positives",
        "negatives_total", "negatives_captured", "negatives_passive", "farms",
        "municipalities", "zone", "domestic_status", "measures"
    ):
        value = display.get(field)
        if value is not None and value != []:
            source_dates[field] = display.get("report_date")
        if value is None or value == []:
            # Recuperación campo a campo: una nota puede contener focos pero omitir
            # otra cifra. No dependemos de que todos los datos estén en la misma nota.
            for candidate in [latest_general_note, *parsed_notes]:
                fallback_value = candidate.get(field)
                if fallback_value is not None and fallback_value != []:
                    display[field] = fallback_value
                    source_dates[field] = candidate.get("report_date")
                    break
    display["_source_dates"] = source_dates

    previous_comparable = None
    for candidate in parsed_notes[1:]:
        if candidate.get("total_foci") is not None or candidate.get("total_positives") is not None:
            previous_comparable = candidate
            break
    display["previous_comparable_note"] = previous_comparable

    if display.get("new_foci") is None and previous_comparable and latest_note is latest_general_note:
        cur = display.get("total_foci")
        prev = previous_comparable.get("total_foci")
        if cur is not None and prev is not None:
            display["new_foci"] = cur - prev

    if display.get("new_positives") is None and previous_comparable and latest_note is latest_general_note:
        cur = display.get("total_positives")
        prev = previous_comparable.get("total_positives")
        if cur is not None and prev is not None:
            display["new_positives"] = cur - prev

    # Control de coherencia: nunca presentar incrementos negativos por haber
    # mezclado notas o por un fallo puntual de extracción.
    if isinstance(display.get("new_positives"), int) and display["new_positives"] < 0:
        display["new_positives"] = None
    if isinstance(display.get("new_foci"), int) and display["new_foci"] < 0:
        display["new_foci"] = None

    # Blindaje final del dato de explotaciones
    if display.get("farms") in (None, "", "N/D"):
        for candidate in parsed_notes:
            if candidate.get("farms") not in (None, "", "N/D"):
                display["farms"] = candidate["farms"]
                break

    return display


def build_history(parsed_notes: list[dict[str, Any]], max_items: int = 12) -> pd.DataFrame:
    rows = []
    for stats in parsed_notes[:max_items]:
        rows.append({
            "Fecha": stats.get("report_date"),
            "Nuevos focos": stats.get("new_foci"),
            "Nuevos positivos": stats.get("new_positives"),
            "Focos totales": stats.get("total_foci"),
            "Positivos totales": stats.get("total_positives"),
            "Negativos analizados": stats.get("negatives_total"),
            "Explotaciones vigiladas": stats.get("farms"),
            "URL": stats.get("pdf_url", ""),
        })
    return (pd.DataFrame(rows).dropna(subset=["Fecha"]).sort_values("Fecha").reset_index(drop=True)
            if rows else pd.DataFrame())


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def get_mercolleida_snapshot() -> dict[str, Any]:
    result: dict[str, Any] = {
        "home_date": None,
        "detail_date": None,
        "comment_date": None,
        "comment_text": None,
        "access_denied": None,
        "price_source": None,
        "price_date": None,
        "price_value": None,
        "price_unit": None,
        "price_delta": None,
        "price_delta_pct": None,
    }

    try:
        html = fetch_text(MERCOLLEIDA_HOME_URL)
        text = normalize_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        home_date_match = re.search(r"Últimas cotizaciones del mercado ganadero\s+(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
        if home_date_match:
            result["home_date"] = parse_date_generic(home_date_match.group(1))
        comment_match = re.search(r"Comentario porcino - (\d{2}/\d{2}/\d{4})\s+(.*?)(?:Comentario|Suscríbete|$)", text, re.IGNORECASE)
        if comment_match:
            result["comment_date"] = parse_date_generic(comment_match.group(1))
            result["comment_text"] = normalize_text(comment_match.group(2))
    except Exception:
        pass

    try:
        html = fetch_text(MERCOLLEIDA_CERDO_URL)
        text = normalize_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        detail_date_match = re.search(r"Última actualización:\s*(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
        if detail_date_match:
            result["detail_date"] = parse_date_generic(detail_date_match.group(1))
        result["access_denied"] = "Acceso denegado" in text
    except Exception:
        pass

    try:
        html = fetch_text(THREETHREE_PRICES_URL)
        text = normalize_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        price_match = re.search(
            r"Mercolleida\s+Vivo\s+(\d{1,2}\s+[A-Za-záéíóúñ]{3})\s+([\d,\.]+)\s+EUR\s+kg\s+([\-\d,\.]+)\s+([\-\d,\.]+%)",
            text,
            re.IGNORECASE,
        )
        if price_match:
            result["price_source"] = "3tres3"
            result["price_date"] = price_match.group(1)
            result["price_value"] = price_match.group(2).replace(".", "").replace(",", ".")
            result["price_unit"] = "EUR/kg vivo"
            result["price_delta"] = price_match.group(3).replace(".", "").replace(",", ".")
            result["price_delta_pct"] = price_match.group(4)
    except Exception:
        pass

    return result


def format_date_es(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else "N/D"


def metric_delta(current: int | None, previous: int | None) -> str | None:
    if current is None or previous is None:
        return None
    delta = current - previous
    return "0" if delta == 0 else f"{delta:+d}"


def render_sources(latest_note: dict[str, Any], merco: dict[str, Any]) -> None:
    with st.sidebar:
        st.header("Fuentes")
        st.markdown(f"- [Noticias sanidad animal MAPA]({MAPA_NEWS_URL})")
        st.markdown(f"- [Ficha PPA MAPA]({MAPA_DISEASE_URL})")
        st.markdown(f"- [Mercolleida]({MERCOLLEIDA_HOME_URL})")
        st.markdown(f"- [Cerdo cebado Mercolleida]({MERCOLLEIDA_CERDO_URL})")
        st.markdown(f"- [Cotizaciones 3tres3]({THREETHREE_PRICES_URL})")
        st.divider()
        st.caption(f"Última nota MAPA disponible: {format_date_es(latest_note.get('report_date'))}")
        if merco.get("home_date"):
            st.caption(f"Última fecha ganadero Mercolleida: {format_date_es(merco['home_date'])}")
        st.caption("Consulta periódica cada 15 minutos (con app abierta). Última lectura correcta conservada localmente.")


render_brand_header()

if st_autorefresh is not None:
    st_autorefresh(interval=15 * 60 * 1000, key="ppa_autorefresh")

col_a, col_b = st.columns([1, 4])
with col_a:
    if st.button("🔄 Forzar actualización", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with col_b:
    st.info(
        "Consulta datos oficiales con conexión verificada. Si no puede actualizarlos, "
        "muestra la última lectura correcta y su fecha. No inventa cifras."
    )

# Se puede recuperar un ZIP guardado antes de un reinicio o despliegue.
with st.sidebar:
    st.markdown("### Recuperación si falla el MAPA")
    st.caption("Carga aquí la última nota PDF oficial del MAPA. Sus cifras "
               "y mapas se conservarán y se mostrarán si vuelve a fallar la conexión.")
    st.markdown(f"[Descargar notas oficiales de PPA — MAPA]({MAPA_DISEASE_URL})")
    uploaded_note = st.file_uploader("Importar nota oficial (PDF)", type=["pdf"], key="nota_ppa_manual")
    if uploaded_note is not None and st.button("Guardar lectura PDF", key="import_ppa_pdf", use_container_width=True):
        ok, message = save_manual_note(uploaded_note.getvalue())
        if ok:
            st.success(message)
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(message)
    st.divider()
    st.markdown("### Copia de seguridad")
    uploaded_backup = st.file_uploader("Restaurar última lectura (ZIP)", type=["zip"], key="backup_ppa")
    if uploaded_backup and st.button("Restaurar datos verificados", key="restore_ppa"):
        if restore_backup_zip(uploaded_backup.getvalue()):
            st.success("Copia restaurada. Se recargará el panel.")
            st.cache_data.clear()
            st.rerun()
        else:
            st.error("No se ha podido validar o restaurar el ZIP.")

cached_snapshot = read_snapshot()
notes: list[dict[str, Any]] = []
parsed_notes: list[dict[str, Any]] = []
mapa_error = None
status = "ACTUALIZADO"
last_success_utc = None

try:
    notes, source_checked_utc = get_mapa_notes()
    extraction_errors = []
    for item in notes[:12]:
        try:
            result = parse_note(item["url"])
            if result.get("report_date") is None:
                result["report_date"] = item.get("report_date")
            parsed_notes.append(result)
        except Exception as exc:
            extraction_errors.append(f"{item['title']}: {exc}")
    if not parsed_notes:
        raise RuntimeError("No se pudo descargar o procesar ninguna nota oficial. " + "; ".join(extraction_errors[:2]))
    if parsed_notes[0].get("pdf_url") != notes[0].get("url"):
        status = "PARCIAL"
        mapa_error = "No se pudo leer el PDF más reciente; se utiliza la última nota procesada. " + "; ".join(extraction_errors[:1])
    if not any(n.get(k) is not None for n in parsed_notes for k in
               ("total_foci", "total_positives", "negatives_total", "farms")):
        raise RuntimeError("Los PDF descargados no contienen cifras epidemiológicas verificables.")
    if status != "PARCIAL":
        store_verified_snapshot(notes, parsed_notes, source_checked_utc)
        cached_snapshot = read_snapshot()
    last_success_utc = (cached_snapshot or {}).get("last_success_utc")
except Exception as exc:
    mapa_error = str(exc)
    status = "SIN CONEXIÓN"
    if cached_snapshot:
        notes, parsed_notes = restore_snapshot(cached_snapshot)
        last_success_utc = cached_snapshot.get("last_success_utc")
        status = "DATOS CONSERVADOS"
    else:
        notes, parsed_notes = [], []
        status = "SIN DATOS PREVIOS"

if status != "ACTUALIZADO":
    if parsed_notes:
        newest = parsed_notes[0].get("report_date")
        st.error(
            "⚠️ NO SE HA PODIDO ACTUALIZAR LA LECTURA DEL MAPA. "
            f"Se muestran los últimos datos disponibles, de fecha **{format_date_es(newest)}**. "
            "NO son cifras confirmadas hoy."
        )
        if last_success_utc:
            st.caption(f"Última consulta con lectura correcta: {last_success_utc[:19].replace('T', ' ')} UTC")
    else:
        st.error("⚠️ NO SE HA PODIDO ACTUALIZAR EL MAPA Y NO EXISTE UNA LECTURA ANTERIOR GUARDADA. "
                 "No se muestran cifras epidemiológicas no verificadas. Puede restaurar un ZIP desde la barra lateral.")
    if mapa_error:
        st.caption("Si es la primera ejecución y no hay datos previos, "
                   "importa el último PDF del MAPA desde la barra lateral. "
                   "Streamlit Cloud puede eliminar archivos guardados al reiniciar; "
                   "descarga y conserva el ZIP de respaldo.")
        with st.expander("Detalles del problema de actualización"):
            st.code(mapa_error)
            if "CERTIFICATE_VERIFY_FAILED" in mapa_error or "SSLError" in mapa_error:
                st.info("El certificado HTTPS no está validado por el servidor. Debe instalarse la CA raíz de confianza "
                        "y configurarse REQUESTS_CA_BUNDLE en el entorno. No se desactiva SSL por seguridad.")
else:
    latest_date = parsed_notes[0].get("report_date")
    st.success(f"Lectura oficial correcta. Última nota MAPA: {format_date_es(latest_date)}")

zip_bytes = backup_zip()
if zip_bytes:
    with st.sidebar:
        st.download_button(
            "Descargar copia de lectura verificada (ZIP)",
            data=zip_bytes,
            file_name="PPA_ultima_lectura_verificada.zip",
            mime="application/zip",
            use_container_width=True,
        )
        st.caption("Guarda este ZIP fuera de Streamlit Cloud: el almacenamiento del servidor puede reiniciarse.")

if parsed_notes:
    latest_note = parsed_notes[0]
    latest_general_note = find_latest_general_note(parsed_notes)
    display_note = merge_display_note(latest_note, latest_general_note, parsed_notes)
    previous_note = display_note.get("previous_comparable_note")
else:
    latest_note = {"report_date": None, "pdf_url": None, "pdf_bytes": b""}
    latest_general_note = latest_note
    display_note = latest_note
    previous_note = None

merco = get_mercolleida_snapshot()
market_stale = False
if merco.get("price_value") is not None or merco.get("home_date") is not None:
    save_json_atomic(MARKET_FILE, {
        "version": 1,
        "last_success_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot": serialize_record(merco),
    })
else:
    try:
        last_market = json.loads(MARKET_FILE.read_text(encoding="utf-8"))
        if last_market.get("version") == 1:
            merco = deserialize_dates(last_market["snapshot"], keys=("home_date", "detail_date", "comment_date"))
            market_stale = True
            st.warning("No se han podido consultar las fuentes de mercado. "
                       "Se muestra la última cotización conservada (ver fecha de la fuente). "
                       f"Última lectura correcta: {last_market.get('last_success_utc', 'N/D')[:19].replace('T', ' ')} UTC.")
    except (OSError, ValueError, TypeError, KeyError):
        pass
render_sources(display_note, merco)

if parsed_notes and latest_note.get("report_date") != latest_general_note.get("report_date"):
    st.warning(
        f"La última nota disponible ({format_date_es(latest_note.get('report_date'))}) no aporta cifras generales suficientes. "
        f"Se mantienen las últimas cifras epidemiológicas consolidadas de la nota del {format_date_es(latest_general_note.get('report_date'))}."
    )


st.subheader("Resumen ejecutivo")
st.markdown(
    f"**Última nota publicada MAPA:** {format_date_es(latest_note.get('report_date'))}\n\n"
    f"**Última nota con cifras generales:** {format_date_es(latest_general_note.get('report_date'))}\n\n"
    f"**Ámbito principal:** {display_note.get('zone') or 'Revisar nota'}\n\n"
    f"**Estado en porcino doméstico:** {display_note.get('domestic_status') or 'Revisar nota oficial'}"
    + (f" (según nota del {format_date_es(display_note.get('_source_dates', {}).get('domestic_status'))})"
       if display_note.get('_source_dates', {}).get('domestic_status') else "")
)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Nuevos focos", display_note.get("new_foci") if display_note.get("new_foci") is not None else "N/D")
m2.metric(
    "Nuevos positivos",
    display_note.get("new_positives") if display_note.get("new_positives") is not None else "N/D",
    delta=metric_delta(display_note.get("total_positives"), previous_note.get("total_positives") if previous_note else None),
)
m3.metric(
    "Focos totales",
    display_note.get("total_foci") if display_note.get("total_foci") is not None else "N/D",
    delta=metric_delta(display_note.get("total_foci"), previous_note.get("total_foci") if previous_note else None),
)
m4.metric(
    "Positivos totales",
    display_note.get("total_positives") if display_note.get("total_positives") is not None else "N/D",
    delta=metric_delta(display_note.get("total_positives"), previous_note.get("total_positives") if previous_note else None),
)

m5, m6, m7, m8 = st.columns(4)
m5.metric("Negativos analizados", f"{display_note['negatives_total']:,}".replace(",", ".") if display_note.get("negatives_total") is not None else "N/D")
m6.metric("Negativos capturados/abatidos", f"{display_note['negatives_captured']:,}".replace(",", ".") if display_note.get("negatives_captured") is not None else "N/D")
m7.metric("Negativos vigilancia pasiva", f"{display_note['negatives_passive']:,}".replace(",", ".") if display_note.get("negatives_passive") is not None else "N/D")
m8.metric("Explotaciones vigiladas", str(display_note.get("farms")) if display_note.get("farms") is not None else "N/D")
source_dates = display_note.get("_source_dates", {})
if source_dates:
    details = []
    for title, field in (("Focos", "total_foci"), ("Positivos", "total_positives"),
                         ("Negativos", "negatives_total"), ("Explotaciones vigiladas", "farms"),
                         ("Porcino doméstico", "domestic_status")):
        if source_dates.get(field):
            details.append(f"{title}: {format_date_es(source_dates[field])}")
    if details:
        st.caption("Fecha de la fuente de cada indicador — " + " · ".join(details))

missing_positive_fields = [
    label for label, field in (
        ("nuevos positivos", "new_positives"),
        ("positivos totales", "total_positives"),
    )
    if display_note.get(field) is None
]
if missing_positive_fields:
    st.warning(
        "No se han podido verificar automáticamente: " + ", ".join(missing_positive_fields) +
        ". Consulta la nota oficial enlazada. El panel no sustituye una cifra no verificada por cero."
    )

if display_note.get("municipalities"):
    st.write("**Municipios con positivos acumulados**")
    st.write(", ".join(display_note["municipalities"]))

with st.expander("Enlace y descarga de la última nota oficial"):
    if latest_note.get("pdf_url", "").startswith("https://"):
        st.link_button("Abrir PDF oficial MAPA", latest_note["pdf_url"])
    if latest_note.get("pdf_bytes"):
        st.download_button(
            "Descargar última nota MAPA",
            data=latest_note["pdf_bytes"],
            file_name=f"PPA_MAPA_{format_date_es(latest_note.get('report_date')).replace('/', '')}.pdf",
            mime="application/pdf",
        )
    elif not latest_note.get("pdf_bytes"):
        st.info("Todavía no se dispone de una nota PDF descargable.")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📍 Cifras y lectura", "🗺️ Mapas", "🛡️ Medidas", "📈 Evolución", "💶 Mercado"])

with tab1:
    st.write("### Lectura rápida")
    bullets: list[str] = []
    if latest_note.get("previous_note_date"):
        bullets.append(f"La nota compara frente a la actualización del {format_date_es(latest_note['previous_note_date'])}.")
    if latest_general_note.get("report_date") and latest_general_note.get("report_date") != latest_note.get("report_date"):
        bullets.append(f"Las cifras generales visibles proceden de la nota del {format_date_es(latest_general_note.get('report_date'))}.")
    if display_note.get("primary_foci") is not None and display_note.get("secondary_foci") is not None:
        bullets.append(f"Los focos acumulados se desglosan en {display_note['primary_foci']} primarios y {display_note['secondary_foci']} secundarios.")
    if display_note.get("zone"):
        bullets.append(f"Los hallazgos reportados se mantienen en {display_note['zone']}.")
    if display_note.get("domestic_status"):
        bullets.append(f"Situación en cerdo doméstico: {display_note['domestic_status'].lower()}.")
    if display_note.get("farms") is not None:
        bullets.append(f"Explotaciones vigiladas mostradas en panel: {display_note['farms']}.")
    for item in bullets:
        st.markdown(f"- {item}")

    st.write("### Últimas notas detectadas")
    for note in notes[:8]:
        if str(note.get("url", "")).startswith("https://"):
            st.markdown(f"- [{note['report_date'].strftime('%d/%m/%Y')}]({note['url']}) — {note['title']}")
        else:
            st.markdown(f"- {note['report_date'].strftime('%d/%m/%Y')} — {note['title']} (PDF guardado en la app)")

with tab2:
    st.write("### Mapas oficiales de la última nota")
    try:
        st.image(render_pdf_page(latest_note["pdf_bytes"], 1), caption="Página 2 del PDF oficial: zonas restringidas y distribución de focos/casos.")
    except Exception as exc:
        st.warning(f"No se ha podido renderizar la página 2 del PDF: {exc}")
    try:
        st.image(render_pdf_page(latest_note["pdf_bytes"], 2), caption="Página 3 del PDF oficial: vallados en puntos de riesgo y medidas en granjas.")
    except Exception as exc:
        st.warning(f"No se ha podido renderizar la página 3 del PDF: {exc}")

with tab3:
    st.write("### Medidas que está aplicando la administración")
    if display_note.get("measures"):
        for measure in display_note["measures"]:
            st.markdown(f"- {measure}")
    else:
        st.warning("No se han podido resumir automáticamente las medidas desde la nota actual.")

with tab4:
    st.write("### Evolución de las últimas notas oficiales")
    history_df = build_history(parsed_notes, max_items=12)
    if history_df.empty:
        st.warning("No se ha podido construir el histórico automático.")
    else:
        chart_df = history_df.set_index("Fecha")[["Focos totales", "Positivos totales"]]
        st.line_chart(chart_df)
        st.dataframe(history_df.drop(columns=["URL"]), use_container_width=True, hide_index=True)

with tab5:
    st.write("### Cerdo cebado / Mercolleida")
    k1, k2, k3 = st.columns(3)
    k1.metric("Última fecha ganadero Mercolleida", format_date_es(merco.get("home_date")))
    k2.metric("Detalle cerdo cebado Mercolleida", format_date_es(merco.get("detail_date")))
    k3.metric("Acceso detalle", "Restringido" if merco.get("access_denied") else "Abierto/No detectado")

    if merco.get("price_value"):
        delta_text = None
        if merco.get("price_delta"):
            try:
                delta_text = f"{float(merco['price_delta']):+.3f} €/kg"
            except ValueError:
                delta_text = merco["price_delta"]
        st.metric("Precio Mercolleida (fallback público)", f"{float(merco['price_value']):.3f} €/kg vivo", delta=delta_text)
        st.caption(f"Fuente pública secundaria: {merco['price_source']} · fecha visible en mercado: {merco.get('price_date') or 'N/D'}")
    else:
        st.warning("No se ha podido recuperar la cotización numérica en esta ejecución.")

    if merco.get("comment_text"):
        st.write("### Último comentario porcino visible")
        st.info(f"{format_date_es(merco.get('comment_date'))}: {merco['comment_text']}")

    st.link_button("Abrir Mercolleida", MERCOLLEIDA_HOME_URL)
    st.link_button("Abrir mercado cerdo cebado Mercolleida", MERCOLLEIDA_CERDO_URL)

st.divider()
st.caption("Los datos provienen exclusivamente de notas oficiales verificables. Si falla la actualización, se visualiza el último dato guardado con aviso y fecha. N/D significa no disponible, nunca cero.")
