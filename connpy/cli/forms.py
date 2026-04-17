import ast
import inquirer
from .validators import Validators

class Forms:
    def __init__(self, app):
        self.app = app
        self.validators = Validators(app)

    def questions_edit(self):
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
        return inquirer.prompt(questions)

    def questions_nodes(self, unique, uniques=None, edit=None):
        try:
            defaults = self.app.services.nodes.get_node_details(unique)
            if "tags" not in defaults:
                defaults["tags"] = ""
            if "jumphost" not in defaults:
                defaults["jumphost"] = ""
        except Exception:
            defaults = {"host": "", "protocol": "", "port": "", "user": "", "options": "", "logs": "", "tags": "", "password": "", "jumphost": ""}
        node = {}
        if edit is None:
            edit = {"host": True, "protocol": True, "port": True, "user": True, "password": True, "options": True, "logs": True, "tags": True, "jumphost": True}
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
                answer["password"] = passa["password"].split(",")
            elif answer["password"] == "No Password":
                answer["password"] = ""
                
        if "tags" in answer and not answer["tags"].startswith("@") and answer["tags"]:
            answer["tags"] = ast.literal_eval(answer["tags"])
            
        result = {**uniques, **answer, **node}
        result["type"] = "connection"
        return result

    def questions_profiles(self, unique, edit=None):
        try:
            defaults = self.app.services.profiles.get_profile(unique, resolve=False)
            if "tags" not in defaults:
                defaults["tags"] = ""
            if "jumphost" not in defaults:
                defaults["jumphost"] = ""
        except Exception:
            defaults = {"host": "", "protocol": "", "port": "", "user": "", "options": "", "logs": "", "tags": "", "jumphost": ""}
        profile = {}
        if edit is None:
            edit = {"host": True, "protocol": True, "port": True, "user": True, "password": True, "options": True, "logs": True, "tags": True, "jumphost": True}
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
            
        answer = inquirer.prompt(questions)
        if answer is None:
            return False
            
        if "password" in answer:
            if answer["password"] != "":
                answer["password"] = self.app.services.config_svc.encrypt_password(answer["password"])
                
        if "tags" in answer and answer["tags"]:
            answer["tags"] = ast.literal_eval(answer["tags"])
            
        result = {**answer, **profile}
        result["id"] = unique
        return result

    def questions_bulk(self, nodes="", hosts=""):
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
                answer["password"] = self.app.services.config_svc.encrypt_password(passa["password"])
            elif answer["password"] == "Profiles":
                passq = [(inquirer.Text("password", message="Set a @profile or a comma separated list of @profiles", validate=self.validators.pass_validation))]
                passa = inquirer.prompt(passq)
                answer["password"] = passa["password"].split(",")
            elif answer["password"] == "No Password":
                answer["password"] = ""
                
        answer["type"] = "connection"
        if "tags" in answer and not answer["tags"].startswith("@") and answer["tags"]:
            answer["tags"] = ast.literal_eval(answer["tags"])
            
        return answer
