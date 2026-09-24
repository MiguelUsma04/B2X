#!/usr/bin/env python3
"""Pone al día el bloque `environment:` del servicio b2x en el compose.

Por qué un script y no "editalo a mano": ese docker-compose.yml no es solo de
B2K. En el mismo archivo viven n8n y postgres, y el YAML se rompe con un
espacio de más. Un error ahí no deja a B2K sin Kommo: deja a los tres
servicios sin arrancar.

Lo que hace:

  1. Copia el archivo a .bak antes de tocarlo.
  2. Encuentra el servicio b2x y, dentro, su lista `environment:`.
  3. Reemplaza SOLO esa lista por la del bloque de referencia.
  4. Comprueba que lo que queda sigue teniendo los mismos servicios que
     antes y la misma cantidad de líneas fuera de ese bloque.
  5. Recién ahí escribe.

Si algo no cuadra, no escribe nada y dice qué pasó.

    python3 /opt/b2x/deploy/poner_env_b2k.py

Se le puede pasar otra ruta:

    python3 poner_env_b2k.py /root/docker-compose.yml
"""
import re
import shutil
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
REFERENCIA = AQUI / "docker-compose.b2x.yml"


def bloque_environment(lineas, desde):
    """Dónde empieza y termina la lista environment: del servicio.

    Devuelve (inicio, fin, sangria) con fin exclusivo, o None.
    """
    for i in range(desde, len(lineas)):
        m = re.match(r"^(\s+)environment:\s*$", lineas[i])
        if not m:
            # Si aparece otro servicio al mismo nivel, ya nos pasamos.
            if re.match(r"^  \S", lineas[i]) and i > desde:
                return None
            continue
        sangria = len(m.group(1))
        fin = i + 1
        while fin < len(lineas):
            l = lineas[fin]
            if not l.strip():
                fin += 1
                continue
            # La lista termina cuando algo vuelve al nivel de environment:
            # o más afuera.
            if len(l) - len(l.lstrip()) <= sangria:
                break
            fin += 1
        return i, fin, sangria
    return None


def bloques(texto):
    """Los nombres de segundo nivel: servicios y volúmenes.

    Se comparan todos y no solo los servicios a propósito: si una edición mal
    hecha se comiera la sección de volúmenes, el contenedor arrancaría sin el
    volumen de la base y empezaría de cero sin que nadie lo note.
    """
    return set(re.findall(r"^  ([A-Za-z0-9_.-]+):\s*$", texto, re.M))


def main():
    destino = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/docker-compose.yml")
    if not destino.exists():
        sys.exit(f"No existe {destino}")
    if not REFERENCIA.exists():
        sys.exit(f"No encuentro el bloque de referencia en {REFERENCIA}")

    original = destino.read_text(encoding="utf-8")
    lineas = original.splitlines(keepends=True)

    # El servicio b2x dentro del compose del servidor.
    inicio_srv = next((i for i, l in enumerate(lineas)
                       if re.match(r"^  b2x:\s*$", l)), None)
    if inicio_srv is None:
        sys.exit("No encontré el servicio 'b2x' en el compose. "
                 "¿Está con otro nombre?")

    sitio = bloque_environment(lineas, inicio_srv + 1)
    if not sitio:
        sys.exit("Encontré el servicio b2x pero no su bloque 'environment:'. "
                 "Mejor mirarlo a mano.")
    ini, fin, sangria = sitio

    # El bloque nuevo, sacado del archivo de referencia y re-sangrado al
    # nivel que tenga el compose del servidor, que puede no ser el mismo.
    ref = REFERENCIA.read_text(encoding="utf-8").splitlines(keepends=True)
    ini_ref = next((i for i, l in enumerate(ref)
                    if re.match(r"^  b2x:\s*$", l)), None)
    sitio_ref = bloque_environment(ref, (ini_ref or 0) + 1)
    if not sitio_ref:
        sys.exit("El archivo de referencia no tiene el bloque environment.")
    ini_r, fin_r, sangria_r = sitio_ref

    nuevas = []
    for l in ref[ini_r:fin_r]:
        if not l.strip():
            nuevas.append(l)
            continue
        actual = len(l) - len(l.lstrip())
        nuevas.append(" " * (actual - sangria_r + sangria) + l.lstrip())

    resultado = "".join(lineas[:ini] + nuevas + lineas[fin:])

    # --- las comprobaciones, antes de escribir ---------------------------
    antes, despues = bloques(original), bloques(resultado)
    if antes != despues:
        sys.exit(f"ABORTADO: cambiarían los servicios o los volúmenes.\n"
                 f"  antes:   {sorted(antes)}\n"
                 f"  después: {sorted(despues)}")

    fuera_antes = len(lineas) - (fin - ini)
    fuera_despues = len(resultado.splitlines(keepends=True)) - len(nuevas)
    if fuera_antes != fuera_despues:
        sys.exit("ABORTADO: se habrían movido líneas fuera del bloque "
                 f"environment ({fuera_antes} -> {fuera_despues}).")

    if "KOMMO_TOKEN" not in resultado:
        sys.exit("ABORTADO: el bloque nuevo no nombra KOMMO_TOKEN.")

    copia = destino.with_suffix(destino.suffix + ".bak")
    shutil.copy2(destino, copia)
    destino.write_text(resultado, encoding="utf-8")

    quitadas = fin - ini - 1
    print(f"Respaldo: {copia}")
    print(f"Servicios y volúmenes intactos: {', '.join(sorted(antes))}")
    print(f"El bloque environment de b2x pasó de {quitadas} a "
          f"{len(nuevas) - 1} líneas.")
    print()
    print("Ahora comprobá que el compose se entiende y redesplegá:")
    print(f"  cd {destino.parent} && docker compose config -q && "
          f"docker compose up -d --build b2x")
    print()
    print(f"Si algo sale mal: cp {copia} {destino}")


if __name__ == "__main__":
    main()
