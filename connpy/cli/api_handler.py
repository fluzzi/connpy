import sys
from .. import printer
from ..services.exceptions import ConnpyError

class APIHandler:
    def __init__(self, app):
        self.app = app

    def dispatch(self, args):
        try:
            status = self.app.services.system.get_api_status()
            
            if args.command == "stop":
                if not status["running"]:
                    printer.warning("API does not seem to be running.")
                else:
                    stopped = self.app.services.system.stop_api()
                    if stopped:
                        printer.success("API stopped successfully.")
            
            elif args.command == "restart":
                port = args.data if args.data and isinstance(args.data, int) else None
                if status["running"]:
                    printer.info(f"Stopping server with process ID {status['pid']}...")
                
                # Service handles port preservation if port is None
                self.app.services.system.restart_api(port=port)
                
                if status["running"]:
                    printer.info(f"Server with process ID {status['pid']} stopped.")
                
                # Re-fetch status to show the actual port used
                new_status = self.app.services.system.get_api_status()
                printer.success(f"API restarted on port {new_status.get('port', 'unknown')}.")

            elif args.command == "start":
                if status["running"]:
                    msg = f"Connpy server is already running (PID: {status['pid']}"
                    if status.get("port"):
                        msg += f", Port: {status['port']}"
                    msg += ")."
                    printer.warning(msg)
                else:
                    port = args.data if args.data and isinstance(args.data, int) else 8048
                    self.app.services.system.start_api(port=port)
                    printer.success(f"API started on port {port}.")
                
            elif args.command == "debug":
                port = args.data if args.data and isinstance(args.data, int) else 8048
                self.app.services.system.debug_api(port=port)
        except ConnpyError as e:
            printer.error(str(e))
            sys.exit(1)
