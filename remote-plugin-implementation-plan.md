# Remote Plugin Support — Implementation Plan

## Objetivo

Cuando connpy opera en modo remoto, el usuario puede usar plugins instalados **solo en el server**. La ejecución es completamente transparente: el cliente construye el argparse localmente (usando el source descargado del server) y todo lo demás corre en el server vía gRPC streaming.

---

## Arquitectura

```
Cliente                              Server
───────                              ──────
connpy aws vpc vpc-123
  │
  ├─ init() remoto
  │    ├─ gRPC: list_plugins() → ["aws", "monitor"]
  │    ├─ gRPC: get_plugin_source("aws") → aws.py source (texto)
  │    ├─ Carga Parser en RAM → agrega subcomando al argparse
  │    └─ Marca "aws" como remote_plugin
  │
  ├─ argparse parsea args localmente (usa el Parser descargado)
  │
  └─ dispatch():
       └─ "aws" es remote_plugin
            └─ gRPC: invoke_plugin("aws", args_json)  streaming
                          └─ Entrypoint(args, parser, connapp) ──→ output
                                   ←─── chunks de stdout/stderr ──┘
```

### Regla fundamental
- **`Parser`** → corre en el **cliente** (construye argparse)
- **`Entrypoint`** → corre en el **server** (toda la lógica del plugin)
- El plugin **no sabe** si está siendo ejecutado local o remotamente

---

## Cache de plugins remotos

### Estructura en disco
```
{configdir}/remote_plugins/
├── aws.py        ← source descargado del server
```

### Cuándo se actualiza
Cada vez que `connpy` arranca en modo remoto, descarga y **sobreescribe** la cache en la ruta especificada por el archivo `.folder` activo. Sin hash, sin TTL, sin lógica extra. Siempre fresco.

### Uso por completion.py
`completion.py` incluye `{configdir}/remote_plugins/` como directorio adicional al escanear plugins. Carga `_connpy_tree()` desde el `.py` cacheado y automáticamente le inyecta la función `get_cwd()` para que pueda completar rutas locales sin dependencias extras.

---

## Gestión de plugin: `connpy plugin`

### `--list`: muestra local y remoto

```
connpy plugin --list

LOCAL:
  aws      [active]
  tools    [active]

REMOTE:
  aws      [shadowed]   ← mismo nombre, local tiene prioridad
  monitor  [active]
  deploy   [active]
```

Estados posibles:
| Estado | Significado |
|---|---|
| `[active]`   | Activo y usable |
| `[shadowed]` | Existe pero el otro lado tiene prioridad |
| `[disabled]` | Explícitamente desactivado |

### Prioridad cuando el mismo plugin existe en ambos lados

**Local gana por defecto.** Override guardado en:
```json
// ~/.config/conn/plugin_preferences.json
{
  "aws": "remote"
}
```

### Semántica de `--enable`

Activa el plugin pedido. Si el mismo plugin existe en el otro lado → ese queda shadowed.

```bash
connpy plugin --enable aws           # activa local, remoto queda shadowed
connpy plugin --enable aws --remote  # activa remoto, local queda shadowed
```

### Semántica de `--disable`

Desactiva el plugin indicado. **NO activa automáticamente el del otro lado.**
Permite tener ambos desactivados si el usuario lo desea.

```bash
connpy plugin --disable aws          # desactiva aws local (remoto no cambia)
connpy plugin --disable aws --remote # desactiva aws remoto (local no cambia)
```

### `--add`, `--del`, `--update`

```bash
connpy plugin --add myplug myfile.py          # instala local
connpy plugin --add myplug myfile.py --remote # sube al server vía gRPC
connpy plugin --del myplug                    # borra local
connpy plugin --del myplug --remote           # borra del server
connpy plugin --update myplug myfile.py          # actualiza local
connpy plugin --update myplug myfile.py --remote # actualiza en server
```

---

## Archivos a modificar

| Archivo | Cambio |
|---|---|
| `grpc/connpy_pb2_grpc.py` | Agregar `get_plugin_source` + `invoke_plugin` a PluginService |
| `grpc/connpy_pb2.py` | Agregar `PluginInvokeRequest` + `OutputChunk` messages |
| `grpc/server.py` | Implementar métodos en `PluginServicer` |
| `grpc/stubs.py` | Agregar métodos a `PluginStub` |
| `services/plugin_service.py` | Agregar `get_plugin_source()` + `invoke_plugin()` |
| `connpy/plugins.py` | Remote loading, preferences, `remote_plugins` dict |
| `connapp.py` | Remote plugin init + dispatch proxy |
| `cli/plugin_handler.py` | Flag `--remote`, `--list` unificado, enable/disable con prefs |
| `completion.py` | Incluir cache remoto en `_get_plugins()` |

---

## Nuevo gRPC en `PluginService`

```python
# Mensajes nuevos
class PluginInvokeRequest:
    name: str
    args_json: str   # argparse.Namespace serializado como JSON (solo tipos básicos)

class OutputChunk:
    text: str
    is_error: bool

# Métodos nuevos en PluginService
get_plugin_source(IdRequest) → StringResponse
invoke_plugin(PluginInvokeRequest) → stream OutputChunk
```

---

## Serialización de args

`argparse.Namespace` se serializa filtrando solo tipos básicos (str, int, float, bool, list, None):

```python
args_dict = {k: v for k, v in vars(args_namespace).items()
             if isinstance(v, (str, int, float, bool, list, type(None)))}
```

**Limitación conocida**: plugins remotos no pueden usar `argparse.FileType`. Deben recibir paths como strings y abrir el archivo en el server.

---

## Flujo de enable/disable con conflictos

```
Estado inicial:
  LOCAL:  aws [active]
  REMOTE: aws [shadowed]

connpy plugin --enable aws --remote
  → preferences: {"aws": "remote"}
  LOCAL:  aws [shadowed]
  REMOTE: aws [active]

connpy plugin --disable aws --remote
  → gRPC disable en server, NO toca el local ni el pref
  LOCAL:  aws [shadowed]   ← sigue con pref=remote pero remoto está disabled
  REMOTE: aws [disabled]

connpy plugin --enable aws
  → Borra "aws" del preferences (vuelve a default = local)
  LOCAL:  aws [active]
  REMOTE: aws [shadowed]
```

---

## Captura de output en el server

El server redirige `sys.stdout` durante la ejecución del `Entrypoint` y hace yield de cada línea:

```python
def invoke_plugin(self, name, args_dict):
    import sys, io
    from argparse import Namespace
    args = Namespace(**args_dict)
    old_stdout = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        plugin.Entrypoint(args, parser, connapp)
    finally:
        sys.stdout = old_stdout
    for line in buf.getvalue().splitlines(keepends=True):
        yield line
```

Si el plugin usa Rich o escribe directo al fd, se puede aislar en un subprocess.
