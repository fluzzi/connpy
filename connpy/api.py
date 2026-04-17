import os
import signal
import time

# Suppress harmless but noisy gRPC fork() warnings from pexpect child processes
os.environ["GRPC_VERBOSITY"] = "NONE"
os.environ["GRPC_ENABLE_FORK_SUPPORT"] = "0"

from connpy import hooks, printer
from connpy.configfile import configfile

PID_FILE1 = "/run/connpy.pid"
PID_FILE2 = "/tmp/connpy.pid"

def _wait_for_termination():
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        pass

def stop_api():
    # Read the process ID (pid) from the file
    try:
        with open(PID_FILE1, "r") as f:
            pid = int(f.readline().strip())
            port_line = f.readline().strip()
            port = int(port_line) if port_line else None
        PID_FILE = PID_FILE1
    except (FileNotFoundError, ValueError, OSError):
        try:
            with open(PID_FILE2, "r") as f:
                pid = int(f.readline().strip())
                port_line = f.readline().strip()
                port = int(port_line) if port_line else None
            PID_FILE = PID_FILE2
        except (FileNotFoundError, ValueError, OSError):
            printer.warning("Connpy API server is not running.")
            return None
    # Send a SIGTERM signal to the process
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        printer.warning(f"Process kill failed (maybe already dead): {e}")
    # Delete the PID file
    os.remove(PID_FILE)
    printer.info(f"Server with process ID {pid} stopped.")
    return port

def debug_api(port=8048, config=None):
    from .grpc.server import serve
    conf = config or configfile()
    server = serve(conf, port=port, debug=True)
    printer.info(f"gRPC Server running in debug mode on port {port}...")
    _wait_for_termination()
    server.stop(0)

def start_server(port=8048, config=None):
    from .grpc.server import serve
    conf = config or configfile()
    server = serve(conf, port=port, debug=False)
    _wait_for_termination()

def start_api(port=8048, config=None):
    # Check if already running via PID file verification
    for pid_file in [PID_FILE1, PID_FILE2]:
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    pid = int(f.readline().strip())
                os.kill(pid, 0)
                # If we get here, process exists
                return
            except (ValueError, OSError, ProcessLookupError):
                # Stale PID file, ignore here, start_api will overwrite
                pass

    pid = os.fork()
    if pid == 0:
        start_server(port, config=config)
    else:
        try:
            with open(PID_FILE1, "w") as f:
                f.write(str(pid) + "\n" + str(port))
        except OSError:
            try:
                with open(PID_FILE2, "w") as f:
                    f.write(str(pid) + "\n" + str(port))
            except OSError:
                printer.error("Couldn't create PID file.")
                exit(1)
        printer.start(f"gRPC Server is running with process ID {pid} on port {port}")
