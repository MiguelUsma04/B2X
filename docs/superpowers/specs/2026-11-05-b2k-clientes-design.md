# B2K para clientes — diseño

Fecha: 5 de noviembre de 2026 · Pedido por Pablo (gmarketing.co)

## Para qué

B2K deja de ser solo la herramienta interna de gmarketing y pasa a ser un
producto que gmarketing **le vende a otras empresas**. Todavía no hay clientes
definidos: se construye para poder venderlo.

Cada cliente hace con B2K **lo mismo que hace gmarketing hoy** —buscar en Maps o
cargar un CSV, encontrar correos, leer sitios, armar fichas, mandar a **su**
Kommo, escribir con la IA la secuencia de tres correos desde **sus** buzones, ver
su Dashboard y sus resultados— en un espacio **totalmente separado** de
gmarketing y de los demás clientes.

gmarketing necesita además **controlar a quién le vendió**: ver sus clientes y
poder **bloquear** a uno que no paga.

## Lo que decidió Pablo

1. **Totalmente separado.** Un cliente no ve nada de gmarketing ni de otro
   cliente.
2. **Marca.** El cliente puede poner su logo y sus colores, pero **siempre** debe
   decir que la app la desarrolló gmarketing. Eso no se puede quitar.
3. **Panel de Clientes** en la B2K de gmarketing, para controlar a quién se le
   vendió.
4. **Bloquear frena absolutamente todo**: no puede entrar, y quedan en pausa sus
   correos programados, sus secuencias, la lectura de sus buzones y lo que B2K
   hace en su Kommo. No se borra nada; al desbloquear sigue donde quedó.
5. De antes (documento de decisiones): la cuenta la crea gmarketing con el correo
   del cliente, y el cliente conecta **su propio Kommo**.
6. **Quedan para después**: quién paga las APIs (Maps, OpenAI, buscadores de
   correo) y cómo entran las personas de cada cliente.
7. **Soporte**: gmarketing puede entrar a la B2K de un cliente para darle
   soporte. Queda anotado cada vez.
8. **Cada cliente mide con su propio dominio.** Los enlaces de apertura, clic y
   baja de sus correos van por un subdominio suyo (por ejemplo
   `links.viajesabc.com`), nunca por un dominio de gmarketing. Mientras no lo
   conecte, sus correos salen sin medir aperturas ni clics y la baja es por
   respuesta.
9. **Conectar un buzón tiene que ser sencillo**, para los clientes y para
   gmarketing, y agregar otra cuenta también. Un asistente que hace todo solo
   ahora; el botón «Conectar con Microsoft» también ahora; el botón «Conectar
   con Google» más adelante, cuando se justifique la auditoría que pide Google.

## Objetivos

- Un cliente nuevo queda andando desde el panel, sin tocar el servidor.
- Cada cliente conecta su Kommo, sus buzones y (si corresponde) sus claves de
  API desde **Ajustes**, sin pedirle nada a gmarketing.
- gmarketing ve en el panel quién usa B2K y cuánto, y bloquea o desbloquea con un
  botón.
- La B2K de gmarketing sigue funcionando **exactamente igual**, con su base, sus
  campañas en curso y sus enlaces de correos ya enviados.

## Lo que no es (por ahora)

- Cobro dentro de la app. El panel bloquea; cobrar se hace por fuera.
- Que el cliente entre a la app por su propio dominio (`b2k.cliente.com`).
  Todos entran por `b2k.gmarketing.co`; el dominio propio es solo para los
  enlaces de los correos.
- Que el cliente cree sus propios usuarios o entre con Google.
- Planes con límites distintos por cliente, y topes de gasto (decisión de
  Claude: el panel muestra el gasto; frenarlo queda para cuando se decida quién
  paga).
- Borrar un cliente (decisión de Claude: en esta versión se bloquea y sus datos
  se pueden descargar; su clave queda reservada para siempre).
- El botón «Conectar con Google» (pide una auditoría de seguridad de Google que
  cuesta varios cientos a miles de dólares por año).

## Lo que haría que esto falle aunque funcione

- **Que un cliente use algo de gmarketing sin que nadie lo decida**: su Kommo,
  sus buzones, su texto comercial («Qué ofrecemos» de KOMMO10X) o sus claves de
  API. Hoy casi todo eso sale del `.env` del servidor; para un cliente, el `.env`
  **no** puede ser el valor por defecto de nada que sea de gmarketing.
- **Que un cliente vea un dato de otro** por un error de enrutamiento: una tanda
  que corre en segundo plano con la base equivocada, un caché de campos de Kommo
  compartido, un progreso de «escribiendo correos» que muestra los de otro.
- **Que un bloqueo deje de frenar algo**: un goteo que sigue mandando o una
  secuencia que mueve leads.
- **Que la baja deje de funcionar.** Aunque el cliente esté bloqueado, quien
  recibió un correo suyo y pide no recibir más **tiene** que poder darse de baja:
  es lo que pide la ley y lo que protege el dominio.
- **Que un correo de un cliente lleve un dominio de gmarketing.** Si un cliente
  manda spam, quema los dominios que aparecen en sus correos; ninguno puede ser
  de gmarketing ni de otro cliente.
- Que se rompan los enlaces (apertura, clic, baja) de los correos que gmarketing
  **ya mandó**.

## Cómo se arma (decisiones de Claude, para revisar)

### Una base por cliente

- La B2K de gmarketing sigue con su base de siempre, `data/b2x.db`. No se mueve
  ni se migra de lugar.
- Cada cliente tiene su carpeta `data/clientes/<clave>/` con su propia
  `b2x.db`, sus respaldos y su logo. `<clave>` es un nombre corto, en minúsculas
  y sin espacios, que se elige al crearlo (por ejemplo `viajesabc`) y no cambia.
- El registro de clientes vive aparte, en `data/clientes.db`: clave, nombre,
  correo de contacto, estado (activo, bloqueado), fechas, el usuario de ingreso
  (correo y contraseña cifrada) y si usa las claves de API de gmarketing.
- Un solo servidor y un solo contenedor para todos. La base que se usa en cada
  momento sale de un **contexto por pedido** (contextvar): el ingreso dice de qué
  cliente es la sesión, y todo lo que se hace en ese pedido —incluidas las
  tareas que lanza— usa la base de ese cliente. `get_db()` lee ese contexto.
- Las migraciones corren **por archivo**: hoy hay una sola bandera para todo el
  proceso, y con varias bases solo se migraría la primera.

Por qué así y no una copia del servidor por cliente: el panel tiene que poder
crear y bloquear clientes al instante y sin tocar el servidor, y con un
contenedor por cliente cada alta sería un despliegue. Por qué no una sola base
con una columna de cliente en cada tabla: eso toca casi todas las consultas, y
un solo `WHERE` olvidado le muestra a un cliente los datos de otro.

### El ingreso

- Todos entran por `b2k.gmarketing.co`. La pantalla de ingreso es la de B2K, con
  «Desarrollada por gmarketing».
- gmarketing entra como hoy (Google @gmarketing.co o la contraseña del
  servidor), a su propia B2K, que además tiene el panel de Clientes.
- **Mientras se define el ingreso de los clientes:** cada cliente tiene un
  usuario, con correo y contraseña, que gmarketing crea en el panel. La
  contraseña se muestra una sola vez al crearla y se puede regenerar desde el
  panel. Dejarlo así no impide sumar Google o varios usuarios después.
- La sesión lleva la clave del cliente. En **cada pedido** se revisa que el
  cliente siga activo: bloquear corta también las sesiones abiertas, no solo el
  próximo ingreso.

### Lo que hoy sale del servidor y pasa a ser de cada cliente

Todo esto vive en la tabla `ajustes` de la base de cada cliente y se configura
desde su pantalla de Ajustes:

- Kommo: subdominio, token, responsable, etiqueta, región del teléfono. (Embudo,
  etapa y etapas por hecho ya están en `ajustes`.)
- El texto comercial por defecto («Qué ofrecemos»), el nombre y la dirección de
  la empresa para el pie del correo, el idioma y el país por defecto, el horario
  de envío.
- Las claves de API: Google Maps, OpenAI, Prospeo, Hunter.
- La marca: nombre de la app, logo y color principal.

Para la B2K de gmarketing, si un ajuste está vacío se sigue leyendo del `.env`,
como hoy. **Para un cliente, nunca**: un ajuste vacío es un ajuste vacío, y la
pantalla dice qué falta conectar.

La única excepción son las claves de API, mientras no se decida quién las paga:
cada cliente tiene en el panel un interruptor **«Usa las claves de
gmarketing»**. Prendido, si el cliente no cargó una clave propia se usa la de
gmarketing. Apagado, el cliente tiene que cargar las suyas. Así la decisión de
quién paga se vuelve un interruptor, en vez de otra obra. Arranca **prendido**
para que un cliente nuevo pueda trabajar desde el primer día; el panel muestra
cuánto gastó cada uno para que gmarketing lo vea.

GoHighLevel queda solo para gmarketing: un cliente no lo ve.

### Lo que corre solo

Los cuatro ciclos de fondo (goteo, buzones, secuencias, sincronización con
Kommo) y el respaldo diario hoy recorren una sola base. Pasan a recorrer **cada
base activa**, una por vez, con el contexto puesto en ese cliente. Un cliente
bloqueado se saltea entero. Un error en un cliente no frena a los demás.

Los estados que hoy son únicos en todo el proceso pasan a ser por cliente: los
progresos (correos, sitios, fichas, celulares, redacción), la actividad en
curso, las cargas pendientes de confirmar, los cachés de campos y de etapas de
Kommo, y el control de ritmo de Kommo (el límite es por cuenta de Kommo, y cada
cliente tiene la suya).

### Los enlaces de los correos: el dominio de seguimiento del cliente

- En Ajustes, el cliente escribe un subdominio suyo (por ejemplo
  `links.viajesabc.com`). B2K le muestra el **único registro DNS** que tiene que
  agregar (un CNAME a `b2k.gmarketing.co`) y le avisa cuando ya resuelve bien.
- El certificado del subdominio lo saca el servidor solo. Para eso se hace
  **una sola vez** un ajuste en el servidor: Traefik lee un archivo de
  configuración dinámica que B2K escribe con los dominios de los clientes
  conectados. Hoy cada dominio va como etiqueta en el `docker-compose`, y eso
  pediría redesplegar con cada cliente nuevo.
- Un pedido que entra por el dominio de un cliente se resuelve a la base de ese
  cliente por el dominio mismo. En esos dominios **solo** responden las rutas de
  rastreo y de baja; la app no.
- **Sin dominio conectado** (o mientras no resuelve): sus correos salen sin
  pixel y sin desvío de enlaces, el pie dice «Si prefieres que no te escriba
  más, respóndeme «no»» sin enlace, y el `List-Unsubscribe` es un `mailto:` al
  buzón que envió. B2K ya da de baja a quien contesta «no» al leer el buzón.
  Dashboard y Resultados le muestran un aviso para conectar el dominio.
- La B2K de gmarketing sigue con `b2k.gmarketing.co` como hoy, y los enlaces de
  los correos que ya mandó siguen andando.

Con el cliente **bloqueado**: la baja **sigue funcionando**. La apertura y el
clic no se anotan, pero el clic igual lleva a la persona a donde iba.

**La baja de un cliente vale solo para ese cliente** (decisión de Claude): queda
en su base, no pasa a la lista de gmarketing ni a la de otros clientes, y un
cliente nuevo no hereda la lista de gmarketing. Es lo que pide «totalmente
separado».

### Conectar un buzón

Hoy se llena servidor, puerto, seguridad, usuario y contraseña, para enviar y
para leer. Pasa a ser un asistente, en la B2K de los clientes y en la de
gmarketing:

1. Se escribe el correo.
2. B2K mira los registros MX del dominio, reconoce el proveedor (Google
   Workspace/Gmail, Microsoft 365/Outlook, Hostinger, Zoho y los que se usen) y
   pone solo los datos de envío y de lectura.
3. Muestra el paso a paso **de ese proveedor** para sacar la contraseña de
   aplicación, con el enlace directo.
4. Se pega la contraseña y B2K prueba en el momento que puede enviar y que
   puede leer. Si falla, dice qué hacer en palabras comunes.
5. «Conectar otra cuenta» repite lo mismo. La configuración a mano queda
   escondida como «Configuración manual», para casos raros.

**«Conectar con Microsoft»**: un botón que entra con la cuenta de Microsoft
(OAuth) y deja el buzón conectado sin contraseña de aplicación, enviando y
leyendo con XOAUTH2. Microsoft viene apagando el envío con contraseña en las
cuentas de Microsoft 365, así que para esas cuentas puede ser el único camino:
si el asistente detecta Microsoft y la contraseña es rechazada, lleva al botón.
Requiere que gmarketing registre la app B2K una vez en Microsoft (Azure, gratis)
y complete la verificación de editor (programa de socios, gratis, tarda unos
días). Hasta que esté verificada, el botón funciona pero Microsoft muestra un
aviso de «app no verificada», y algunas empresas exigen que su administrador lo
apruebe.

**«Conectar con Google»** queda para más adelante: el permiso de correo de
Google es «restringido» y para clientes externos exige verificación y una
auditoría de seguridad paga.

### El panel de Clientes

Solo en la B2K de gmarketing. Una pantalla más en el menú, **Clientes**:

- La lista: nombre, estado, fecha de alta, último ingreso, empresas en la base,
  correos enviados en los últimos 30 días y lo que gastó en APIs de gmarketing.
- **Nuevo cliente**: nombre, clave, correo de contacto y correo de ingreso. Al
  guardar se crea su base vacía y se muestra la contraseña una sola vez.
- Por cliente: **Bloquear / Desbloquear** (con confirmación), **Nueva
  contraseña**, el interruptor **Usa las claves de gmarketing** y **Entrar como
  este cliente**.
- **Entrar como este cliente** abre su B2K tal como la ve él, con una franja
  fija arriba que dice que se está viendo la de ese cliente y un botón para
  volver. Cada entrada queda anotada en el Historial **del cliente** (quién de
  gmarketing entró y cuándo) y en el de gmarketing. Un cliente bloqueado
  también se puede abrir así, para revisar; los ciclos de fondo siguen frenados.
- Queda anotado en el Historial quién bloqueó, desbloqueó o creó a quién.
- El gasto de las claves de gmarketing se cuenta por cliente y por mes; no hay
  tope todavía.

Un cliente no ve esta pantalla ni puede llamar a sus rutas.

### La marca

- En Ajustes de cada cliente: nombre de la app, logo y color principal. El logo
  se guarda en su carpeta y se sirve solo a sus sesiones.
- El color principal reemplaza al verde de la marca (`--brand`) en botones y
  acentos. Se rechaza un color con el que el texto no se lea.
- **Siempre**, y sin forma de quitarlo: «Desarrollada por gmarketing» al pie de
  la app y en la pantalla de ingreso.
- Para un cliente se van los textos que nombran a gmarketing, a Kommo10X o a sus
  dominios como si fueran suyos (los ejemplos en la pantalla, la plantilla de
  correo por defecto). El manual de uso dentro de la app pasa a ser genérico.

### Bloqueo

Al bloquear: no puede entrar, sus sesiones abiertas se cortan, los ciclos de
fondo lo saltean (no sale ningún correo ni seguimiento, no se leen sus buzones,
no se mueve nada en su Kommo) y lo que estaba en marcha a pedido (redacción,
lectura de sitios) se corta al terminar el contacto que estaba haciendo. Al
desbloquear, todo sigue donde quedó. Las secuencias cuentan el plazo desde el
último correo: si el bloqueo duró una semana, el siguiente seguimiento sale en
la próxima vuelta, no se manda una tanda de golpe.

## Lo que queda para quien lo construye

- Cómo se ve el panel de Clientes y la parte de marca en Ajustes, siguiendo el
  diseño actual de la app (oscuro, íconos, ⓘ, primero el teléfono).
- El formato exacto de la clave y de los enlaces de rastreo.
- Cómo se cuenta el gasto en APIs por cliente (alcanza con contar llamadas por
  servicio por mes).

## Cómo se prueba

- Dos clientes de prueba con datos distintos: ninguna pantalla, ninguna ruta y
  ningún ciclo de fondo muestra o toca los datos del otro ni los de gmarketing.
- Un cliente sin Kommo ni claves propias: la app dice qué falta, y nada sale con
  lo de gmarketing salvo las claves de API con el interruptor prendido.
- Bloquear con una campaña y una secuencia en marcha: no sale nada, la sesión
  abierta se corta, y la baja de un correo ya enviado sigue funcionando.
  Desbloquear: sigue.
- La B2K de gmarketing: todas las pruebas de hoy siguen pasando, y un enlace de
  un correo enviado antes del cambio sigue anotando la apertura y la baja.
- Un cliente sin dominio de seguimiento: ningún correo suyo lleva un enlace ni
  una imagen de un dominio de gmarketing, y contestar «no» lo da de baja. Con el
  dominio conectado: la apertura, el clic y la baja se anotan en su base.
- El asistente de buzones reconoce Google, Microsoft, Hostinger y Zoho por sus
  MX, y con una contraseña mala dice qué hacer.
- Entrar como un cliente desde el panel: se ve su B2K y queda anotado en su
  Historial.
