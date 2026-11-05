# Rediseño de la experiencia de B2K

**Estado:** aprobado por Pablo el 5 de noviembre de 2026 a partir de las
maquetas (`.superpowers/brainstorm/1275-1793896047/content/rediseno-v2.html`):
«me gusta como se ve, despliégalo». Va directo a `main`.

## Para qué

Pablo: el diseño está bien pero **no es intuitivo**. A simple vista no se sabe
qué hay que hacer, sobre todo desde que se marcan contactos en Contactos hasta
Ajustes. La gente no lee bloques de texto: **el propio entorno gráfico tiene
que decir qué se puede hacer**. Lo que necesite explicación va escondido detrás
de un ícono de información que se despliega al tocarlo.

Tiene que cumplir las normas de UX/UI y ser 100% responsive, pensado primero
para el teléfono.

## Lo que pidió Pablo

- Poco texto. Ningún párrafo explicativo a la vista: la explicación va en un
  ícono **ⓘ** que se abre al tocarlo.
- **Íconos, no emojis.** Los emojis le quitan seriedad. Vale para toda la app,
  incluido el menú actual (⬆ ✉ ◆ ✈ 📈 ⚙).
- Que se vea como en las maquetas aprobadas.

## Lo que mostraron las maquetas (aprobado)

**En toda la app**
- Cada pantalla abre con un título que es una orden corta («Elegí a quién
  escribirle») y, al lado, un ⓘ con la explicación.
- El menú marca con ✓ los pasos ya hechos.
- Las métricas grandes (contactables, con email, con celular, en Kommo, falta
  buscar, quién encontró cada email) quedan **solo en Contactos y como una fila
  de pastillas**. En el resto de las pantallas no aparecen.
- Nada flotante tapa el contenido: cuando hay una barra abajo, la página deja
  ese espacio libre.
- Lo que no se deshace va separado, en rojo, y siempre pide confirmar.

**Contactos con marcados**
- Cada fila muestra en chips el estado: email (✓ / ✕) y Kommo (sin Kommo / En Kommo).
- Barra de selección apoyada abajo: «N marcados», botón principal
  **Escribirles** (lleva a Correos con los marcados), **Mandar a Kommo**, y un
  **⋯** que abre: Ver el detalle, Exportar a Excel, Desmarcar, Borrar (en rojo).
- En el teléfono la barra va arriba de la barra de secciones, con los botones a
  todo el ancho.

**Correos como cuatro pasos**
- Arriba, una fila de pasos: 1 Qué mandar · 2 Revisar · 3 Seguimiento ·
  4 Cuándo sale. El hecho lleva ✓, el actual va resaltado. En el teléfono solo
  el actual muestra su nombre.
- Paso 1: tres tarjetas grandes con ícono — **Lo escribe la IA** (marcada como
  recomendada), **Texto**, **Con diseño**.
- Paso 2: el editor de siempre (texto o diseño) o los borradores de la IA.
- Paso 3: interruptor «Si no contesta, volver a escribirle» y una **línea de
  tiempo**: Correo 1 → [3] días → Correo 2 → [3] días → Correo 3 → [5] días →
  No interesado; debajo, «Si contesta en cualquier momento → IA conversando».
  Los días se editan en la misma línea. En el teléfono, vertical.
- Paso 4: el ritmo del envío.
- Pie fijo con el resumen («3 correos · escritos por la IA · con seguimiento ·
  desde 2 buzones») y los botones **Atrás** / **Siguiente**; en el último paso,
  el principal es **Programar el envío**.

**Ajustes por lo que se usa**
- En vez de pestañas, tarjetas agrupadas en **Envío** (Buzones, Horario, Salud
  del envío), **Kommo** (Embudo y etapa, Etapas automáticas, Repetidos) y
  **Cuenta** (Historial, Copia de la base, Cómo usar B2K).
- Cada tarjeta dice cómo está, con un punto de color (verde bien, amarillo
  para revisar, gris sin configurar): «2 de 4 activos», «8 a 19, lun a vie»,
  «B2K · Lead importado», «hoy 10:08».
- Abajo, cerrada, una **Zona de cuidado** en rojo: empezar de cero con Kommo y
  apagar los correos.

## Objetivos

1. Que alguien que nunca usó B2K sepa qué tocar en cada pantalla sin leer
   párrafos.
2. Que lo importante de cada pantalla —la tarea— esté arriba, a la vista sin
   desplazarse, en el teléfono y en el computador.
3. Que nada de lo que funciona hoy deje de funcionar.

## Lo que no es

- No cambia lo que hace B2K por detrás: los mismos datos, las mismas reglas,
  las mismas rutas del servidor.
- No es una app nueva: es la misma página, reorganizada.

## Lo que lo haría un fracaso aunque se vea lindo

- Romper un flujo que hoy anda: lanzar campañas, aprobar borradores, mandar a
  Kommo, las pruebas, la secuencia.
- Esconder detrás de un ⓘ algo que hace falta para decidir (cuántos correos
  salen, desde qué buzón, si va con seguimiento). Eso queda a la vista; lo que
  se esconde es el **porqué**.
- Que en el teléfono algo quede tapado, cortado o haya que hacer zoom.

## Restricciones

- JavaScript sin framework, como hoy (`app/static/app.js`), y el mismo CSS con
  sus tokens de color (`app/static/style.css`). Sin librerías nuevas.
- Los `id` que usa el JavaScript se conservan: el rediseño mueve y envuelve,
  no renombra.
- Accesibilidad: objetivos de toque de 44 px, foco visible, contraste AA,
  `aria-expanded` en los ⓘ y en ⋯, se cierran con Escape y tocando afuera, y
  respetan `prefers-reduced-motion`.
- El manual (`app/templates/manual.html`) se actualiza en el mismo cierre y se
  republica en su URL de siempre.

## Decisiones que tomé yo

- **Íconos:** un juego de íconos de línea dibujados en SVG dentro de la propia
  página (estilo Lucide, licencia libre), sin pedirlos a otro servidor. Uno por
  sección del menú, por tarjeta de Ajustes, por tipo de correo y por acción.
- **Cuándo un paso del menú lleva ✓:** Cargar, si hay contactos; Emails, si ya
  no queda ninguno por buscar o se encontró al menos uno; Contactos, si hay
  alguno en Kommo; Correos, si hay al menos una campaña; Resultados y Ajustes
  nunca llevan ✓.
- **Los pasos de Correos no se bloquean:** se puede tocar cualquiera. El pie
  avisa lo que falta («falta el asunto», «ningún correo aprobado») y el botón
  Programar queda deshabilitado hasta que esté todo.
- **Ajustes:** cada tarjeta abre el contenido que ya existe hoy para esa
  función, en el lugar de la grilla, con un «← Ajustes» para volver. El
  interruptor «Mandar correos desde B2K» pasa a la Zona de cuidado, porque
  apagarlo esconde dos secciones.
- **Cargar, Emails y Resultados** reciben las reglas generales —título corto,
  ⓘ con lo que hoy es párrafo, íconos—, sin reorganizarse: Pablo dijo que el
  problema empieza en la selección.
- **Las explicaciones que hoy son párrafos** pasan al ⓘ de su tarjeta,
  recortadas, no borradas.

### Lo que la maqueta no mostraba (decidido después de la revisión)

Ninguna función de hoy se pierde; se reubica:

1. **Completar datos de los marcados** —ficha con IA, leer sitios, buscar
   celulares— quedan **a la vista debajo de la lista** cuando hay marcados,
   como tres tarjetas chicas con ícono y su costo («gratis», «créditos de IA»,
   «10× créditos»). No van al ⋯: cuestan plata y tienen que verse.
2. **Mandar a Kommo** abre una ventana con **etiqueta, embudo y etapa** ya
   elegidos (los de Ajustes) y editables, y un botón «Mandar N». El resultado
   se ve en la misma ventana —cuántos entraron, cuáles fallaron y por qué— y
   en el chip de cada fila. La tarjeta «Embudo y etapa» de Ajustes fija el
   valor de fábrica.
3. **La lista de Contactos** sigue siendo tabla en el computador y tarjetas en
   el teléfono, con los mismos datos de hoy, pero los estados se muestran como
   chips con ícono: estado del email (verificado, sin verificar, falta buscar,
   no se encontró, rebotó), Kommo (sin enviar, en Kommo, falló — con el error
   en el ⓘ del chip), celular y WhatsApp. La fuente y el resumen de la IA van
   al detalle.
4. **Los textos de los correos 2 y 3** (modo Texto o Diseño) se editan tocando
   «Correo 2» o «Correo 3» en la línea de tiempo del paso 3: se abre su texto
   debajo. En modo IA, esos nodos llevan a los borradores.
5. **«Ver el detalle»** en el ⋯ aparece solo con un marcado; con varios no se
   muestra.
6. **El pie de Correos** cuenta **empresas**: «3 empresas reciben el primer
   correo» (con la IA, solo las aprobadas; en Texto, recortadas por «Cuántos
   enviar»). Con seguimiento, agrega «hasta 3 correos cada una».
7. **Con los correos apagados**, como hoy: desaparecen Correos, Resultados,
   «Escribirles» y las tarjetas de Envío. Prenderlos de nuevo se hace desde la
   Zona de cuidado, sin confirmación; apagarlos sí la pide.

## Lo que queda para quien lo construya

- El orden de los cambios y cómo probar cada pantalla.
- Los textos exactos de los ⓘ, siguiendo el tono de la app (tuteo hacia los
  clientes, voseo en la interfaz como hoy).
