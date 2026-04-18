from .base import BaseService
from .exceptions import ConnpyError

class SystemService(BaseService):
    """Business logic for application lifecycle (API, processes)."""

    def start_api(self, port=None):
        """Start the Connpy REST API."""
        from connpy.api import start_api
        try:
            start_api(port, config=self.config)
        except Exception as e:
            raise ConnpyError(f"Failed to start API: {e}")

    def debug_api(self, port=None):
        """Start the Connpy REST API in debug mode."""
        from connpy.api import debug_api
        try:
            debug_api(port, config=self.config)
        except Exception as e:
            raise ConnpyError(f"Failed to start API in debug mode: {e}")


    def stop_api(self):
        """Stop the Connpy REST API."""
        try:
            import os
            import signal
            
            pids = ["/run/connpy.pid", "/tmp/connpy.pid"]
            stopped = False
            for pid_file in pids:
                if os.path.exists(pid_file):
                    try:
                        with open(pid_file, "r") as f:
                            # Read only the first line (PID)
                            line = f.readline().strip()
                            if not line:
                                continue
                            pid = int(line)
                        os.kill(pid, signal.SIGTERM)
                        # Remove the PID file after successful kill
                        os.remove(pid_file)
                        stopped = True
                    except (ValueError, OSError, ProcessLookupError):
                        # If process is already dead, just remove the stale PID file
                        try:
                            os.remove(pid_file)
                        except OSError:
                            pass
                        continue
            return stopped
        except Exception as e:
            raise ConnpyError(f"Failed to stop API: {e}")

    def restart_api(self, port=None):
        """Restart the Connpy REST API, maintaining the current port if none provided."""
        if port is None:
            status = self.get_api_status()
            if status["running"] and status.get("port"):
                port = status["port"]
        
        self.stop_api()
        import time
        time.sleep(1)
        self.start_api(port)

    def get_api_status(self):
        """Check if the API is currently running."""
        import os
        pids = ["/run/connpy.pid", "/tmp/connpy.pid"]
        for pid_file in pids:
            if os.path.exists(pid_file):
                try:
                    with open(pid_file, "r") as f:
                        pid_line = f.readline().strip()
                        port_line = f.readline().strip()
                        if not pid_line:
                            continue
                        pid = int(pid_line)
                        port = int(port_line) if port_line else None
                    # Signal 0 checks for process existence without killing it
                    os.kill(pid, 0)
                    return {"running": True, "pid": pid, "port": port, "pid_file": pid_file}
                except (ValueError, OSError, ProcessLookupError):
                    continue
        return {"running": False}
