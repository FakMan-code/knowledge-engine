# Cerebro operacional para el NOC

Conocimiento operativo de los servicios de la billetera, construido desde la
evidencia que ya existe en la organizacion, para que un operador del NOC pueda
preguntar en lenguaje natural y obtener una respuesta que se puede abrir en la
fuente.

## Los tres principios

**1. La unidad es el servicio, no el repositorio.** Un repositorio puede
contener varios servicios y un servicio puede vivir en varios repositorios. El
servicio es una entidad resuelta desde evidencia.

**2. Nada se afirma sin poder abrirse en la fuente.** Una afirmacion sin span
es un error de programa, no un dato degradado. La procedencia es obligatoria en
el schema, no una convencion.

**3. Lo que no sabemos es parte de la respuesta.** El reporte de cobertura es
una salida del producto. Es el insumo para saber que data pedir y que
instrumentar.

Consecuencia: los modelos entran tarde. La ingesta es determinista y no usa
ningun modelo. La interpretacion llega despues y nace marcada como inferida.

## Las seis facetas operativas

Identidad, topologia, runtime, senales, falla y cobertura. Son las preguntas
que un operador hace de verdad, y ordenan como se guarda y como se responde.

## Capas

| Capa | Que hace | Estado |
| --- | --- | --- |
| L1 Ingesta | Conectores, blobs por contenido, texto direccionable con span | Lista |
| L2 Extraccion | Evidencia candidata por formato y lenguaje | Pendiente |
| L3 Resolucion | Agrupa evidencia en servicios, resuelve alias | Pendiente |
| L4 Grafo | Entidades, relaciones, claims con procedencia y ciclo de vida | Pendiente |
| L5 Cobertura | Que se sabe y que falta, por servicio y faceta | Pendiente |
| L6 Consulta | Pregunta del operador, respuesta citada, diagrama, accionables | Pendiente |
| L7 Alerta | Una alerta resuelve el servicio y adjunta el procedimiento | Pendiente |
| L8 Bindings | CLI, despues MCP sobre el mismo store, despues Datadog | Pendiente |

## Uso

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

python cli.py ingest <repo-git | carpeta | archivo | texto>
python cli.py doctor        # que formatos se pueden leer hoy
python cli.py ledger        # que paso con cada documento de la ultima corrida
python cli.py gaps          # que no se pudo leer y por que
python cli.py find <texto>  # buscar entre las unidades
python cli.py show <id>     # reabrir una cita en los bytes originales
```

La capa 1 corre con la libreria estandar sola. Las dependencias de
`requirements.txt` estan comentadas y son opcionales: cada una agrega formatos
que se pueden extraer. Si falta una, la ingesta igual completa y deja el
documento registrado como no extraido, con el motivo. `doctor` dice cuales
faltan y que instalar.

### Verificar una cita

`show` lee la unidad de la base y despues corta el fragmento del blob por
separado. Cuando el locator es un rango de lineas y el extractor no normalizo
el texto, la verificacion es byte a byte. Cuando el extractor tuvo que
renderizar, como HTML sin etiquetas o una pagina de PDF, la unidad queda
marcada como no literal y `show` lo dice en lugar de fingir exactitud.

## Datos generados

`store/` y `blobs/` se crean en tiempo de ejecucion y estan excluidos de Git.
Contienen material ingerido de sistemas reales.

## Seguridad

Solo ingerir fuentes autorizadas. La ingesta marca unidades con indicio de
secreto o dato personal para que las capas superiores no las reproduzcan en una
respuesta. Ese marcado es una ayuda, no un control de seguridad: no reemplaza
la revision de que se ingiere.

## Historia

El motor anterior, centrado en repositorios, quedo archivado en el tag
`archive/v0.4-experimental`.
