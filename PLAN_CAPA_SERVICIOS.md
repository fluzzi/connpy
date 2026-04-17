# Plan de Arquitectura: Creación de Capa de Servicios en Connpy

Este documento detalla el plan paso a paso para refactorizar `connpy` y extraer la lógica de negocio actual (acoplada en `connapp.py` y `api.py`) hacia una **Capa de Servicios (Service Layer)** limpia y reutilizable.

## 🎯 Objetivos
1. **Desacoplar la CLI (`connapp.py`)**: La CLI solo debe encargarse de procesar argumentos (`argparse`), solicitar datos al usuario (`inquirer`, `rich.prompt`) y renderizar la salida en pantalla (`rich`).
2. **Desacoplar la API (`api.py`)**: La API actual (Flask) y la futura API gRPC solo deben encargarse de exponer endpoints y delegar la ejecución a la capa subyacente.
3. **Centralizar la Lógica de Negocio**: Todas las operaciones sobre nodos, perfiles, configuración, ejecución de comandos, IA, plugins e importación/exportación vivirán en la nueva capa de servicios. Esto asegura que ejecutar una acción desde la CLI local, CLI remota, o API produzca **exactamente el mismo comportamiento**.

---

## 🏗️ 1. Estructura de la Capa de Servicios

Crearemos un nuevo paquete `connpy/services/` que agrupe las distintas responsabilidades del dominio. Basado en todos los comandos de `connapp.py`, la estructura será:

```text
connpy/
└── services/
    ├── __init__.py
    ├── node_service.py         # CRUD de nodos, carpetas, bulk, mover, copiar y listar
    ├── profile_service.py      # CRUD de perfiles
    ├── execution_service.py    # Ejecución de comandos en paralelo (ad-hoc, scripts, yaml, test)
    ├── import_export_service.py# Importación y exportación de configuración a YAML
    ├── ai_service.py           # Interacciones con el Agente (Claude/LLMs) y su configuración
    ├── plugin_service.py       # Habilitar, deshabilitar y listar plugins
    ├── config_service.py       # Manejo de la configuración global de la app (case, fzf, idletime)
    ├── system_service.py       # Control de ciclo de vida (iniciar/detener API local)
    └── exceptions.py           # Excepciones de negocio (ej. NodeNotFoundError)
```

---

## 🛠️ 2. Diseño de los Servicios (Casos de Uso Completos)

A continuación, la lista detallada de servicios mapeando cada funcionalidad de la aplicación actual:

### 1. `NodeService`
Maneja toda la interacción con `configfile` relacionada con la topología de red (nodos y carpetas).
- `list_nodes(filter: str/list) -> list`: Devuelve lista de nodos (comando `list`).
- `list_folders(filter: str/list) -> list`: Devuelve lista de carpetas.
- `get_node_details(unique: str) -> dict`: Devuelve configuración de un nodo (`node show`).
- `add_node(unique: str, data: dict) -> None`: Agrega un nuevo nodo (`node -a`).
- `update_node(unique: str, data: dict) -> None`: Modifica un nodo (`node -e`).
- `delete_node(unique: str) -> None`: Elimina un nodo (`node -r`).
- `move_node(src: str, dst: str) -> None`: Renombra o mueve nodos a otras carpetas (`move`).
- `copy_node(src: str, dst: str) -> None`: Duplica un nodo existente (`copy`).
- `bulk_add_nodes(folder: str, nodes_data: list) -> dict`: Lógica para procesar la creación masiva de nodos (`bulk`).

### 2. `ProfileService`
- `list_profiles() -> list`: Muestra los perfiles disponibles (`list`).
- `get_profile(name: str) -> dict`: Muestra un perfil (`profile show`).
- `add_profile(name: str, data: dict) -> None`: Agrega un perfil (`profile -a`).
- `update_profile(name: str, data: dict) -> None`: Modifica un perfil (`profile mod`).
- `delete_profile(name: str) -> None`: Elimina un perfil (`profile -r`).

### 3. `ExecutionService`
Encapsula la clase `core.nodes` para conexiones y envíos de comandos, abstrayéndola de `sys.stdout` o funciones `print`.
- `run_commands(nodes_list: list, commands: list) -> dict`: Llama a nodos en paralelo y devuelve un diccionario con los resultados (`run`).
- `test_commands(nodes_list: list, commands: list, expected: str) -> dict`: Valida el output esperado.
- `run_cli_script(nodes_list: list, script_path: str) -> dict`: Lee y ejecuta un script plano en los nodos.
- `run_yaml_playbook(playbook_path: str) -> dict`: Ejecuta la lógica compleja definida en un archivo YAML.

### 4. `ImportExportService`
- `export_to_yaml(folder_name: str, output_path: str) -> None`: Exporta la configuración completa de una carpeta de forma segura (`export`).
- `import_from_yaml(yaml_path: str, destination_folder: str) -> dict`: Parsea e importa nodos desde un archivo YAML asegurando que no haya colisiones críticas (`import`).

### 5. `PluginService`
- `list_plugins() -> list`: Devuelve el estado de todos los plugins detectados (activos/inactivos) (`plugin`).
- `enable_plugin(name: str) -> None`: Activa un plugin en la configuración.
- `disable_plugin(name: str) -> None`: Desactiva un plugin en la configuración.

### 6. `ConfigService`
- `update_setting(key: str, value: any) -> None`: Actualiza de forma genérica o específica (fzf, case, idletime, configfolder) en el `configfile` (`config`).
- `get_settings() -> dict`: Devuelve las configuraciones globales actuales.

### 7. `AIService`
Encapsula `connpy.ai.ai`.
- `ask(input_text: str, dryrun: bool, chat_history: list) -> dict/str`: Envia consulta al Agente (`ai`).
- `confirm(input_text: str) -> bool`: Mecanismo de seguridad.
- `configure_provider(provider: str, model: str, api_key: str) -> None`: Guarda configuración de OpenAI/Anthropic/Google en config (`config openai/anthropic/google`).

### 8. `SystemService`
- `start_api(host: str, port: int) -> None`: Levanta el daemon o proceso de la API (`api start`).
- `stop_api() -> None`: Baja el proceso local (`api stop`).
- `status_api() -> dict`: Devuelve el estado del proceso local.

---

## 🔌 3. Sobre los Plugins (Core Plugins)
Los plugins de core (como `sync.py`) añaden sus propios `subparsers` directamente a la CLI (ej. `sync start`, `sync backup`, `sync restore`).
- **Arquitectura para Plugins**: Para mantener la capa de servicios limpia, los plugins deben instanciar su propio Service si requieren lógica compleja (ej. `GoogleSyncService` definido dentro de `core_plugins/sync.py`), o bien llamar a los servicios core que definimos arriba. El motor de plugins de la aplicación no se toca, pero el comportamiento dentro de los plugins debería alinearse a usar llamadas de la Capa de Servicios si tocan datos de nodos.

---

## 🚀 4. Fases de Implementación Actualizadas

### Fase 1: Creación del Esqueleto y Modelos de Datos
1. Crear el directorio `connpy/services/` y los archivos listados.
2. Definir `exceptions.py` con errores como `NodeNotFoundError`, `ProfileNotFoundError`, `DuplicateEntityError`.
3. Crear el `connpy/services/__init__.py` que expondrá estos servicios para que puedan ser fácilmente importados (`from connpy.services import NodeService, ExecutionService`).

### Fase 2: Migración de CRUD y Configuración
1. Refactorizar la CLI y la API para instanciar y usar: `NodeService`, `ProfileService`, `ConfigService` y `PluginService`.
2. Todo el código de validación de variables (`_questions_nodes`, `_type_node`) permanecerá en `connapp.py` ya que pertenece a la "Presentación/CLI", pero los diccionarios limpios se pasarán al Servicio para su guardado final.

### Fase 3: Migración de Import/Export e IA
1. Extraer la lógica de YAML a `ImportExportService`.
2. Mover la configuración de las llaves API a `AIService`.

### Fase 4: Migración de Ejecución (El cambio más complejo)
1. Desacoplar `core.nodes` para que sea capaz de retornar estado consolidado (diccionarios con la salida de los comandos por nodo) en vez de imprimir asíncronamente en pantalla con `printer`.
2. Integrar `ExecutionService` en los comandos `run`, `node (connect)`, test, etc.
3. La CLI se subscribirá a los resultados que devuelve el `ExecutionService` para formatearlos con `rich`.

### Fase 5: Preparación para Cliente Servidor (gRPC/REST remoto)
1. Con los servicios totalmente aislados, si la CLI opera en "modo remoto", inyectará un Cliente Remoto que implementa las mismas interfaces (mismos métodos del `NodeService`) pero que serializa peticiones hacia la API en lugar de acceder directamente al archivo de configuración cifrado local.

---

## ✅ Checklist para el éxito
- [ ] Ningún `print()`, `console.print()`, `Prompt.ask()` debe existir dentro del paquete `services/`.
- [ ] Todas las excepciones lanzadas por `services/` deben ser manejadas visualmente por la capa que los consuma (`connapp.py` las pinta, `api.py` devuelve 400/500 JSON).
- [ ] Asegurarse de que el comportamiento local (CLI sin red) no perciba pérdida de rendimiento.
