"""Inserción de contactos con deduplicación contra la base existente."""
import sqlite3
from .csv_import import read_csv_bytes, detect_mapping, detect_export_kind, row_to_contact


def _existing_keys(conn: sqlite3.Connection) -> tuple[set[str], set[tuple[str, str]]]:
    emails, name_domain = set(), set()
    for r in conn.execute(
        "SELECT lower(email) e, lower(full_name) n, lower(company_domain) d FROM contacts"
    ):
        if r["e"]:
            emails.add(r["e"])
        if r["n"] and r["d"]:
            name_domain.add((r["n"], r["d"]))
    return emails, name_domain


def import_contacts(conn: sqlite3.Connection, filename: str, raw: bytes,
                    icp_tag: str | None = None) -> dict:
    """Importa un CSV. Devuelve el resumen del batch.

    Dedupe: primero por email; si el contacto no trae email, por full_name +
    company_domain. Los duplicados se cuentan pero no se insertan.
    """
    headers, rows = read_csv_bytes(raw)
    if not headers:
        raise ValueError("El CSV no tiene encabezados legibles.")

    kind = detect_export_kind(headers)
    if kind == "company":
        raise ValueError(
            "Este CSV parece un export de CUENTAS (empresas) de Apollo, no de "
            "contactos: no tiene columnas de persona (First Name / Email / Title). "
            "Exporta desde la pestaña People de Apollo."
        )

    mapping = detect_mapping(headers)
    if not mapping.get("first_name") and not mapping.get("full_name"):
        raise ValueError("No se encontró una columna de nombre (First Name o Full Name).")

    seen_emails, seen_nd = _existing_keys(conn)
    cur = conn.execute(
        "INSERT INTO import_batches (filename, total_rows, icp_tag) VALUES (?,?,?)",
        (filename, len(rows), icp_tag or None),
    )
    batch_id = cur.lastrowid

    new_count = dup_count = skipped = 0
    for row in rows:
        c = row_to_contact(row, mapping)
        if not c["full_name"]:
            skipped += 1
            continue

        email_key = (c["email"] or "").lower() or None
        nd_key = None
        if c["full_name"] and c["company_domain"]:
            nd_key = (c["full_name"].lower(), c["company_domain"].lower())

        if email_key and email_key in seen_emails:
            dup_count += 1
            continue
        if not email_key and nd_key and nd_key in seen_nd:
            dup_count += 1
            continue

        try:
            conn.execute(
                """INSERT INTO contacts
                   (first_name,last_name,full_name,email,email_status,email_source,
                    phone,phone_type,job_title,company_name,company_domain,
                    linkedin_url,import_batch_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (c["first_name"], c["last_name"], c["full_name"], c["email"],
                 c["email_status"], c["email_source"], c["phone"], c.get("phone_type"),
                 c["job_title"], c["company_name"], c["company_domain"],
                 c["linkedin_url"], batch_id),
            )
        except sqlite3.IntegrityError:
            # Carrera contra los índices únicos (duplicado dentro del mismo archivo).
            dup_count += 1
            continue

        new_count += 1
        if email_key:
            seen_emails.add(email_key)
        if nd_key:
            seen_nd.add(nd_key)

    conn.execute(
        "UPDATE import_batches SET new_contacts=?, duplicate_contacts=? WHERE id=?",
        (new_count, dup_count, batch_id),
    )
    return {
        "batch_id": batch_id, "filename": filename, "total_rows": len(rows),
        "new_contacts": new_count, "duplicate_contacts": dup_count,
        "skipped_no_name": skipped, "mapping": mapping,
    }


def preview_csv(raw: bytes, limit: int = 10) -> dict:
    """Vista previa previa a confirmar: mapeo detectado + primeras filas."""
    headers, rows = read_csv_bytes(raw)
    kind = detect_export_kind(headers)
    mapping = detect_mapping(headers)
    return {
        "headers": headers,
        "export_kind": kind,
        "mapping": mapping,
        "total_rows": len(rows),
        "preview": [row_to_contact(r, mapping) for r in rows[:limit]],
        "unmapped_fields": [k for k, v in mapping.items() if v is None],
    }


def delete_batch(conn, batch_id: int, delete_contacts: bool = False) -> dict:
    """Borra una carga del historial.

    Por defecto conserva los contactos y solo los desvincula: ya fueron
    revisados o enviados al CRM, y perderlos rompería la deduplicación
    (volverían a entrar como nuevos en la próxima importación).

    Con delete_contacts=True se borran también, salvo los que ya están en
    el CRM: esos no se tocan nunca, porque el registro de qué se envió es
    lo único que evita mandar duplicados a GoHighLevel.
    """
    row = conn.execute("SELECT id FROM import_batches WHERE id=?", (batch_id,)).fetchone()
    if not row:
        raise ValueError(f"La carga #{batch_id} no existe.")

    total = conn.execute(
        "SELECT COUNT(*) c FROM contacts WHERE import_batch_id=?", (batch_id,)).fetchone()["c"]
    sent = conn.execute(
        "SELECT COUNT(*) c FROM contacts WHERE import_batch_id=? AND ghl_status='sent'",
        (batch_id,)).fetchone()["c"]

    deleted = 0
    if delete_contacts:
        # Los enviados al CRM se conservan siempre.
        deleted = conn.execute(
            "DELETE FROM contacts WHERE import_batch_id=? AND ghl_status<>'sent'",
            (batch_id,)).rowcount

    # Lo que quede (o todo, si no se borran) pierde el vínculo con la carga.
    conn.execute("UPDATE contacts SET import_batch_id=NULL WHERE import_batch_id=?",
                 (batch_id,))
    conn.execute("DELETE FROM import_batches WHERE id=?", (batch_id,))

    return {"batch_id": batch_id, "total_contacts": total,
            "deleted_contacts": deleted, "kept_sent_to_crm": sent,
            "kept_contacts": total - deleted}
