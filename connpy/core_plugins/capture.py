import argparse
import sys
import subprocess
import random
import socket
import time
import threading
from pexpect import TIMEOUT
from connpy import printer

class RemoteCapture:
    def __init__(self, connapp, node_name, interface, namespace=None, use_wireshark=False, tcpdump_filter=None, tcpdump_args=None):
        self.connapp = connapp
        self.node_name = node_name
        self.interface = interface
        self.namespace = namespace
        self.use_wireshark = use_wireshark
        self.tcpdump_filter = tcpdump_filter or []
        self.tcpdump_args = tcpdump_args if isinstance(tcpdump_args, list) else []

        if node_name.startswith("@"):  # fuzzy match
            matches = [k for k in connapp.nodes_list if node_name in k]
        else:
            matches = [k for k in connapp.nodes_list if k.startswith(node_name)]

        if not matches:
            printer.error(f"Node '{node_name}' not found.")
            sys.exit(2)
        elif len(matches) > 1:
            matches[0] = connapp._choose(matches, "node", "capture")

        if matches[0] is None:
            sys.exit(7)

        node_data = connapp.config.getitem(matches[0])
        self.node = connapp.node(matches[0], **node_data, config=connapp.config)

        if self.node.protocol != "ssh":
            printer.error(f"Node '{self.node.unique}' must be an SSH connection.")
            sys.exit(2)

        self.wireshark_path = connapp.config.config.get("wireshark_path")

    def _start_local_listener(self, port, ws_proc=None):
        self.listener_active = True
        self.listener_conn = None
        self.listener_connected = threading.Event()

        def listen():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("localhost", port))
                s.listen(1)
                printer.start(f"Listening on localhost:{port}")
                
                conn, addr = s.accept()
                self.listener_conn = conn
                printer.start(f"Connection from {addr}")
                self.listener_connected.set()

                try:
                    while self.listener_active:
                        data = conn.recv(4096)
                        if not data:
                            break

                        if self.use_wireshark and ws_proc:
                            try:
                                ws_proc.stdin.write(data)
                                ws_proc.stdin.flush()
                            except BrokenPipeError:
                                printer.info("Wireshark closed the pipe.")
                                break
                        else:
                            sys.stdout.buffer.write(data)
                            sys.stdout.buffer.flush()
                except Exception as e:
                    if isinstance(e, BrokenPipeError):
                        printer.info("Listener closed due to broken pipe.")
                    else:
                        printer.error(f"Listener error: {e}")
                finally:
                    conn.close()
                    self.listener_conn = None

        self.listener_thread = threading.Thread(target=listen)
        self.listener_thread.daemon = True
        self.listener_thread.start()

    def _is_port_in_use(self, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('localhost', port)) == 0

    def _find_free_port(self, start=20000, end=30000):
        for _ in range(10):
            port = random.randint(start, end)
            if not self._is_port_in_use(port):
                return port
        raise RuntimeError("No free port found for SSH tunnel.")

    def _monitor_wireshark(self, ws_proc):
        try:
            while True:
                try:
                    ws_proc.wait(timeout=1)
                    self.listener_active = False
                    if self.listener_conn:
                        printer.info("Wireshark exited, stopping listener.")
                        try:
                            self.listener_conn.shutdown(socket.SHUT_RDWR)
                            self.listener_conn.close()
                        except Exception:
                            pass
                    break
                except subprocess.TimeoutExpired:
                    if not self.listener_active:
                        break
                    time.sleep(0.2)
        except Exception as e:
            printer.warning(f"Error in monitor_wireshark: {e}")

    def _detect_sudo_requirement(self):
        base_cmd = f"tcpdump -i {self.interface} -w - -U -c 1"
        if self.namespace:
            base_cmd = f"ip netns exec {self.namespace} {base_cmd}"

        cmds = [base_cmd, f"sudo {base_cmd}"]

        printer.info(f"Verifying sudo requirement")
        for cmd in cmds:
            try:
                self.node.child.sendline(cmd)
                start_time = time.time()
                while time.time() - start_time < 5:
                    try:
                        index = self.node.child.expect([
                            r'listening on',
                            r'permission denied',
                            r'cannot',
                            r'No such file or directory',
                        ], timeout=1)

                        if index == 0:
                            self.node.child.send("\x03")
                            return "sudo" in cmd
                        else:
                            break
                    except Exception:
                        continue

                self.node.child.send("\x03")
                time.sleep(0.5)
                try:
                    self.node.child.read_nonblocking(size=1024, timeout=0.5)
                except Exception:
                    pass

            except Exception as e:
                printer.warning(f"Error during sudo detection: {e}")
                continue

        printer.error(f"Failed to run tcpdump on remote node '{self.node.unique}'")
        sys.exit(4)

    def _monitor_capture_output(self):
        try:
            index = self.node.child.expect([
                r'Broken pipe',
                r'packet[s]? captured'
            ], timeout=None)
            if index == 0:
                printer.error("Tcpdump failed: Broken pipe.")
            else:
                printer.success("Tcpdump finished capturing packets.")

            self.listener_active = False
        except:
            pass

    def _sendline_until_connected(self, cmd, retries=5, interval=2):
        for attempt in range(1, retries + 1):
            printer.info(f"Attempt {attempt}/{retries} to connect listener...")
            self.node.child.sendline(cmd)

            try:
                index = self.node.child.expect([
                    r'listening on',
                    TIMEOUT,
                    r'permission',
                    r'not permitted',
                    r'invalid',
                    r'unrecognized',
                    r'Unable',
                    r'No such',
                    r'illegal',
                    r'syntax error'
                ], timeout=5)

                if index == 0:

                    self.monitor_end = threading.Thread(target=self._monitor_capture_output)
                    self.monitor_end.daemon = True
                    self.monitor_end.start()

                    if self.listener_connected.wait(timeout=interval):
                        printer.success("Listener successfully received a connection.")
                        return True
                    else:
                        printer.warning("No connection yet. Retrying...")

                elif index == 1:
                    error = f"tcpdump did not respond within the expected time.\n" \
                            f"Command used:\n{cmd}\n" \
                            f"→ Please verify the command syntax."
                    return f"{error}"
                else:
                    before_last_line = self.node.child.before.decode().splitlines()[-1]
                    error = f"Tcpdump error detected:" \
                            f"{before_last_line}{self.node.child.after.decode()}{self.node.child.readline().decode()}".rstrip()
                    return f"{error}"

            except Exception as e:
                printer.warning(f"Unexpected error during tcpdump startup: {e}")
                return False

        return False

    def _build_tcpdump_command(self):
        base = f"tcpdump -i {self.interface}"
        if self.use_wireshark:
            base += " -w - -U"
        else:
            base += " -l"

        if self.namespace:
            base = f"ip netns exec {self.namespace} {base}"

        if self.requires_sudo:
            base = f"sudo {base}"

        if self.tcpdump_args:
                base += " " + " ".join(self.tcpdump_args)

        if self.tcpdump_filter:
                base += " " + " ".join(self.tcpdump_filter)

        base += f" | nc localhost {self.local_port}"
        return base

    def run(self):
        if self.use_wireshark:
            if not self.wireshark_path:
                printer.error("Wireshark path not set in config.\nUse '--set-wireshark-path /full/path/to/wireshark' to configure it.")
                sys.exit(1)

        self.local_port = self._find_free_port()
        self.node.options += f" -o ExitOnForwardFailure=yes -R {self.local_port}:localhost:{self.local_port}"

        connection = self.node._connect()
        if connection is not True:
            printer.error(f"Could not connect to {self.node.unique}\n{connection}")
            sys.exit(1)

        self.requires_sudo = self._detect_sudo_requirement()
        tcpdump_cmd = self._build_tcpdump_command()

        ws_proc = None
        monitor_thread = None

        if self.use_wireshark:

            printer.info(f"Live capture from {self.node.unique}:{self.interface}, launching Wireshark...")
            try:
                ws_proc = subprocess.Popen(
                    [self.wireshark_path, "-k", "-i", "-"],
                    stdin=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            except Exception as e:
                printer.error(f"Failed to launch Wireshark: {e}\nMake sure the path is correct and Wireshark is installed.")
                exit(1)

            monitor_thread = threading.Thread(target=self._monitor_wireshark, args=(ws_proc,))
            monitor_thread.daemon = True
            monitor_thread.start()
        else:
            printer.info(f"Live text capture from {self.node.unique}:{self.interface}")
            printer.info("Press Ctrl+C to stop.\n")

        try:
            self._start_local_listener(self.local_port, ws_proc=ws_proc)
            time.sleep(1)  # small delay before retry attempts

            result = self._sendline_until_connected(tcpdump_cmd, retries=5, interval=2)
            if result is not True:
                if isinstance(result, str):
                    printer.error(f"{result}")
                else:
                    printer.error("Listener connection failed after all retries.")
                self.listener_active = False
                return

            while self.listener_active:
                time.sleep(0.5)

        except KeyboardInterrupt:
            print("")
            printer.warning("Capture interrupted by user.")
            self.listener_active = False
        finally:
            if self.listener_conn:
                try:
                    self.listener_conn.shutdown(socket.SHUT_RDWR)
                    self.listener_conn.close()
                except:
                    pass
            if hasattr(self.node, "child"):
                self.node.child.close(force=True)
            if self.listener_thread.is_alive():
                self.listener_thread.join()
            if monitor_thread and monitor_thread.is_alive():
                monitor_thread.join()


class Parser:
    def __init__(self):
        self.parser = argparse.ArgumentParser(description="Capture packets remotely using a saved SSH node", epilog="All unknown arguments will be passed to tcpdump.")

        self.parser.add_argument("node", nargs='?', help="Name of the saved node (must use SSH)")
        self.parser.add_argument("interface", nargs='?', help="Network interface to capture on")
        self.parser.add_argument("--ns", "--namespace", dest="namespace", help="Optional network namespace")
        self.parser.add_argument("-w","--wireshark", action="store_true", help="Open live capture in Wireshark")
        self.parser.add_argument("--set-wireshark-path", metavar="PATH", help="Set the default path to Wireshark binary")
        self.parser.add_argument(
            "-f", "--filter",
            dest="tcpdump_filter",
            metavar="ARG",
            nargs="*",
            default=["not", "port", "22"],
            help="tcpdump filter expression (e.g., -f port 443 and udp). Default: not port 22"
        )
        self.parser.add_argument(
            "--unknown-args",
            action="store_true",
            default=True,
            help=argparse.SUPPRESS
        )

class Entrypoint:
    def __init__(self, args, parser, connapp):
        if "--" in args.unknown_args:
            args.unknown_args.remove("--")
        if args.set_wireshark_path:
            connapp._change_settings("wireshark_path", args.set_wireshark_path)
            return

        if not args.node or not args.interface:
            parser.error("node and interface are required unless --set-wireshark-path is used")

        capture = RemoteCapture(
            connapp=connapp,
            node_name=args.node,
            interface=args.interface,
            namespace=args.namespace,
            use_wireshark=args.wireshark,
            tcpdump_filter=args.tcpdump_filter,
            tcpdump_args=args.unknown_args
        )
        capture.run()

def _connpy_completion(wordsnumber, words, info = None):
    if wordsnumber == 3:
        result = ["--help", "--set-wireshark-path"]
        result.extend(info["nodes"])
    elif wordsnumber == 5 and words[1] in info["nodes"]:
        result = ['--wireshark', '--namespace', '--filter', '--help']
    elif wordsnumber == 6 and words[3] in ["-w", "--wireshark"]:
        result = ['--namespace', '--filter', '--help']
    elif wordsnumber == 7 and words[3] in ["-n", "--namespace"]:
        result = ['--wireshark', '--filter', '--help']
    elif wordsnumber == 8:
        if any(w in words for w in ["-w", "--wireshark"]) and any(w in words for w in ["-n", "--namespace"]):
            result = ['--filter', '--help']
        else:
            result = []

    return result
