import re
import ast
import inquirer

class Validators:
    def __init__(self, app):
        self.app = app

    def host_validation(self, answers, current, regex = "^.+$"):
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Host cannot be empty")
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        return True

    def profile_protocol_validation(self, answers, current, regex = "(^ssh$|^telnet$|^kubectl$|^docker$|^ssm$|^$)"):
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Pick between ssh, telnet, kubectl, docker, ssm or leave empty")
        return True

    def protocol_validation(self, answers, current, regex = "(^ssh$|^telnet$|^kubectl$|^docker$|^ssm$|^$|^@.+$)"):
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Pick between ssh, telnet, kubectl, docker, ssm, leave empty or @profile")
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        return True

    def profile_port_validation(self, answers, current, regex = "(^[0-9]*$)"):
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Pick a port between 1-65535, @profile o leave empty")
        try:
            port = int(current)
        except ValueError:
            port = 0
        if current != "" and not 1 <= int(port) <= 65535:
            raise inquirer.errors.ValidationError("", reason="Pick a port between 1-65535 or leave empty")
        return True

    def port_validation(self, answers, current, regex = "(^[0-9]*$|^@.+$)"):
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Pick a port between 1-65535, @profile or leave empty")
        try:
            port = int(current)
        except ValueError:
            port = 0
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        elif current != "" and not 1 <= int(port) <= 65535:
            raise inquirer.errors.ValidationError("", reason="Pick a port between 1-65535, @profile o leave empty")
        return True

    def pass_validation(self, answers, current, regex = "(^@.+$)"):
        profiles = current.split(",")
        for i in profiles:
            if not re.match(regex, i) or i[1:] not in self.app.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(i))
        return True

    def tags_validation(self, answers, current):
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        elif current != "":
            isdict = False
            try:
                isdict = ast.literal_eval(current)
            except Exception:
                pass
            if not isinstance (isdict, dict):
                raise inquirer.errors.ValidationError("", reason="Tags should be a python dictionary.".format(current))
        return True

    def profile_tags_validation(self, answers, current):
        if current != "":
            isdict = False
            try:
                isdict = ast.literal_eval(current)
            except Exception:
                pass
            if not isinstance (isdict, dict):
                raise inquirer.errors.ValidationError("", reason="Tags should be a python dictionary.".format(current))
        return True

    def jumphost_validation(self, answers, current):
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        elif current != "":
            if current not in self.app.nodes_list:
                raise inquirer.errors.ValidationError("", reason="Node {} don't exist.".format(current))
        return True

    def profile_jumphost_validation(self, answers, current):
        if current != "":
            if current not in self.app.nodes_list:
                raise inquirer.errors.ValidationError("", reason="Node {} don't exist.".format(current))
        return True

    def default_validation(self, answers, current):
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        return True

    def bulk_node_validation(self, answers, current, regex = "^[0-9a-zA-Z_.,$#-]+$"):
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Host cannot be empty")
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        return True

    def bulk_folder_validation(self, answers, current):
        if not self.app.case:
            current = current.lower()
            
        candidate = current
        if "/" in current:
            candidate = current.split("/")[0]
            
        matches = list(filter(lambda k: k == candidate, self.app.folders))
        if current != "" and len(matches) == 0:
            raise inquirer.errors.ValidationError("", reason="Location {} don't exist".format(current))
        return True

    def bulk_host_validation(self, answers, current, regex = "^.+$"):
        if not re.match(regex, current):
            raise inquirer.errors.ValidationError("", reason="Host cannot be empty")
        if current.startswith("@"):
            if current[1:] not in self.app.profiles:
                raise inquirer.errors.ValidationError("", reason="Profile {} don't exist".format(current))
        hosts = current.split(",")
        nodes = answers["ids"].split(",")
        if len(hosts) > 1 and len(hosts) != len(nodes):
                raise inquirer.errors.ValidationError("", reason="Hosts list should be the same length of nodes list")
        return True
