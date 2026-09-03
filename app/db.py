"""Capa de acceso a SQLite. Sin ORM: el esquema es chico y las queries son directas."""
import re
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "b2x.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS import_batches (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    filename           TEXT NOT NULL,
    imported_at        TEXT NOT NULL DEFAULT (datetime('now')),
    total_rows         INTEGER NOT NULL DEFAULT 0,
    new_contacts       INTEGER NOT NULL DEFAULT 0,
    duplicate_contacts INTEGER NOT NULL DEFAULT 0,
    icp_tag            TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name        TEXT,
    last_name         TEXT,
    full_name         TEXT,
    email             TEXT,
    email_status      TEXT NOT NULL DEFAULT 'pending'
                      CHECK (email_status IN ('verified','unverified','not_found','pending')),
    email_source      TEXT
                      CHECK (email_source IN ('apollo','prospeo','icypeas','hunter','web')
                             OR email_source IS NULL),
    phone             TEXT,
    -- 'personal' = directo o móvil de la persona; 'company' = conmutador;
    -- 'whatsapp' = número publicado como WhatsApp, se le escribe directo.
    phone_type        TEXT,
    job_title         TEXT,
    company_name      TEXT,
    company_domain    TEXT,
    linkedin_url      TEXT,
    -- Datos del negocio cuando el contacto viene de Google Maps.
    place_id          TEXT,
    address           TEXT,
    rating            REAL,
    rating_count      INTEGER,
    maps_url          TEXT,
    category          TEXT,
    social_url        TEXT,
    -- Ficha que arma la IA leyendo el sitio: JSON completo y un resumen
    -- corto para poder mostrarlo en la lista sin abrir el detalle.
    ai_profile        TEXT,
    ai_summary        TEXT,
    ai_updated_at     TEXT,
    import_batch_id   INTEGER REFERENCES import_batches(id) ON DELETE SET NULL,
    ghl_status        TEXT NOT NULL DEFAULT 'pending'
                      CHECK (ghl_status IN ('pending','sent','error')),
    ghl_contact_id    TEXT,
    ghl_opportunity_id TEXT,
    -- 1 = algún proveedor tiene su móvil pero no lo reveló (cuesta créditos).
    mobile_available  INTEGER NOT NULL DEFAULT 0,
    ghl_error_message TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Dedupe por email: parcial, para que múltiples contactos sin email no colisionen.
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_email
    ON contacts(lower(email)) WHERE email IS NOT NULL AND email <> '';
-- Dedupe secundario: nombre completo + dominio.
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_name_domain
    ON contacts(lower(full_name), lower(company_domain))
    WHERE full_name IS NOT NULL AND company_domain IS NOT NULL AND company_domain <> '';
CREATE INDEX IF NOT EXISTS idx_contacts_email_status ON contacts(email_status);
CREATE INDEX IF NOT EXISTS idx_contacts_ghl_status   ON contacts(ghl_status);
CREATE INDEX IF NOT EXISTS idx_contacts_batch        ON contacts(import_batch_id);

-- Consumo de la API de Google Maps. Google cobra por CONSULTA, y una
-- consulta trae hasta 20 negocios: por eso se guardan las dos cifras.
CREATE TABLE IF NOT EXISTS places_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    query      TEXT,
    requests   INTEGER NOT NULL DEFAULT 1,
    results    INTEGER NOT NULL DEFAULT 0,
    timestamp  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_places_usage_ts ON places_usage(timestamp);

-- Qué negocios devolvió cada búsqueda. Google contesta siempre lo mismo para
-- el mismo texto, así que sin esta memoria repetir una búsqueda no aporta
-- nada: con ella se saltean los ya vistos y se va a buscar más adelante.
CREATE TABLE IF NOT EXISTS places_seen (
    query_key  TEXT NOT NULL,
    place_id   TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (query_key, place_id)
);

-- Consumo de la IA, para saber cuánto se está gastando en leer sitios.
CREATE TABLE IF NOT EXISTS ai_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER,
    model      TEXT,
    tokens_in  INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    ok         INTEGER NOT NULL DEFAULT 1,
    timestamp  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_ts ON ai_usage(timestamp);

-- Servidor de salida. Una sola fila: es la cuenta desde la que escribe el
-- equipo. La contraseña queda en claro, igual que las API keys del .env: la
-- base vive en el servidor y no se versiona.
-- Los buzones desde los que se manda. Varios, para repartir el volumen: un
-- solo remitente enviando de a cientos es lo que dispara los filtros de spam.
CREATE TABLE IF NOT EXISTS smtp_config (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT,
    host       TEXT,
    port       INTEGER NOT NULL DEFAULT 587,
    username   TEXT,
    password   TEXT,
    from_name  TEXT,
    from_email TEXT,
    security   TEXT NOT NULL DEFAULT 'starttls'
               CHECK (security IN ('starttls', 'ssl', 'none')),
    active     INTEGER NOT NULL DEFAULT 1,
    daily_cap  INTEGER NOT NULL DEFAULT 50,
    last_used  TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Un envío: el texto y el ritmo con que se suelta.
CREATE TABLE IF NOT EXISTS email_campaigns (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT,
    subject        TEXT NOT NULL,
    body           TEXT NOT NULL,
    every_seconds  INTEGER NOT NULL DEFAULT 180,
    jitter_seconds INTEGER NOT NULL DEFAULT 60,
    daily_cap      INTEGER NOT NULL DEFAULT 50,
    status         TEXT NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'paused', 'done', 'cancelled')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- La cola. Cada fila tiene su hora: el goteo vive acá y no en memoria, así
-- reiniciar la app no pierde lo que faltaba mandar ni reenvía lo ya mandado.
CREATE TABLE IF NOT EXISTS email_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Desde qué buzón salió: para repartir la carga y para saber después
    -- cuál viene rebotando.
    smtp_id     INTEGER REFERENCES smtp_config(id) ON DELETE SET NULL,
    campaign_id INTEGER NOT NULL REFERENCES email_campaigns(id) ON DELETE CASCADE,
    contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    send_after  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'sent', 'error', 'cancelled')),
    error       TEXT,
    sent_at     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_email_queue_pend
    ON email_queue(status, send_after);
-- Un contacto no puede estar dos veces en la misma campaña.
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_queue_unico
    ON email_queue(campaign_id, contact_id);

CREATE TABLE IF NOT EXISTS enrichment_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id       INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    provider         TEXT NOT NULL,
    success          INTEGER NOT NULL DEFAULT 0,
    request_payload  TEXT,
    response_payload TEXT,
    error_message    TEXT,
    timestamp        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_enrichlog_contact ON enrichment_log(contact_id);
"""


# Índices sobre columnas que se agregan en _migrate: corren después de ella,
# porque en una base vieja la columna todavía no existe cuando se crea el resto.
SCHEMA_POST_MIGRATE = """
-- Un mismo negocio de Maps no se importa dos veces.
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_place
    ON contacts(place_id) WHERE place_id IS NOT NULL AND place_id <> '';
"""

_schema_ready = False


def connect() -> sqlite3.Connection:
    """Abre una conexión, creando el esquema si el archivo no existe todavía.

    Se asegura en cada arranque (y tras borrar data/b2x.db) para que la app no
    quede apuntando a una base sin tablas.
    """
    global _schema_ready
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    if not _schema_ready:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.executescript(SCHEMA_POST_MIGRATE)
        conn.commit()
        _schema_ready = True
    return conn


@contextmanager
def get_db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columnas agregadas después de la primera versión, en orden de aparición.
_NEW_COLUMNS = [
    ("phone_type", "TEXT"),
    ("ghl_opportunity_id", "TEXT"),
    ("mobile_available", "INTEGER NOT NULL DEFAULT 0"),
    ("place_id", "TEXT"),
    ("address", "TEXT"),
    ("rating", "REAL"),
    ("rating_count", "INTEGER"),
    ("maps_url", "TEXT"),
    ("category", "TEXT"),
    ("social_url", "TEXT"),
    ("ai_profile", "TEXT"),
    ("ai_summary", "TEXT"),
    ("ai_updated_at", "TEXT"),
]


def _migrate(conn) -> None:
    """Agrega columnas nuevas a bases que ya existen."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(contacts)")}
    for name, ddl in _NEW_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE contacts ADD COLUMN {name} {ddl}")
    _allow_web_as_source(conn)
    _varios_buzones(conn)


def _varios_buzones(conn) -> None:
    """Pasa smtp_config de un solo buzón a varios.

    La tabla vieja tenía CHECK (id = 1) y SQLite no deja quitar un CHECK: hay
    que rehacerla. El buzón que ya estaba configurado se conserva tal cual, así
    quien venía usando la app no tiene que volver a cargarlo.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='smtp_config'"
    ).fetchone()
    if not row or not row["sql"] or "CHECK (id = 1)" not in row["sql"]:
        return   # ya es la versión nueva

    conn.execute("ALTER TABLE smtp_config RENAME TO smtp_config_vieja")
    conn.executescript("""
        CREATE TABLE smtp_config (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            label      TEXT,
            host       TEXT,
            port       INTEGER NOT NULL DEFAULT 587,
            username   TEXT,
            password   TEXT,
            from_name  TEXT,
            from_email TEXT,
            security   TEXT NOT NULL DEFAULT 'starttls'
                       CHECK (security IN ('starttls', 'ssl', 'none')),
            active     INTEGER NOT NULL DEFAULT 1,
            daily_cap  INTEGER NOT NULL DEFAULT 50,
            last_used  TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.execute("""
        INSERT INTO smtp_config
            (id, label, host, port, username, password, from_name, from_email,
             security, active, daily_cap, updated_at)
        SELECT id, from_email, host, port, username, password, from_name,
               from_email, security, 1, 50, updated_at
          FROM smtp_config_vieja
    """)
    conn.execute("DROP TABLE smtp_config_vieja")

    # La cola necesita saber por qué buzón salió cada correo.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(email_queue)")}
    if "smtp_id" not in cols:
        conn.execute("ALTER TABLE email_queue ADD COLUMN smtp_id INTEGER")


def _allow_web_as_source(conn) -> None:
    """Suma 'web' a los valores permitidos de email_source.

    El CHECK viaja dentro del CREATE TABLE y SQLite no lo deja modificar: hay
    que rehacer la tabla. Se toma el DDL que la base tiene hoy —ya con las
    columnas que se agregaron por ALTER— y solo se le amplía la lista, así no
    queda una segunda copia del esquema que se desincronice con la de arriba.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='contacts'").fetchone()
    if not row or not row["sql"] or "'web'" in row["sql"]:
        return

    viejo = "'apollo','prospeo','icypeas','hunter'"
    if viejo not in row["sql"]:
        return  # esquema inesperado: mejor no tocarlo
    ddl = row["sql"].replace(viejo, viejo + ",'web'")
    ddl = re.sub(r"CREATE TABLE\s+(IF NOT EXISTS\s+)?[\"'`\[]?contacts[\"'`\]]?",
                 "CREATE TABLE contacts_nueva", ddl, count=1)

    conn.commit()                              # el rebuild va en su propia transacción
    conn.execute("PRAGMA foreign_keys=OFF")    # si no, el DROP arrastra el log
    try:
        conn.executescript(f"""
            BEGIN;
            {ddl};
            INSERT INTO contacts_nueva SELECT * FROM contacts;
            DROP TABLE contacts;
            ALTER TABLE contacts_nueva RENAME TO contacts;
            COMMIT;""")
        conn.executescript(SCHEMA)             # los índices se fueron con la tabla vieja
        rotas = conn.execute("PRAGMA foreign_key_check").fetchall()
        if rotas:
            raise sqlite3.IntegrityError(
                f"La migración dejó {len(rotas)} referencia(s) rotas.")
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.executescript(SCHEMA_POST_MIGRATE)
