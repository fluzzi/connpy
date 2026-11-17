import ast
import json

from ..auth_utils import (
    MAX_FALLBACK_ENTRIES,
    MAX_JSON_INPUT_BYTES,
    MAX_JSON_INPUT_DEPTH,
    validate_auth_budget_tags,
    validate_fallback_entries,
    validate_json_depth_and_size,
)
from .validators import Validators


def _parse_tags_input(raw_tags):
    if raw_tags in ("", None):
        return ""
    if isinstance(raw_tags, dict):
        validate_json_depth_and_size(
            raw_tags,
            max_bytes=MAX_JSON_INPUT_BYTES,
            max_depth=MAX_JSON_INPUT_DEPTH,
        )
        validate_auth_budget_tags(raw_tags)
        return raw_tags
    s = str(raw_tags)
    if s.startswith("@"):
        return s
    if len(s.encode("utf-8", errors="replace")) > MAX_JSON_INPUT_BYTES:
        raise ValueError(f"Tags exceed maximum size of {MAX_JSON_INPUT_BYTES} bytes")
    try:
        parsed = json.loads(s)
    except (ValueError, TypeError):
        parsed = ast.literal_eval(s)
    validate_json_depth_and_size(
        parsed,
        max_bytes=MAX_JSON_INPUT_BYTES,
        max_depth=MAX_JSON_INPUT_DEPTH,
    )
    validate_auth_budget_tags(parsed)
    return parsed


def _parse_fallback_input(raw_fb):
    if raw_fb in (None, "", []):
        return ""
    if isinstance(raw_fb, str):
        stripped = raw_fb.strip()
        if not stripped:
            return ""
        if stripped.startswith(("[", "{")):
            parsed = validate_json_depth_and_size(
                stripped,
                max_bytes=MAX_JSON_INPUT_BYTES,
                max_depth=MAX_JSON_INPUT_DEPTH,
            )
            return validate_fallback_entries(parsed, max_entries=MAX_FALLBACK_ENTRIES)
        validated = validate_fallback_entries(stripped, max_entries=MAX_FALLBACK_ENTRIES)
        if (
            stripped.startswith("@")
            and not stripped.startswith("@@")
            and "," in stripped
            and not stripped.startswith("compose:")
        ):
            return validated
        return stripped
    validate_json_depth_and_size(
        raw_fb,
        max_bytes=MAX_JSON_INPUT_BYTES,
        max_depth=MAX_JSON_INPUT_DEPTH,
    )
    return validate_fallback_entries(raw_fb, max_entries=MAX_FALLBACK_ENTRIES)


class Forms:
    def __init__(self, app):
        self.app = app
        self.validators = Validators(app)

    def questions_edit(self):
        import inquirer
        questions = []
        questions.append(inquirer.Confirm("host", message="Edit Hostname/IP?"))
        questions.append(inquirer.Confirm("protocol", message="Edit Protocol/app?"))
        questions.append(inquirer.Confirm("port", message="Edit Port?"))
        questions.append(inquirer.Confirm("options", message="Edit Options?"))
        questions.append(inquirer.Confirm("logs", message="Edit logging path/file?"))
        questions.append(inquirer.Confirm("tags", message="Edit tags?"))
        questions.append(inquirer.Confirm("jumphost", message="Edit jumphost?"))
        questions.append(inquirer.Confirm("user", message="Edit User?"))
        questions.append(inquirer.Confirm("password", message="Edit password?"))
        questions.append(inquirer.Confirm("fallback", message="Edit fallback credentials?"))
        return inquirer.prompt(questions)

    def questions_nodes(self, unique, uniques=None, edit=None):
        import inquirer
        try:
            defaults = self.app.services.nodes.get_node_details(unique)
            if "tags" not in defaults:
                defaults["tags"] = ""
            if "jumphost" not in defaults:
                defaults["jumphost"] = ""
            if "fallback" not in defaults:
                defaults["fallback"] = ""
        except Exception:  # noqa: BLE001
            defaults = {"host": "", "protocol": "", "port": "", "user": "", "options": "", "logs": "", "tags": "", "password": "", "jumphost": "", "fallback": ""}
        node = {}
        if edit is None:
            edit = {"host": True, "protocol": True, "port": True, "user": True, "password": True, "options": True, "logs": True, "tags": True, "jumphost": True, "fallback": True}
        questions = []
        if edit["host"]:
            questions.append(inquirer.Text("host", message="Add Hostname or IP", validate=self.validators.host_validation, default=defaults["host"]))
        else:
            node["host"] = defaults["host"]
        if edit["protocol"]:
            questions.append(inquirer.Text("protocol", message="Select Protocol/app", validate=self.validators.protocol_validation, default=defaults["protocol"]))
        else:
            node["protocol"] = defaults["protocol"]
        if edit["port"]:
            questions.append(inquirer.Text("port", message="Select Port Number", validate=self.validators.port_validation, default=defaults["port"]))
        else:
            node["port"] = defaults["port"]
        if edit["options"]:
            questions.append(inquirer.Text("options", message="Pass extra options to protocol/app", validate=self.validators.default_validation, default=defaults["options"]))
        else:
            node["options"] = defaults["options"]
        if edit["logs"]:
            questions.append(inquirer.Text("logs", message="Pick logging path/file ", validate=self.validators.default_validation, default=defaults["logs"].replace("{", "{{").replace("}", "}}")))
        else:
            node["logs"] = defaults["logs"]
        if edit["tags"]:
            questions.append(inquirer.Text("tags", message="Add tags dictionary", validate=self.validators.tags_validation, default=str(defaults["tags"]).replace("{", "{{").replace("}", "}}")))
        else:
            node["tags"] = defaults["tags"]
        if edit["jumphost"]:
            questions.append(inquirer.Text("jumphost", message="Add Jumphost node", validate=self.validators.jumphost_validation, default=str(defaults["jumphost"]).replace("{", "{{").replace("}", "}}")))
        else:
            node["jumphost"] = defaults["jumphost"]
        if edit["user"]:
            questions.append(inquirer.Text("user", message="Pick username", validate=self.validators.default_validation, default=defaults["user"]))
        else:
            node["user"] = defaults["user"]
        if edit["password"]:
            questions.append(inquirer.List("password", message="Password: Use a local password, no password or a list of profiles to reference?", choices=["Local Password", "Profiles", "No Password"]))
        else:
            node["password"] = defaults["password"]
        if not edit.get("fallback"):
            node["fallback"] = defaults.get("fallback", "")

        answer = inquirer.prompt(questions)
        if answer is None:
            return False

        if "password" in answer:
            if answer["password"] == "Local Password":
                passq = [inquirer.Password("password", message="Set Password")]
                passa = inquirer.prompt(passq)
                if passa is None:
                    return False
                answer["password"] = self.app.services.config_svc.encrypt_password(passa["password"])
            elif answer["password"] == "Profiles":
                passq = [(inquirer.Text("password", message="Set a @profile or a comma separated list of @profiles", validate=self.validators.pass_validation))]
                passa = inquirer.prompt(passq)
                if passa is None:
                    return False
                answer["password"] = [p.strip() for p in passa["password"].split(",") if p.strip()]
            elif answer["password"] == "No Password":
                answer["password"] = ""

        if edit.get("fallback"):
            raw_def_fb = defaults.get("fallback", "")
            def_fb_str = json.dumps(raw_def_fb) if isinstance(raw_def_fb, (list, dict)) else str(raw_def_fb or "")
            fbq = [
                inquirer.Text(
                    "fallback",
                    message="Add fallback credentials (@profile, @p1,@p2, JSON list/dict, or compose:)",
                    validate=self.validators.fallback_validation,
                    default=def_fb_str.replace("{", "{{").replace("}", "}}"),
                )
            ]
            fba = inquirer.prompt(fbq)
            if fba is None:
                return False
            answer["fallback"] = _parse_fallback_input(fba["fallback"])

        if "tags" in answer and answer["tags"] and not str(answer["tags"]).startswith("@"):
            answer["tags"] = _parse_tags_input(answer["tags"])

        result = {**uniques, **answer, **node}
        result["type"] = "connection"
        return result

    def questions_profiles(self, unique, edit=None):
        import inquirer
        try:
            defaults = self.app.services.profiles.get_profile(unique, resolve=False)
            if "tags" not in defaults:
                defaults["tags"] = ""
            if "jumphost" not in defaults:
                defaults["jumphost"] = ""
            if "fallback" not in defaults:
                defaults["fallback"] = ""
        except Exception:  # noqa: BLE001
            defaults = {"host": "", "protocol": "", "port": "", "user": "", "options": "", "logs": "", "tags": "", "jumphost": "", "fallback": ""}
        profile = {}
        if edit is None:
            edit = {"host": True, "protocol": True, "port": True, "user": True, "password": True, "options": True, "logs": True, "tags": True, "jumphost": True, "fallback": True}
        questions = []
        if edit["host"]:
            questions.append(inquirer.Text("host", message="Add Hostname or IP", default=defaults["host"]))
        else:
            profile["host"] = defaults["host"]
        if edit["protocol"]:
            questions.append(inquirer.Text("protocol", message="Select Protocol/app", validate=self.validators.profile_protocol_validation, default=defaults["protocol"]))
        else:
            profile["protocol"] = defaults["protocol"]
        if edit["port"]:
            questions.append(inquirer.Text("port", message="Select Port Number", validate=self.validators.profile_port_validation, default=defaults["port"]))
        else:
            profile["port"] = defaults["port"]
        if edit["options"]:
            questions.append(inquirer.Text("options", message="Pass extra options to protocol/app", default=defaults["options"]))
        else:
            profile["options"] = defaults["options"]
        if edit["logs"]:
            questions.append(inquirer.Text("logs", message="Pick logging path/file ", default=defaults["logs"].replace("{", "{{").replace("}", "}}")))
        else:
            profile["logs"] = defaults["logs"]
        if edit["tags"]:
            questions.append(inquirer.Text("tags", message="Add tags dictionary", validate=self.validators.profile_tags_validation, default=str(defaults["tags"]).replace("{", "{{").replace("}", "}}")))
        else:
            profile["tags"] = defaults["tags"]
        if edit["jumphost"]:
            questions.append(inquirer.Text("jumphost", message="Add Jumphost node", validate=self.validators.profile_jumphost_validation, default=str(defaults["jumphost"]).replace("{", "{{").replace("}", "}}")))
        else:
            profile["jumphost"] = defaults["jumphost"]
        if edit["user"]:
            questions.append(inquirer.Text("user", message="Pick username", default=defaults["user"]))
        else:
            profile["user"] = defaults["user"]
        if edit["password"]:
            questions.append(inquirer.Password("password", message="Set Password"))
        else:
            profile["password"] = defaults["password"]
        if edit.get("fallback"):
            raw_def_fb = defaults.get("fallback", "")
            def_fb_str = json.dumps(raw_def_fb) if isinstance(raw_def_fb, (list, dict)) else str(raw_def_fb or "")
            questions.append(inquirer.Text("fallback", message="Add fallback credentials (@profile, @p1,@p2, JSON list/dict, or compose:)", validate=self.validators.fallback_validation, default=def_fb_str.replace("{", "{{").replace("}", "}}")))
        else:
            profile["fallback"] = defaults.get("fallback", "")

        answer = inquirer.prompt(questions)
        if answer is None:
            return False

        if "password" in answer and answer["password"] != "":
            answer["password"] = self.app.services.config_svc.encrypt_password(answer["password"])

        if "tags" in answer and answer["tags"]:
            answer["tags"] = _parse_tags_input(answer["tags"])
        if "fallback" in answer:
            answer["fallback"] = _parse_fallback_input(answer["fallback"])

        result = {**answer, **profile}
        result["id"] = unique
        return result

    def questions_bulk(self, nodes="", hosts=""):
        import inquirer
        questions = []
        questions.append(inquirer.Text("ids", message="add a comma separated list of nodes to add", default=nodes, validate=self.validators.bulk_node_validation))
        questions.append(inquirer.Text("location", message="Add a @folder, @subfolder@folder or leave empty", validate=self.validators.bulk_folder_validation))
        questions.append(inquirer.Text("host", message="Add comma separated list of Hostnames or IPs", default=hosts, validate=self.validators.bulk_host_validation))
        questions.append(inquirer.Text("protocol", message="Select Protocol/app", validate=self.validators.protocol_validation))
        questions.append(inquirer.Text("port", message="Select Port Number", validate=self.validators.port_validation))
        questions.append(inquirer.Text("options", message="Pass extra options to protocol/app", validate=self.validators.default_validation))
        questions.append(inquirer.Text("logs", message="Pick logging path/file ", validate=self.validators.default_validation))
        questions.append(inquirer.Text("tags", message="Add tags dictionary", validate=self.validators.tags_validation))
        questions.append(inquirer.Text("jumphost", message="Add Jumphost node", validate=self.validators.jumphost_validation))
        questions.append(inquirer.Text("user", message="Pick username", validate=self.validators.default_validation))
        questions.append(inquirer.List("password", message="Password: Use a local password, no password or a list of profiles to reference?", choices=["Local Password", "Profiles", "No Password"]))

        answer = inquirer.prompt(questions)
        if answer is None:
            return False

        if "password" in answer:
            if answer["password"] == "Local Password":
                passq = [inquirer.Password("password", message="Set Password")]
                passa = inquirer.prompt(passq)
                if passa is None:
                    return False
                answer["password"] = self.app.services.config_svc.encrypt_password(passa["password"])
            elif answer["password"] == "Profiles":
                passq = [(inquirer.Text("password", message="Set a @profile or a comma separated list of @profiles", validate=self.validators.pass_validation))]
                passa = inquirer.prompt(passq)
                if passa is None:
                    return False
                answer["password"] = [p.strip() for p in passa["password"].split(",") if p.strip()]
            elif answer["password"] == "No Password":
                answer["password"] = ""

        fbq = [
            inquirer.Text(
                "fallback",
                message="Add fallback credentials (@profile, @p1,@p2, JSON list/dict, or compose:)",
                validate=self.validators.fallback_validation,
            )
        ]
        fba = inquirer.prompt(fbq)
        if fba is None:
            return False
        answer["fallback"] = _parse_fallback_input(fba["fallback"])

        answer["type"] = "connection"
        if "tags" in answer and answer["tags"] and not str(answer["tags"]).startswith("@"):
            answer["tags"] = _parse_tags_input(answer["tags"])

        return answer

    def mcp_wizard(self, mcp_servers):
        """Interactive wizard to manage MCP servers."""
        import inquirer
        from .helpers import theme
        
        while True:
            options = [
                ("List Configured Servers", "list"),
                ("Add/Update Server", "add"),
                ("Enable/Disable Server", "toggle"),
                ("Remove Server", "remove"),
                ("Back", "exit")
            ]
            
            questions = [
                inquirer.List("action", message="MCP Configuration", choices=options)
            ]
            
            answers = inquirer.prompt(questions, theme=theme)
            if not answers or answers["action"] == "exit":
                return None
                
            action = answers["action"]
            
            if action == "list":
                if not mcp_servers:
                    print("\nNo MCP servers configured.\n")
                else:
                    return {"action": "list"}
            
            elif action == "add":
                questions = [
                    inquirer.Text("name", message="Server Name (identifier)"),
                    inquirer.Text("url", message="SSE URL (e.g., http://localhost:8000/sse)"),
                    inquirer.Confirm("enabled", message="Enabled?", default=True),
                    inquirer.Text("auto_load_os", message="Auto-load on specific OS (blank for any)")
                ]
                answers = inquirer.prompt(questions, theme=theme)
                if answers:
                    return {
                        "action": "add",
                        "name": answers["name"],
                        "url": answers["url"],
                        "enabled": answers["enabled"],
                        "os": answers["auto_load_os"]
                    }
            
            elif action == "toggle":
                if not mcp_servers:
                    print("\nNo servers to toggle.\n")
                    continue
                
                choices = []
                for name, cfg in mcp_servers.items():
                    status = "[Enabled]" if cfg.get("enabled", True) else "[Disabled]"
                    choices.append((f"{name} {status}", name))
                
                questions = [
                    inquirer.List("name", message="Select server to toggle", choices=choices + [("Cancel", None)])
                ]
                answers = inquirer.prompt(questions, theme=theme)
                if answers and answers["name"]:
                    current = mcp_servers[answers["name"]].get("enabled", True)
                    return {
                        "action": "update",
                        "name": answers["name"],
                        "enabled": not current
                    }
            
            elif action == "remove":
                if not mcp_servers:
                    print("\nNo servers to remove.\n")
                    continue
                    
                questions = [
                    inquirer.List("name", message="Select server to remove", choices=list(mcp_servers.keys()) + ["Cancel"])
                ]
                answers = inquirer.prompt(questions, theme=theme)
                if answers and answers["name"] != "Cancel":
                    return {"action": "remove", "name": answers["name"]}
        return None
