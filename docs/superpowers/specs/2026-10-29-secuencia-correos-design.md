# Secuencia de tres correos y la IA que lee las respuestas

**Estado:** diseño, para evaluar con el equipo. **No va a producción** hasta
que Pablo lo apruebe: se construye en la rama `secuencia-correos` y no se
mezcla con `main`, que es lo que despliega el servidor.

## Para qué

Hoy B2K manda **un** correo por lead y en Kommo no pasa nada: la fila "Se le
envió el correo" del mapa de etapas existe en pantalla pero nunca se dispara,
porque el envío no deja ningún registro. Las respuestas se detectan por la
cabecera del correo, pero **nadie las lee**: el cuerpo ni siquiera se guarda,
así que todas caen en la misma etapa digan lo que digan.

El equipo quiere que B2K trabaje el embudo B2K de Kommo por su cuenta. Que le
escriba tres veces a quien no contesta, que lo mueva de etapa con cada correo
y que, cuando alguien contesta, lea la respuesta y lo ponga donde corresponde.
Así el comercial abre Kommo y encuentra cada lead en su lugar, con una
etiqueta que dice dónde está, sin haber movido nada a mano.

## Lo que decidió Pablo

- **B2K manda los tres correos solo**, espaciados en el tiempo. Nadie arma
  una campaña nueva para el segundo ni para el tercero.
- **Los correos 2 y 3 son claramente distintos al 1.** No son recordatorios
  del primero. Los nombres de las etapas marcan el tema de cada uno:
  Descubrimiento, Diagnóstico y Oportunidad.
- **Cada envío mueve el lead:**

  | Sale | El lead pasa a |
  |---|---|
  | Correo 1 | CORREO #1 - DESCUBRIMIENTO |
  | Correo 2 | CORREO #2 - DIAGNÓSTICO |
  | Correo 3 | CORREO #3 - OPORTUNIDAD |
  | Pasa el plazo después del 3 sin respuesta | NO INTERESADO |

- **Los correos 2 y 3 son solo para quien no contestó.** Apenas alguien
  responde, a cualquiera de los tres, la secuencia se corta para él.
- **Cuando contesta, el lead pasa a IA CONVERSANDO, diga lo que diga.**
  Lo decidió el equipo el 29 de octubre de 2026 y reemplaza la tabla de
  respuesta → etapa que estaba acá. La IA igual clasifica la respuesta
  (interesado, propone o confirma reunión, no interesado, no escribir, otra
  persona, no entiende) y lo guarda, pero eso no decide la etapa: queda como
  dato para quien sigue la conversación.
- **"No me escriban más" pasa a IA CONVERSANDO como cualquier respuesta**,
  pero además entra a la lista de supresión: no recibe ningún correo más, ni
  de la secuencia ni de otra campaña.
- **Abrir o tocar un enlace no es contestar.** No corta la secuencia ni mueve
  nada.
- **La etiqueta se reemplaza.** El lead lleva una sola etiqueta de etapa, la
  actual; la anterior se saca. Las demás etiquetas del lead, como la de la
  campaña o las que haya puesto el equipo, no se tocan.
- **Rebote y baja sacan al lead de la secuencia**, como ya pasa hoy.

## Objetivos

1. Que un lead que entra a la secuencia reciba hasta tres correos sin que
   nadie intervenga, y que en Kommo esté siempre en la etapa que corresponde a
   lo último que pasó.
2. Que cada respuesta real termine en la etapa que le corresponde según lo que
   dice, no según el solo hecho de haber contestado.
3. Que el equipo pueda ver desde B2K en qué paso está cada lead y por qué
   salió de la secuencia.

## Lo que no es

- **No es la IA que conversa.** No se le manda nada al cliente en respuesta a
  lo que escribió.
- **No es multi-cliente.** Corre sobre la cuenta única de gmarketing, como
  todo B2K hoy.
- **No es WhatsApp ni llamadas.** Las etapas WHATSAPP y LLAMADA IA del embudo
  quedan como están.

## Lo que lo haría un fracaso aunque funcione

- **Escribirle el correo 2 a alguien que ya contestó el 1.** Es la falla más
  visible y la que más daño le hace a la marca. Ante cualquier duda sobre si
  hubo respuesta, se frena.
- **Mover hacia atrás un lead que un comercial ya avanzó a mano.** La regla de
  hoy se mantiene: B2K nunca mueve hacia atrás y no toca ganados ni perdidos.
- **Que una respuesta automática corte la secuencia.** "Estoy de vacaciones"
  no es contestar. Hoy ya se descartan esos correos y tiene que seguir así.
- **Que una apertura de antivirus cuente como interés.** Hoy ya se filtran y
  tiene que seguir así.
- **Que B2K pise lo que hizo el equipo.** Si un comercial ya está trabajando
  el lead, B2K no le manda más correos.

## El embudo B2K, tal como está en Kommo

Leído de la API el 29 de octubre de 2026 (embudo `14420808`), en orden:

| Orden | Id | Etapa |
|---|---|---|
| 20 | 111388072 | LEAD IMPORTADO |
| 40 | 111388080 | CORREO #1 - DESCUBRIMIENTO |
| 50 | 111474760 | CORREO #2 - DIAGNÓSTICO |
| 60 | 111474764 | CORREO #3 - OPORTUNIDAD |
| 100 | 111474780 | IA CONVERSANDO |
| 10000 | **142** | SESIÓN EJECUTIVA AGENDADA |
| 11000 | **143** | NO INTERESADO |

Hay otras etapas en el medio —WhatsApp, IA conversando, llamadas—, pero la
secuencia no las usa.

**SESIÓN EJECUTIVA AGENDADA es el estado de ganado de Kommo (142) y NO
INTERESADO el de perdido (143)**, renombrados. Mandar un lead a cualquiera
de los dos **lo cierra**, y B2K no vuelve a tocar un lead cerrado. La
secuencia solo usa NO INTERESADO, cuando pasa el plazo después del tercer
correo sin respuesta.

IA CONVERSANDO está en el orden 100, después de los tres correos, así que
pasar ahí desde cualquier paso de la secuencia es avanzar. Si un comercial ya
llevó el lead más lejos —a una llamada comercial, por ejemplo—, la regla de
no mover hacia atrás lo deja donde está.

## Restricciones

- **Aprobación antes de que salga.** Ningún correo escrito por la IA sale sin
  que una persona lo lea y lo apruebe. Es una regla del equipo desde que
  existen los correos con IA, y se mantiene para los tres pasos.
- **El límite de Kommo**, de unos 7 pedidos por segundo por cuenta. Se usa el
  control de ritmo compartido que ya existe en `kommo.py`.
- **El horario y el goteo de hoy.** Los correos 2 y 3 salen dentro de la
  ventana de envío configurada y respetan el tope diario de cada buzón, igual
  que el 1.
- **La base tiene datos de producción.** Las migraciones agregan columnas o
  tablas y no rehacen tablas existentes. Se prueban contra una copia limpia,
  sin la migración aplicada (ver la nota del proyecto sobre la caída de
  septiembre de 2026).
- **El interruptor del correo.** Si el envío está apagado en Ajustes →
  Sistema, la secuencia queda en pausa igual que las campañas.

## Decisiones que tomé yo

Pablo no las definió. Las dejo marcadas para que se puedan revisar:

- **El plazo entre correos es configurable**, con valores de fábrica de
  **3 días hábiles** entre el 1 y el 2 y entre el 2 y el 3, y **5 días
  hábiles** después del 3 antes de mandarlo a NO INTERESADO. Se cuentan desde
  que salió el correo anterior, no desde que se armó la secuencia.
- **La nota de la respuesta.** Al pasar a IA CONVERSANDO, B2K deja en el
  lead una nota con lo que escribió el cliente y la clase que le puso la IA,
  para que quien sigue la conversación no tenga que ir a buscarla al buzón.
- **Si un comercial movió el lead a una etapa que no es de la secuencia**, la
  secuencia se corta para ese lead: alguien ya lo está trabajando. Se mira
  antes de cada envío.
- **Las etapas y la etiqueta se configuran** en Ajustes → Sistema → Etapas en
  Kommo. Ahí se reemplaza la fila muerta "Se le envió el correo" por una fila
  por paso, más una por cada desenlace de la respuesta. Viene precargado con
  las etapas de las tablas de arriba, buscadas por nombre en el embudo B2K.
- **La etiqueta es el nombre de la etapa**, tal cual figura en Kommo, para
  que filtrar por etiqueta y por etapa dé lo mismo.
- **La respuesta se guarda en B2K** sin el historial citado de abajo, que es
  nuestro propio correo repetido. Así la IA lee solo lo que escribió el
  cliente y la persona que revisa también.
- **Quién entra a la secuencia.** Al armar una campaña se elige si es de un
  solo correo, como hoy, o una secuencia de tres. Entran los contactos que se
  marquen en esa campaña y que **nunca recibieron un correo**: los que ya
  recibieron el primero antes de que existiera la secuencia no se suman solos,
  para que el día que se encienda no salga una tanda acumulada.
- **Contactos sin lead en Kommo** reciben igual los tres correos, pero no se
  mueve nada porque no hay qué mover. **Contactos con el lead en otro
  embudo** no entran a la secuencia: moverlos al embudo B2K sería sacarlos
  del lugar donde los puso alguien.
- **Cómo se sabe que un comercial movió el lead.** B2K anota la última etapa
  a la que lo llevó él. Antes de cada envío compara con la etapa real en
  Kommo: si no coinciden, alguien lo movió y la secuencia se corta.
- **Las filas de hoy para «Abrió» y «Tocó un enlace»** se ignoran mientras
  el lead está en la secuencia: abrir no mueve nada. La fila «Contestó» es la
  que lo lleva a IA CONVERSANDO. Para los leads que no están en una secuencia siguen
  funcionando como hoy.
- **El 2 y el 3 salen del mismo buzón que el 1**, con el mismo remitente y en
  el mismo hilo de conversación. Que el seguimiento llegue de otra dirección
  parece otra empresa y huele a spam. Si ese buzón está en pausa, el correo
  espera a que vuelva.
- **Sin lectura del buzón no hay segundo correo.** Si el buzón que mandó el 1
  no se pudo leer después de que salió, B2K no sabe si el cliente contestó, y
  ante la duda se frena: el 2 y el 3 esperan hasta que la lectura funcione.
  Un buzón sin IMAP configurado no puede usarse para una secuencia.
- **La IA que clasifica usa el modelo general** (`OPENAI_MODEL`), con salida
  estructurada: una etiqueta de la tabla, un nivel de confianza y una frase
  que diga por qué. Si la confianza es baja, cuenta como "no entiende".

## Lo que queda para quien lo construya

- La forma exacta de guardar el paso de cada lead y la respuesta, siempre que
  sea agregando y no rehaciendo tablas.
- Cómo se ve la secuencia en la pantalla de Correos, siguiendo los patrones
  que ya tiene la app.
- Cómo reemplazar la etiqueta en Kommo sin tocar las demás.

## Preguntas abiertas para el equipo

Resuelta el 29 de octubre de 2026: si proponer una reunión cuenta como
agendada ya no importa, porque toda respuesta va a IA CONVERSANDO.

1. **¿Quién escribe los correos 2 y 3, y cuándo se aprueban?** Hoy la IA
   escribe uno por empresa a partir de lo que leyó en su sitio, y cada uno se
   aprueba antes de salir. Si escribe también el 2 y el 3, hay que decidir si
   se aprueban los tres juntos al armar la secuencia o cada uno cuando le toca
   salir. Lo segundo obliga a alguien a entrar a B2K cada pocos días, y hay
   que decidir qué pasa si llega el día y nadie lo aprobó; lo primero triplica
   lo que hay que leer de entrada. También se puede escribir el 2 y el 3 como
   plantilla fija, igual para todos, y que la IA escriba solo el 1.
