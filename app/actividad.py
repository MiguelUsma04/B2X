"""Lo que B2K está haciendo en segundo plano, para mostrarlo en la pantalla.

Hay trabajos que tardan —leer cien sitios, escribir correos con la IA,
buscar en Maps— y mientras corren la pantalla parecía quieta. Este módulo
junta en un solo lugar todo lo que está en marcha, con el paso en el que va,
para la barra de actividad de arriba.

Dos fuentes:

1. Los procesos largos que ya llevan su propio progreso (emails, celulares,
   sitios, fichas, redacción, el goteo de la campaña). Se leen tal cual.
2. Los pedidos que tardan unos segundos y no tenían progreso —una prueba de
   la IA, una búsqueda en Maps—. Se anotan con `tarea()` mientras corren.
"""
import itertools
import time
from contextlib import contextmanager

_TAREAS: dict[int, dict] = {}
_ids = itertools.count(1)


class _Tarea:
    def __init__(self, tid: int):
        self.id = tid

    def paso(self, texto: str, hechos: int | None = None,
             total: int | None = None) -> None:
        t = _TAREAS.get(self.id)
        if not t:
            return
        t["paso"] = texto
        if hechos is not None:
            t["hechos"] = hechos
        if total is not None:
            t["total"] = total


@contextmanager
def tarea(titulo: str, total: int = 0):
    """Anota un trabajo mientras dura. Se borra solo al terminar, aunque falle."""
    tid = next(_ids)
    _TAREAS[tid] = {"id": f"t{tid}", "titulo": titulo, "paso": "",
                    "hechos": 0, "total": total, "desde": time.time()}
    try:
        yield _Tarea(tid)
    finally:
        _TAREAS.pop(tid, None)


def _de_progreso(clave: str, titulo: str, p: dict, detalle: str = "") -> dict | None:
    if not p.get("running"):
        return None
    actual = p.get("current_contact") or ""
    paso = " · ".join(x for x in (actual, detalle) if x)
    return {"id": clave, "titulo": titulo, "paso": paso,
            "hechos": p.get("processed") or 0, "total": p.get("total") or 0}


def ahora() -> list[dict]:
    from . import enrichment, mailer, redactor

    salida = []
    for clave, titulo, prog, extra in (
        ("emails", "Buscando emails", enrichment.PROGRESS, "current_provider"),
        ("celulares", "Buscando celulares", enrichment.MOBILE_PROGRESS, None),
        ("sitios", "Leyendo los sitios web", enrichment.WEB_PROGRESS, None),
        ("fichas", "La IA arma las fichas de las empresas", enrichment.AI_PROGRESS, None),
    ):
        p = prog.as_dict()
        fila = _de_progreso(clave, titulo, p, (p.get(extra) or "") if extra else "")
        if fila:
            salida.append(fila)

    p = redactor.PROGRESO.as_dict()
    fila = _de_progreso("redaccion", "La IA está escribiendo los correos", p,
                        p.get("paso") or "")
    if fila:
        salida.append(fila)

    try:
        e = mailer.estado()
        c = e.get("campaign") or {}
        if c.get("status") == "running" and e.get("pending") and mailer.correo_activo():
            proximo = (e.get("next_at") or "")[11:16]
            espera = "" if mailer.en_horario() else " · esperando el horario de envío"
            salida.append({
                "id": "campania", "titulo": f"Enviando la campaña «{c.get('name') or 'sin nombre'}»",
                "paso": (f"próximo cerca de las {proximo} UTC" if proximo else "") + espera,
                "hechos": e.get("sent", 0) + e.get("error", 0),
                "total": e.get("sent", 0) + e.get("error", 0) + e.get("pending", 0)})
    except Exception:
        pass

    salida += [{k: v for k, v in t.items() if k != "desde"}
               | {"segundos": int(time.time() - t["desde"])}
               for t in list(_TAREAS.values())]
    return salida
