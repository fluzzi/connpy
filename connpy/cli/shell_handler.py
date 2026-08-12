import os
import sys
import shutil
import shlex
import socket
from .. import printer
from ..core import node

class ShellHandler:
    def __init__(self, app):
        self.app = app

    def dispatch(self, args):
        shell_config = self.app.config.config.get("shell", {}) if hasattr(self.app.config, "config") else {}
        command = getattr(args, 'command_override', None) or shell_config.get("command") or os.environ.get("SHELL", "/bin/bash")

        try:
            exe = shlex.split(command)[0]
        except Exception:
            exe = command

        if not shutil.which(exe):
            printer.error(f"Shell command executable not found: {exe}")
            sys.exit(1)

        node_info = self._build_local_identity(shell_config)

        tags = {
            "os": node_info["os"],
            "prompt": node_info["prompt"]
        }

        n = node(
            unique=node_info["name"],
            host=command,
            protocol="local",
            config=self.app.config,
            tags=tags
        )

        capture_file = getattr(args, 'capture_file', None)
        if capture_file:
            n.logs = capture_file
        elif shell_config.get("logging"):
            n.logs = shell_config.get("log_path", os.path.expanduser("~/.config/conn/shell_logs/session.log"))

        n.interact(debug=getattr(args, 'debug', False))

    def _build_local_identity(self, shell_config):
        return {
            "name": "local-shell",
            "host": socket.gethostname(),
            "os": shell_config.get("os", "linux"),
            "prompt": shell_config.get("prompt", r'\$\s*$|#\s*$')
        }
