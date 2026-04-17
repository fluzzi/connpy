# Plan: Auditoría Completa + Descomposición del Monolito connapp.py

## Estado Actual — Auditoría de la Arquitectura

### Servicios (✅ Bien planteados)

| Servicio | Archivo | Responsabilidad | Estado |
|---|---|---|---|
| `BaseService` | `base.py` | Config compartida, hooks, validación de nombres reservados | ✅ OK |
| `NodeService` | `node_service.py` | CRUD nodos/carpetas, list, move, bulk, connect | ✅ OK |
| `ProfileService` | `profile_service.py` | CRUD perfiles, resolución de `@profile` | ✅ OK |
| `ConfigService` | `config_service.py` | Settings, encrypt, config folder | ✅ OK |
| `ExecutionService` | `execution_service.py` | run/test commands en nodos | ✅ OK |
| `ImportExportService` | `import_export_service.py` | YAML import/export | ✅ OK |
| `PluginService` | `plugin_service.py` | add/del/enable/disable plugins | ✅ OK |
| `SystemService` | `system_service.py` | API start/stop/restart/status | ✅ OK |
| `AIService` | `ai_service.py` | ask, sessions, provider config | ⚠️ Parcial* |

> \* `AIService` existe pero `connapp._func_ai` bypasea completamente al servicio e instancia directamente `ai(self.config)`. El servicio solo se usa para `list_sessions` y `delete_session`.

### Excepciones (✅ Limpias)
La jerarquía `ConnpyError > {NodeNotFoundError, ProfileNotFoundError, etc.}` es correcta.

---

## Problemas Detectados

### 🐛 Bug 1: Métodos duplicados en connapp.py

`_case`, `_fzf`, `_idletime`, `_configfolder` y `_ai_config` están definidos **dos veces**:
- Primera vez: líneas ~606-627 (versiones viejas sin feedback, sin try/except)
- Segunda vez: líneas ~735-768 (versiones nuevas con `printer.success`)

Python sobrescribe la primera con la segunda, así que la app funciona, pero el `_func_others` en línea 600 mapea a métodos que llaman a las versiones antiguas (las cuales nunca se ejecutan realmente). **Esto es código muerto que genera confusión.**

### 🐛 Bug 2: `self.config` accesos directos pendientes

Quedan 3 accesos directos a `self.config` que rompen SOA:
- Línea 250: `self.config.defaultdir` → debería usar `self.config_service` o un accessor
- Línea 622-623: `self.config.config["ai"]` → debería usar `self.config_service.get_settings()`  
  (este es el primer `_ai_config` duplicado, se elimina con Bug 1)
- Línea 885: `self.myai = ai(self.config, **arguments)` → directo a config, debería pasar por servicio

### 🐛 Bug 3: `self.config = config` duplicado (líneas 59-60)

La línea de asignación está repetida dos veces.

### ⚠️ Bug 4: `print()` raw en lugar de `printer`

8 usos de `print()` nativo en connapp.py:
- Líneas 434, 547, 646, 834: YAML dumps de `--show`, `list`, `plugin --list` → **reemplazar** con el nuevo `printer.data()` con syntax highlighting
- Líneas 604, 619: shell completion/fzf wrapper output → **correcto**, es output que va a `.bashrc`, debe seguir con `print()` crudo
- Líneas 904, 939: `print("\r")` spacer en AI → reemplazar con `console.print()`

### ⚠️ Bug 5: `ImportError` en AIService.delete_session

Línea 34 de `ai_service.py` referencia `InvalidConfigurationError` pero no lo importa.

---

## Fase 0 — Sistema de Diseño Visual (Rich Output)

Antes de migrar código, debemos definir el **lenguaje visual unificado** de toda la CLI. Todos los handlers van a usar las mismas funciones de output. El objetivo es que cada tipo de output tenga una apariencia consistente, profesional y con colores.

### 0.1 Paleta de Colores

| Uso | Color Rich | Ejemplo |
|---|---|---|
| Éxito / OK | `green` | `[✓] Node added successfully` |
| Error | `red` | `[✗] Node not found` |
| Warning / Info menor | `yellow` | `[!] Plugin already enabled` |
| Info / neutral | `cyan` | `[i] Editing: ['router1']` |
| Títulos / headers | `bold cyan` | Paneles, reglas |
| Data keys (YAML) | `blue` | Syntax highlighting de YAML |
| Data values (YAML) | `white`/default | Syntax highlighting de YAML |
| AI Engineer | `blue` | Panel border blue |
| AI Architect | `medium_purple` | Panel border purple |
| Test PASS | `bold green` | `✓ PASS` |
| Test FAIL | `bold red` | `✗ FAIL` |
| Dim/metadata | `dim` | Token counts, timestamps |

### 0.2 Funciones del printer — Ampliación

El módulo `printer.py` actual tiene: `info`, `success`, `error`, `warning`, `custom`, `table`, `start`, `debug`. Hay que agregar funciones nuevas para cubrir todos los tipos de output:

#### Nuevas funciones a agregar en `printer.py`

| Función | Propósito | Diseño |
|---|---|---|
| `printer.data(title, content, language="yaml")` | Mostrar datos estructurados (nodo, perfil, lista) con syntax highlighting | Panel con título `bold cyan`, body con `Syntax(content, language)` |
| `printer.node_panel(unique, output, status)` | Panel de resultado de ejecución en un nodo | Panel con borde `green`/`red` según status, título con `✓`/`✗`, body con output |
| `printer.test_panel(unique, results_dict)` | Panel de resultado de test en un nodo | Igual que node_panel pero con resultados pass/fail por check |
| `printer.test_summary(results)` | Resumen consolidado de tests | Múltiples test_panel |
| `printer.header(text)` | Separador/título de sección | `Rule(text, style="bold cyan")` |
| `printer.kv(key, value)` | Key-value inline | `[bold]{key}[/bold]: {value}` |
| `printer.confirm_action(item, action)` | Mensaje pre-confirmación | `[i] {action}: {item}` estilizado |

#### Ejemplo concreto: `printer.data()`

```python
def data(title, content, language="yaml"):
    """Display structured data with syntax highlighting inside a panel."""
    from rich.syntax import Syntax
    from rich.panel import Panel
    syntax = Syntax(content, language, theme="monokai", word_wrap=True)
    panel = Panel(syntax, title=f"[bold cyan]{title}[/bold cyan]", 
                  border_style="dim", expand=False)
    console.print(panel)
```

**Antes** (actual):
```
[router1] 
host: 10.0.0.1
protocol: ssh
port: '22'
user: admin
```

**Después** (nuevo):
```
╭─ router1 ────────────────────╮
│ host: 10.0.0.1               │
│ protocol: ssh                │
│ port: '22'                   │
│ user: admin                  │ 
╰──────────────────────────────╯
```
Con syntax highlighting YAML (keys en azul, values en blanco).

#### Ejemplo concreto: `printer.node_panel()`

```python
def node_panel(unique, output, status):
    """Display node execution result in a styled panel."""
    from rich.panel import Panel
    from rich.text import Text
    
    if status == 0:
        status_str = "[bold green]✓ PASS[/bold green]"
        border = "green"
    else:
        status_str = f"[bold red]✗ FAIL({status})[/bold red]"
        border = "red"
    
    title = f"[bold]{unique}[/bold] — {status_str}"
    body = Text(output.strip() + "\n") if output and output.strip() else Text()
    console.print(Panel(body, title=title, border_style=border))
```

#### Ejemplo concreto: lista de nodos/perfiles 

```python
# En list handler, en vez de yaml dump + print:
items = node_service.list_nodes()
yaml_str = yaml.dump(items, sort_keys=False, default_flow_style=False)
printer.data("nodes", yaml_str)

# Para plugins:
plugins = plugin_service.list_plugins()
yaml_str = yaml.dump(plugins, sort_keys=False, default_flow_style=False)
printer.data("plugins", yaml_str)
```

### 0.3 Mapa de Outputs Actuales → Nuevos

| Comando | Output actual | Output nuevo |
|---|---|---|
| `node --show router1` | `printer.custom(name, "")` + `print(yaml)` | `printer.data(name, yaml_str)` |
| `profile --show myprofile` | `printer.custom(name, "")` + `print(yaml)` | `printer.data(name, yaml_str)` |
| `list nodes` | `printer.custom("nodes", "")` + `print(yaml)` | `printer.data("nodes", yaml_str)` |
| `list folders` | `printer.custom("folders", "")` + `print(yaml)` | `printer.data("folders", yaml_str)` |
| `list profiles` | `printer.custom("profiles", "")` + `print(yaml)` | `printer.data("profiles", yaml_str)` |
| `plugin --list` | `printer.custom("plugins", "")` + `print(yaml)` | `printer.data("plugins", yaml_str)` |
| `run node1 "cmd"` | `_print_node_panel()` inline | `printer.node_panel(unique, output, status)` |
| YAML run test | `_print_test_summary()` inline | `printer.test_panel()` + `printer.test_summary()` |
| `config --*` | `printer.success("Config saved")` | Sin cambio (ya es correcto) |
| `ai "query"` | Rich Panel + `print("\r")` | Rich Panel + `console.print()` |
| `ai --list` | `printer.table(...)` | Sin cambio (ya es correcto) |
| `node -a`, `-e`, `-r` | `printer.success/error` | Sin cambio (ya es correcto) |

### 0.4 Implementación

1. **Editar `connpy/printer.py`** para agregar las nuevas funciones (`data`, `node_panel`, `test_panel`, `test_summary`, `header`, `kv`)
2. Los handlers usarán estas funciones en vez de crear Panels inline
3. `_print_node_panel`, `_print_node_test_panel`, `_print_test_summary` de connapp.py se eliminan y se reemplazan por las funciones del printer

> [!IMPORTANT]
> La regla es: **toda presentación visual pasa por `printer`**. Los handlers nunca deben importar Rich directamente ni construir Panels. Solo llaman a `printer.data()`, `printer.node_panel()`, etc. Esto garantiza consistencia visual en toda la app.

### 0.5 Argparse con Rich (`rich-argparse`)

El output de `--help` de argparse es texto plano sin colores. Usando la librería `rich-argparse` se obtiene un help coloreado como drop-in replacement:

```python
from rich_argparse import RichHelpFormatter

parser = argparse.ArgumentParser(
    prog="connpy",
    description="SSH and Telnet connection manager",
    formatter_class=RichHelpFormatter  # ← solo cambiar esto
)
```

Esto afecta **solo al `--help`**. Los errores de argparse ya los interceptamos con `parser.error = self._custom_error` que usa `printer.error()`.

| Output argparse | Estado actual | Con rich-argparse |
|---|---|---|
| `--help` | Texto monótono plano | Argumentos en cyan, usage en bold, secciones claras |
| Errores de parsing | ✅ Ya usa `printer.error()` | Sin cambio |
| `--version` | ✅ Ya usa `printer.info()` | Sin cambio |

---

## Fase 1 — Limpieza Pre-Migración

Antes de tocar la estructura de archivos, hay que limpiar el código existente.

### 1.1 Implementar las nuevas funciones de `printer.py`
- Agregar `data()`, `node_panel()`, `test_panel()`, `test_summary()`, `header()`, `kv()` según lo definido en Fase 0
- Agregar test en `test_printer.py` para las funciones nuevas

### 1.2 Agregar `rich-argparse`
- Agregar `rich-argparse` a `requirements.txt`
- En `connapp.py`, cambiar `formatter_class=argparse.RawTextHelpFormatter` por `formatter_class=RichHelpFormatter` en todos los parsers (~12 instancias)
- Los subparsers que no tienen formatter explícito heredan del padre, así que con poner en `defaultparser` y en los que usan `RawTextHelpFormatter` alcanza

### 1.3 Eliminar métodos duplicados
- **Borrar** las definiciones antiguas de `_case`, `_fzf`, `_idletime`, `_configfolder`, `_ai_config` (líneas 603-627)
- Dejar solamente las definiciones de líneas 735-768 que tienen feedback con `printer.success`
- Mover `_fzf_wrapper` y `_completion` (que son únicos) a la zona cercana a las versiones finales

### 1.4 Corregir asignación duplicada
- Remover la línea 60 (`self.config = config` duplicada)

### 1.5 Corregir `self.config.defaultdir` 
- Agregar método `get_default_dir()` a `ConfigService` que retorne `self.config.defaultdir`
- Reemplazar en connapp.py línea 250

### 1.6 Corregir import faltante en AIService
- Agregar `from .exceptions import InvalidConfigurationError` en `ai_service.py`

### 1.7 Reemplazar `print()` raw por printer
- Reemplazar los 4 YAML dumps (`print(yaml_output)`) por `printer.data(title, yaml_str)`
- Reemplazar `print("\r")` por `console.print()` en las líneas 904 y 939
- Mantener `print()` crudo solo para shell completion/fzf wrapper output (necesita output limpio sin estilos)

---

## Fase 2 — Descomposición del Monolito

El objetivo es reducir `connapp.py` de **1803 líneas** a un orquestador limpio de ~200-300 líneas que solo:
1. Define los parsers de argparse
2. Despacha a handlers del paquete `connpy/cli/`

### Estructura propuesta del paquete `connpy/cli/`

```
connpy/cli/
├── __init__.py          # Exporta todos los handlers
├── node_handler.py      # _connect, _add, _del, _mod, _show
├── profile_handler.py   # _profile_add, _profile_del, _profile_mod, _profile_show
├── config_handler.py    # _case, _fzf, _idletime, _configfolder, _ai_config, _completion, _fzf_wrapper
├── run_handler.py       # _node_run, _yaml_run, _yaml_generate, _cli_run
├── ai_handler.py        # _func_ai (modo single + interactive)
├── api_handler.py       # _func_api
├── plugin_handler.py    # _func_plugin
├── import_export_handler.py  # _func_import, _func_export, _bulk
├── helpers.py           # _choose (selector fzf/inquirer)
├── validators.py        # Todas las *_validation functions (host, port, protocol, tags, jumphost, etc.)
├── forms.py             # _questions_nodes, _questions_edit, _questions_profiles, _questions_bulk
└── help_text.py         # _help, _print_instructions (completion scripts, YAML template, etc.)
```

### 2.1 Crear `connpy/cli/__init__.py`

- Exportar todas las clases handler
- Definir una clase base `CLIHandler` con:
  - `self.app` → referencia al connapp (para acceder a servicios)
  - `self.services` → acceso directo a los servicios
  - Acceso rápido a `printer` (todos los outputs pasan por ahí)

### 2.2 Crear `connpy/cli/helpers.py`

Extraer el único método utilitario de UI compartido:

| Método | Descripción |
|---|---|
| `choose(list, name, action, fzf, case)` | Selector inquirer/fzf |

> [!NOTE]
> Los métodos `_print_node_panel`, `_print_node_test_panel`, `_print_test_summary` ya no van aquí. Ahora viven en `printer.py` como `printer.node_panel()`, `printer.test_panel()`, `printer.test_summary()`.

### 2.3 Crear `connpy/cli/validators.py`

Extraer **todas** las funciones de validación de inquirer (~14 funciones):

| Función | Uso |
|---|---|
| `host_validation` | Validar hostname |
| `protocol_validation` | Validar protocolo de nodo |
| `profile_protocol_validation` | Validar protocolo de perfil |
| `port_validation` | Validar puerto de nodo |
| `profile_port_validation` | Validar puerto de perfil |
| `pass_validation` | Validar password @profile |
| `tags_validation` | Validar tags dict |
| `profile_tags_validation` | Validar tags de perfil |
| `jumphost_validation` | Validar jumphost |
| `profile_jumphost_validation` | Validar jumphost de perfil |
| `default_validation` | Validación default @profile |
| `bulk_node_validation` | Validar nodo en bulk |
| `bulk_folder_validation` | Validar folder en bulk |
| `bulk_host_validation` | Validar host en bulk |

Estas funciones necesitan acceso a `self.profiles`, `self.nodes_list`, `self.folders` y `self.case`. Se les pasará un contexto o se guardará como atributo de la clase.

### 2.4 Crear `connpy/cli/forms.py`

Extraer los formularios interactivos de inquirer:

| Función | Líneas approx | Descripción |
|---|---|---|
| `questions_nodes()` | 1378-1450 | Formulario completo de nodo |
| `questions_edit()` | 1363-1376 | Checkboxes de qué editar |
| `questions_profiles()` | 1452-1512 | Formulario completo de perfil |
| `questions_bulk()` | 1514-1545 | Formulario de bulk add |

Estas funciones usan validators y servicios (para obtener defaults de nodos/perfiles).

### 2.5 Crear `connpy/cli/help_text.py`

Extraer todo el texto estático:

| Función | Descripción |
|---|---|
| `get_help(type, parsers)` | Genera help text (usage, end, node) |
| `get_instructions(type)` | Wizard instructions, completion scripts, fzf wrapper, YAML template |

Este módulo es **puro texto**, sin dependencias de servicios.

### 2.6 Crear `connpy/cli/node_handler.py`

```python
class NodeHandler:
    def __init__(self, app):
        self.app = app
    
    def connect(self, args)     # Actual _connect
    def add(self, args)         # Actual _add  
    def delete(self, args)      # Actual _del
    def modify(self, args)      # Actual _mod
    def show(self, args)        # Usa printer.data() para YAML
    def dispatch(self, args)    # Actual _func_node
```

### 2.7 Crear `connpy/cli/profile_handler.py`

```python
class ProfileHandler:
    def __init__(self, app):
        self.app = app
    
    def add(self, args)         # Actual _profile_add
    def delete(self, args)      # Actual _profile_del
    def modify(self, args)      # Actual _profile_mod
    def show(self, args)        # Usa printer.data() para YAML
    def dispatch(self, args)    # Actual _func_profile
```

### 2.8 Crear `connpy/cli/config_handler.py`

```python
class ConfigHandler:
    def __init__(self, app):
        self.app = app
    
    def set_case(self, args)
    def set_fzf(self, args)
    def set_idletime(self, args)
    def set_config_folder(self, args)
    def set_ai_config(self, args)
    def show_completion(self, args)
    def show_fzf_wrapper(self, args)
    def dispatch(self, args)
```

### 2.9 Crear `connpy/cli/run_handler.py`

```python
class RunHandler:
    def __init__(self, app):
        self.app = app
    
    def node_run(self, args)        # Usa printer.header() + printer.node_panel()
    def yaml_run(self, args)        # Playbook YAML
    def yaml_generate(self, args)   # Generar template
    def cli_run(self, script)       # Usa printer.header() + printer.node_panel/test_panel
    def dispatch(self, args)
```

### 2.10 Crear `connpy/cli/ai_handler.py`

```python
class AIHandler:
    def __init__(self, app):
        self.app = app
    
    def single_question(self, args, myai, session_id)  # Modo single shot
    def interactive_chat(self, args, myai, session_id)  # Modo interactivo
    def list_sessions(self, args)                       # Usa printer.table()
    def delete_session(self, args)
    def dispatch(self, args)
```

### 2.11 Crear `connpy/cli/api_handler.py`

```python
class APIHandler:
    def __init__(self, app):
        self.app = app
    
    def dispatch(self, args)   # Start/stop/restart/debug
```

### 2.12 Crear `connpy/cli/plugin_handler.py`

```python
class PluginHandler:
    def __init__(self, app):
        self.app = app
    
    def dispatch(self, args)   # add/update/del/enable/disable/list
                               # list usa printer.data() para YAML
```

### 2.13 Crear `connpy/cli/import_export_handler.py`

```python
class ImportExportHandler:
    def __init__(self, app):
        self.app = app
    
    def import_file(self, args)
    def export_file(self, args)
    def bulk(self, args)
    def dispatch_import(self, args)
    def dispatch_export(self, args)
```

---

## Fase 2.5 — Auditoría y Correcciones Post-Refactor ✅

Revisión exhaustiva línea por línea de los 12 archivos del paquete `connpy/cli/` comparados contra el `connapp.py` original. Todos los bugs críticos (B1-B7) y mejoras (M1-M5) han sido corregidos.

<details>
<summary>Detalle de bugs corregidos (click para expandir)</summary>

### 🔴 Bugs Críticos (Corregidos)

| Bug | Archivo | Problema | Fix |
|-----|---------|----------|-----|
| B1 | `run_handler.py` | `node_run` pasaba comandos como lista separada en vez de string unido | `commands = [" ".join(args.data[1:])]` |
| B2 | `run_handler.py` | `cli_run` no pasaba `folder` ni `prompt` al execution service | Agregados como parámetros opcionales a `ExecutionService` |
| B3 | `ai_handler.py` | Sesiones usan `ai_service` pero AI usa `ai()` directo | Validado que ambos leen del mismo storage |
| B4 | `ai_handler.py` | Faltaba error msg cuando sesión no carga | Agregado branch else con `printer.error()` |
| B5 | `ai_handler.py` | Faltaba mensaje de historial previo al resumir | Agregado `printer.info()` con count de mensajes |
| B6 | `ai_handler.py` | `KeyboardInterrupt` mataba el chat entero | Doble try/except: interno (continue) + externo (exit) |
| B7 | `api_handler.py` | Lógica `if`/`elif` rota + bypass de `system_service` | Corregido a `elif` y llamadas directas a `connpy.api` |

### 🟢 Mejoras de Calidad (Corregidas)

| ID | Archivo | Acción |
|----|---------|--------|
| M1 | `cli/__init__.py` | Clase `CLIHandler` muerta eliminada |
| M2 | `cli/config_handler.py` | Handlers huérfanos eliminados |
| M3 | `cli/commands/` | Directorio vacío eliminado |
| M4 | `cli/ai_handler.py` | Import no usado eliminado |
| M5 | `cli/profile_handler.py` | Import no usado eliminado |

</details>

---

## Fase 2.7 — Sistema de Temas Persistente ✅

Se implementó un sistema de temas centralizado y persistente que permite personalizar todos los colores de la CLI.

### Componentes implementados

| Archivo | Cambio |
|---------|--------|
| `printer.py` | `STYLES`, `DARK_THEME`, `LIGHT_THEME` + función `apply_theme()` con merge y fallback |
| `services/config_service.py` | `apply_theme_from_file()` — acepta `dark`, `light`, o path a YAML |
| `cli/config_handler.py` | Handler `set_theme` con dispatch y aplicación inmediata |
| `connapp.py` | Flag `--theme THEME` + `_apply_app_theme()` que sincroniza printer y `RichHelpFormatter` |

### Características
- `connpy config --theme dark` / `light` / `custom.yaml`
- Persistido en `config.yaml` bajo `config.theme`
- Auto-cargado al inicio vía `connapp._apply_app_theme()`
- Fallback: keys faltantes en el YAML usan los defaults de `STYLES`
- Afecta: paneles, tablas, AI (Engineer/Architect), y menús `--help`

### Eliminación de colores hardcodeados ✅

Auditoría completa de `ai.py`, `ai_handler.py`, `run_handler.py`, `connapp.py`: **cero colores literales** fuera de `printer.py`. Todos usan aliases semánticos (`engineer`, `architect`, `error`, `warning`, `unavailable`, etc.).

---

## Fase 3 — Dynamic Service Backend (ServiceProvider Pattern)

### Objetivo

Hacer que el CLI sea **agnóstico del backend**. En vez de que los handlers accedan a servicios locales hardcodeados (`self.app.node_service`), pasan por un **ServiceProvider** que decide qué implementación usar. Por defecto → servicios locales. Con `--remote` → stubs gRPC (a implementar después). **Zero refactoring del CLI cuando se agregue gRPC.**

### Arquitectura

```
┌─────────────────────────────────────────────────────┐
│                   CLI Handlers                       │
│  (NodeHandler, ProfileHandler, RunHandler, etc.)     │
│                                                      │
│  self.app.services.nodes.list_nodes()                │
│  self.app.services.config_svc.update_setting()       │
└──────────────────────┬──────────────────────────────┘
                       │
              ┌────────▼────────┐
              │ ServiceProvider  │  ← decides backend
              │                  │
              │ mode = "local"   │  (default)
              │ mode = "remote"  │  (--remote / config)
              └───────┬──┬──────┘
                      │  │
          ┌───────────┘  └───────────┐
          ▼                          ▼
  ┌───────────────┐        ┌─────────────────┐
  │ Local Services │        │  gRPC Stubs      │
  │ (current code) │        │  (Fase 4+, TBD)  │
  │                │        │                  │
  │ NodeService    │        │ NodeServiceStub  │
  │ ProfileService │        │ ProfileStub      │
  │ ConfigService  │        │ ConfigStub       │
  │ ...            │        │ ...              │
  └───────────────┘        └─────────────────┘
```

### 3.1 Crear `connpy/services/provider.py` [NEW]

Fachada ligera que expone atributos de servicio. El provider recibe un `mode` y un `config` e instancia el backend correcto.

```python
class ServiceProvider:
    """Dynamic service backend. Transparently provides local or remote services."""
    
    def __init__(self, config, mode="local", remote_host=None):
        self.mode = mode
        self.config = config
        self.remote_host = remote_host
        
        if mode == "local":
            self._init_local()
        elif mode == "remote":
            self._init_remote()
        else:
            raise ValueError(f"Unknown service mode: {mode}")
    
    def _init_local(self):
        from .node_service import NodeService
        from .profile_service import ProfileService
        from .config_service import ConfigService
        from .plugin_service import PluginService
        from .ai_service import AIService
        from .system_service import SystemService
        from .execution_service import ExecutionService
        from .import_export_service import ImportExportService
        
        self.nodes = NodeService(self.config)
        self.profiles = ProfileService(self.config)
        self.config_svc = ConfigService(self.config)
        self.plugins = PluginService(self.config)
        self.ai = AIService(self.config)
        self.system = SystemService(self.config)
        self.execution = ExecutionService(self.config)
        self.import_export = ImportExportService(self.config)
    
    def _init_remote(self):
        # Fase 4+: gRPC stubs go here
        raise NotImplementedError(
            "Remote mode (gRPC) is not yet available. "
            "Use local mode or wait for the gRPC implementation."
        )
```

> [!NOTE]
> Los nombres de atributos son cortos y limpios: `services.nodes`, `services.profiles`, `services.config_svc` (evita colisión con `self.config`), `services.execution`, etc.

### 3.2 Refactorizar `connapp.__init__` [MODIFY]

Reemplazar las 8 instanciaciones individuales por un único `ServiceProvider`:

```python
# ANTES (actual):
self.node_service = NodeService(self.config)
self.profile_service = ProfileService(self.config)
self.config_service = ConfigService(self.config)
self.plugin_service = PluginService(self.config)
self.ai_service = AIService(self.config)
self.system_service = SystemService(self.config)
self.execution_service = ExecutionService(self.config)
self.import_export_service = ImportExportService(self.config)

# DESPUÉS:
from .services.provider import ServiceProvider
mode = self.config.config.get("service_mode", "local")
remote_host = self.config.config.get("remote_host", None)
self.services = ServiceProvider(self.config, mode=mode, remote_host=remote_host)
```

### 3.3 Agregar flags `--service-mode` y `--remote` globales [MODIFY connapp.py]

Agregar a `defaultparser`:
```python
defaultparser.add_argument("--service-mode", dest="service_mode", choices=["local", "remote"],
                           help="Set the backend service mode (local or remote)")
defaultparser.add_argument("--remote", dest="remote_host", metavar="HOST:PORT",
                           help="Connect to a remote connpy service via gRPC (requires --service-mode remote)")
```

Y en `start()`, después del parsing:
```python
mode = args.service_mode or self.config.config.get("service_mode", "local")
remote_host = args.remote_host or self.config.config.get("remote_host", None)

self.services = ServiceProvider(self.config, mode=mode, remote_host=remote_host)
```

### 3.4 Migrar handlers al nuevo API [MODIFY cli/*.py]

Renombrar todas las referencias en los handlers:

| Antes | Después |
|-------|---------|
| `self.app.node_service` | `self.app.services.nodes` |
| `self.app.profile_service` | `self.app.services.profiles` |
| `self.app.config_service` | `self.app.services.config_svc` |
| `self.app.plugin_service` | `self.app.services.plugins` |
| `self.app.ai_service` | `self.app.services.ai` |
| `self.app.system_service` | `self.app.services.system` |
| `self.app.execution_service` | `self.app.services.execution` |
| `self.app.import_export_service` | `self.app.services.import_export` |

> [!IMPORTANT]
> **Estrategia de migración**: Hacer un full rename en todos los handlers en un solo pase (clean break). Son find-replace directos y todos los handlers están en `connpy/cli/`.

### 3.5 Actualizar tests [MODIFY tests/]

- Actualizar mocks para usar `app.services.nodes` en vez de `app.node_service`
- Agregar test para `ServiceProvider` con modo `local` y verificar que `remote` lanza `NotImplementedError`

---

## Fase 4 — Servidor gRPC y Stubs Remotos

Con el `ServiceProvider` en su lugar (Fase 3), la aplicación ahora es agnóstica de si sus servicios se ejecutan localmente o de forma remota. Esta fase consiste en implementar la comunicación gRPC real.

### Arquitectura de gRPC
- **Protocol Buffers**: Un único archivo `.proto` (`connpy.proto`) que define todos los mensajes y servicios.
- **Servidor (`connpy api -s`)**: Un servidor gRPC que instancia los servicios locales (como lo hacía `ServiceProvider` en modo local) y procesa las peticiones de los clientes.
- **Cliente (`connpy --remote <host>`)**: Stubs (proxies) que exponen la misma interfaz de los servicios locales y serializan las llamadas a través de la red hacia el servidor.

### 4.1 Definir proto files (`connpy/proto/connpy.proto`)
- Crear los mensajes base: `Node`, `Folder`, `Profile`, `Theme`, `Plugin`, etc.
- Definir servicios que mapeen la interfaz existente de los servicios Python:
  - `NodeService` (list_nodes, list_folders, move_node, bulk_add, etc.)
  - `ProfileService` (list_profiles, add_profile, get_profile, etc.)
  - `ConfigService` (get_settings, update_setting, set_config_folder, etc.)
  - `PluginService` (list_plugins, add_plugin, enable_plugin, etc.)
  - `ExecutionService` (run_commands)

### 4.2 Generar código Python
- Agregar `grpcio` y `grpcio-tools` a `requirements.txt`.
- Ejecutar el compilador `protoc` para generar `connpy_pb2.py` y `connpy_pb2_grpc.py`.

### 4.3 Implementar Servidor gRPC (`connpy/grpc/server.py`)
- Crear las clases de servidor (ej. `NodeServicer`, `ProfileServicer`) que hereden de las generadas por `protoc`.
- Cada Servicer debe recibir una instancia de `Configfile` en su constructor, inicializar el servicio local correspondiente (ej. `NodeService(config)`) y redirigir las llamadas RPC a este servicio local.
- **Reemplazo total de la API**: Eliminar completamente el servidor Flask/Waitress actual en `connpy/api.py`. No se mantiene nada de la API REST anterior. Modificar `connpy/cli/api_handler.py` y `connpy/api.py` para que `connpy api -s` arranque exclusivamente este nuevo servidor gRPC en el puerto especificado.

### 4.4 Implementar Stubs en el Cliente (`connpy/grpc/stubs.py`)
- Crear las clases proxy (`NodeStub`, `ProfileStub`, etc.) que cumplan con la misma firma que los servicios locales.
- Cada Stub debe recibir un `grpc.Channel`, construir el Stub correspondiente generado por `protoc` (ej. `connpy_pb2_grpc.NodeServiceStub`) y llamar a los métodos gRPC serializando/deserializando los argumentos y respuestas.

### 4.5 Conectar Stubs a `ServiceProvider`
- En `connpy/services/provider.py`, modificar `_init_remote(self)` para que en lugar de asignar `RemoteStub()`, construya un `grpc.insecure_channel(self.remote_host)`.
- Instanciar los stubs reales y asignarlos a las propiedades de la clase (`self.nodes = NodeStub(channel)`, `self.profiles = ProfileStub(channel)`, etc.).
- Mantener la inicialización de `ConfigService` en modo mixto (lee de local para saber la configuración de red y temas visuales, pero podría enviar cambios al servidor si es necesario, o mantener las configuraciones de interfaz estrictamente locales).

### 4.6 Manejo de Errores
- Envolver las excepciones `grpc.RpcError` en `ConnpyError` del cliente para que los handlers del CLI las impriman limpiamente y no lancen un stacktrace sucio.

---

## Fase 5 — Verificación Final

### 5.1 Correr suite completa
```bash
pytest connpy/tests/
```

### 5.2 Tests de integración manual
- `connpy node -s router1` → Verifica `printer.data()` con syntax highlighting
- `connpy list nodes` → Verifica `printer.data()` con panel
- `connpy plugin --list` → Verifica `printer.data()` con panel
- `connpy config --allow-uppercase true` → Verifica config handler
- `connpy run router1 "show version"` → Verifica `printer.node_panel()`
- `connpy ai "hello"` → Verifica AI handler
- `connpy --remote localhost:50051 list nodes` → Verifica que lanza `NotImplementedError` (hasta Fase 4)

### 5.3 Verificar que `wc -l connpy/connapp.py` < 400

---

## Resumen de Ejecución

| Fase | Estado | Descripción | Archivos involucrados |
|---|---|---|---|
| **0** | ✅ | Sistema de diseño visual Rich | `connpy/printer.py` |
| **1** | ✅ | Limpieza pre-migración + adoptar printer nuevo | `connapp.py`, `ai_service.py`, `config_service.py`, `printer.py` |
| **2** | ✅ | Crear paquete `cli/` con 12 módulos | `connpy/cli/*.py` |
| **2.5** | ✅ | Auditoría post-refactor: fix bugs B1-B7 + limpieza M1-M5 | `cli/*.py`, `services/execution_service.py` |
| **2.7** | ✅ | Sistema de temas persistente + eliminación de colores hardcodeados | `printer.py`, `ai.py`, `ai_handler.py`, `config_service.py`, `config_handler.py`, `connapp.py` |
| **3** | ✅ | Dynamic Service Backend (ServiceProvider) | `services/provider.py` (nuevo), `connapp.py`, `cli/*.py`, `tests/` |
| **4** | ✅ | Servidor gRPC y Stubs Remotos | `proto/`, `grpc/`, `services/provider.py`, `api_handler.py` |
| **4.5** | 📋 | Auditoría Post-gRPC (Context/Sync/Completion) | `cli/helpers.py`, `connapp.py`, `tests/` |
| **5** | 📋 | Verificación final | Todos |

> [!IMPORTANT]
> La Fase 3 completada estableció el `ServiceProvider`. Agregar gRPC en Fase 4 es implementar el servidor y los stubs en el cliente, logrando comunicación real sin tocar la lógica de los handlers del CLI.

> [!WARNING]
> El método `_func_ai` / `AIHandler` instancia `ai(self.config)` directamente, bypasseando `AIService`. Esto es intencional: el AI necesita estado largo de sesión (`self.myai`) y un refactor completo del AIService sería trabajo aparte. El ServiceProvider no afecta este flujo.


