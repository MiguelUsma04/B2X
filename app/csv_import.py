"""Parseo de CSV de Apollo con detección aproximada de columnas.

Apollo cambia los nombres de columna entre exports, así que no asumimos orden
ni nombres exactos: normalizamos el encabezado y puntuamos contra alias conocidos.
"""
import csv
import io
import re
from urllib.parse import urlparse

# Alias por campo, en orden de preferencia. Se comparan normalizados.
FIELD_ALIASES = {
    "first_name":     ["first name", "firstname", "given name", "nombre"],
    "last_name":      ["last name", "lastname", "surname", "family name", "apellido"],
    # Ojo: "name" a secas NO va como alias. Apollo exporta "Company Name" y
    # "Company Name for Emails", y la coincidencia por contención se llevaba
    # el nombre de la empresa como si fuera el de la persona.
    "full_name":      ["full name", "fullname", "person name", "contact name",
                       "nombre completo"],
    "email":          ["email", "email address", "work email", "primary email",
                       "person email", "correo"],
    "job_title":      ["title", "job title", "position", "headline", "cargo", "puesto"],
    "company_name":   ["company name", "company", "company name for emails",
                       "organization", "organization name", "account name", "employer",
                       "empresa"],
    "company_domain": ["website", "company website", "company domain", "domain",
                       "company url", "primary domain", "sitio web"],
    "linkedin_url":   ["person linkedin url", "linkedin url", "linkedin",
                       "linkedin profile", "person linkedin"],
    # Orden = preferencia: el móvil y el directo sirven para prospectar; el
    # conmutador corporativo queda último, como último recurso.
    "phone":          ["mobile phone", "work direct phone", "direct phone", "mobile",
                       "phone number", "home phone", "corporate phone", "other phone",
                       "phone", "telefono", "teléfono"],
}

# Columnas que identifican un export de EMPRESAS (no de personas).
COMPANY_EXPORT_MARKERS = ["company linkedin url", "# employees", "annual revenue",
                          "apollo account id", "number of retail locations"]
# Columnas que sólo existen en un export de PERSONAS.
PERSON_EXPORT_MARKERS = ["first name", "last name", "person linkedin url", "title",
                         "email", "seniority", "departments"]


def _norm(s: str) -> str:
    """Normaliza un encabezado: minúsculas, sin puntuación, espacios colapsados."""
    s = (s or "").replace("\ufeff", "").strip().lower()
    s = re.sub(r"[_\-./]+", " ", s)
    s = re.sub(r"[^a-z0-9# ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


# Campos que describen a la persona: una columna "Company ..." nunca lo es.
PERSON_FIELDS = {"first_name", "last_name", "full_name", "email", "job_title",
                 "linkedin_url", "phone"}


def detect_mapping(headers: list[str]) -> dict[str, str | None]:
    """Devuelve {campo_interno: nombre_de_columna_original | None}."""
    norm_map = {_norm(h): h for h in headers if h and h.strip()}
    mapping: dict[str, str | None] = {}
    used: set[str] = set()

    for field, aliases in FIELD_ALIASES.items():
        found = None
        # 1) coincidencia exacta contra alias
        for alias in aliases:
            if alias in norm_map and norm_map[alias] not in used:
                found = norm_map[alias]
                break
        # 2) coincidencia por contención (p.ej. "Work Email (verified)")
        if not found:
            for alias in aliases:
                for nh, orig in norm_map.items():
                    if orig in used:
                        continue
                    # Una columna de empresa nunca describe a la persona.
                    if field in PERSON_FIELDS and nh.startswith("company "):
                        continue
                    if nh.startswith(alias + " ") or nh.endswith(" " + alias) or (
                        alias in nh and len(alias) >= 5
                    ):
                        found = orig
                        break
                if found:
                    break
        if found:
            used.add(found)
        mapping[field] = found
    return mapping


def detect_export_kind(headers: list[str]) -> str:
    """'person' | 'company' — cuál de los dos exports de Apollo es este archivo."""
    norm = {_norm(h) for h in headers}
    person_hits = sum(1 for m in PERSON_EXPORT_MARKERS if m in norm)
    company_hits = sum(1 for m in COMPANY_EXPORT_MARKERS if m in norm)
    has_name = "first name" in norm or "full name" in norm
    if has_name and person_hits >= 2:
        return "person"
    if company_hits >= 2 and not has_name:
        return "company"
    return "person" if person_hits >= company_hits else "company"


def clean_domain(value: str | None) -> str | None:
    """Extrae el dominio desnudo de una URL o cadena suelta."""
    if not value:
        return None
    v = value.strip().lower()
    if not v:
        return None
    if "://" not in v:
        v = "http://" + v
    try:
        host = urlparse(v).netloc or ""
    except ValueError:
        return None
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def clean_email(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().strip("'\"").lower()
    if not v or "@" not in v:
        return None
    # Apollo marca los no disponibles con placeholders
    if v.startswith("email_not_unlocked") or v in {"n/a", "na", "-", "null", "none"}:
        return None
    return v


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    # Apollo antepone ' a los teléfonos para que Excel no los reformatee
    v = str(value).strip().lstrip("'").strip()
    return v or None


def read_csv_bytes(raw: bytes) -> tuple[list[str], list[dict]]:
    """Decodifica y parsea el CSV. Devuelve (headers, filas como dict)."""
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    rows = [r for r in reader]
    return headers, rows


def row_to_contact(row: dict, mapping: dict[str, str | None]) -> dict:
    """Aplica el mapeo a una fila cruda y devuelve un contacto normalizado."""
    def g(field: str) -> str | None:
        col = mapping.get(field)
        return clean_text(row.get(col)) if col else None

    first = g("first_name")
    last = g("last_name")
    full = g("full_name")
    if not full and (first or last):
        full = " ".join(p for p in (first, last) if p)
    if full and not first and not last:
        parts = full.split()
        if len(parts) >= 2:
            first, last = parts[0], " ".join(parts[1:])
        elif parts:
            first = parts[0]

    # El teléfono se resuelve por fila, no por columna: Apollo llena distintas
    # columnas según el contacto, y el mapeo global elige una sola.
    # Se distingue el de la persona del conmutador de la empresa: llamar al
    # conmutador creyendo que es un directo hace perder tiempo al vendedor.
    PERSONAL_COLS = ("Mobile Phone", "Work Direct Phone", "Home Phone", "Other Phone")
    COMPANY_COLS = ("Corporate Phone", "Company Phone")

    phone, phone_type = None, None
    for col in PERSONAL_COLS:
        phone = clean_text(row.get(col))
        if phone:
            phone_type = "personal"
            break
    if not phone:
        mapped = g("phone")
        # La columna mapeada solo cuenta como personal si no es una de empresa.
        if mapped and (mapping.get("phone") or "") not in COMPANY_COLS:
            phone, phone_type = mapped, "personal"
    if not phone:
        for col in COMPANY_COLS:
            phone = clean_text(row.get(col))
            if phone:
                phone_type = "company"
                break

    email = clean_email(g("email"))
    return {
        "first_name": first,
        "last_name": last,
        "full_name": full,
        "email": email,
        "email_status": "verified" if email else "pending",
        "email_source": "apollo" if email else None,
        "phone": phone,
        "phone_type": phone_type,
        "job_title": g("job_title"),
        "company_name": g("company_name"),
        "company_domain": clean_domain(g("company_domain")),
        "linkedin_url": g("linkedin_url"),
    }
