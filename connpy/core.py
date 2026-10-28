#!/usr/bin/env python3
#Imports
import os
import re
import ipaddress
import pexpect
import shlex
import ast
from time import sleep,time
import datetime
import sys
import threading
from pathlib import Path
from copy import deepcopy
from .hooks import ClassHook, MethodHook
import io
import asyncio
import fcntl
from . import printer
from .tunnels import LocalStream
from contextlib import contextmanager

@contextmanager
def copilot_terminal_mode():
    import sys, tty, termios
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
        
        # First we switch to absolute raw mode to disable ISIG, ICANON, ECHO, etc.
        tty.setraw(fd)
        
        # Then we re-enable OPOST so rich.Live renders correctly
        new_settings = termios.tcgetattr(fd)
        new_settings[1] = new_settings[1] | termios.OPOST
        termios.tcsetattr(fd, termios.TCSANOW, new_settings)
        
        yield
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSANOW, old_settings)
        except Exception:
            pass

_ALLOWED_SSH_NOARG_FLAGS = frozenset({
    "-4", "-6", "-A", "-C", "-X", "-Y", "-a", "-q", "-t", "-v", "-vv", "-vvv", "-x",
})
_NON_TUNNEL_SSH_FLAGS = frozenset({
    "-A", "-X", "-Y", "-t", "-v", "-vv", "-vvv", "-x",
})
_ALLOWED_SSH_ARG_FLAGS = frozenset({
    "-c", "-i", "-m",
})
_ALLOWED_SSH_O_KEYS = frozenset({
    "addkeystoagent",
    "ciphers",
    "compression",
    "connectionattempts",
    "connecttimeout",
    "fingerprinthash",
    "forwardagent",
    "gssapiauthentication",
    "hostkeyalgorithms",
    "identitiesonly",
    "identityfile",
    "ipqos",
    "kbdinteractiveauthentication",
    "kexalgorithms",
    "loglevel",
    "macs",
    "numberofpasswordprompts",
    "preferredauthentications",
    "pubkeyacceptedalgorithms",
    "pubkeyacceptedkeytypes",
    "pubkeyauthentication",
    "serveralivecountmax",
    "serveraliveinterval",
    "stricthostkeychecking",
    "tcpkeepalive",
    "userknownhostsfile",
    "verifyhostkeydns",
})
_SAFE_SSH_PATH_RE = re.compile(r"^[A-Za-z0-9._/@:,+~= -]+$", re.ASCII)
_SAFE_SSH_O_VALUE_RE = re.compile(r"^[A-Za-z0-9._/@:,+~=-]+$", re.ASCII)
_LEGACY_SSH_UNSAFE_RE = re.compile(r'[\s\x00-\x1f\x7f;|&$`><"\']')

#functions and classes
@ClassHook
class node:
    ''' This class generates a node object. Containts all the information and methods to connect and interact with a device using ssh or telnet.

    ### Attributes:  

        - output (str): Output of the commands you ran with run or test 
                        method.  

        - result(bool): True if expected value is found after running 
                        the commands using test method.

        - status (int): 0 if the method run or test run successfully.
                        1 if connection failed.
                        2 if expect timeouts without prompt or EOF.

        '''
    
    def __init__(self, unique, host, options='', logs='', password='', port='', protocol='', user='', config='', tags='', jumphost=''):
        ''' 
            
        ### Parameters:  

            - unique (str): Unique name to assign to the node.

            - host   (str): IP address or hostname of the node.

        ### Optional Parameters:  

            - options  (str): Additional options to pass the ssh/telnet for
                              connection.  

            - logs     (str): Path/file for storing the logs. You can use 
                              ${unique},${host}, ${port}, ${user}, ${protocol} 
                              as variables.  

            - password (str): Encrypted or plaintext password.  

            - port     (str): Port to connect to node, default 22 for ssh and 23 
                              for telnet.  

            - protocol (str): Select ssh, mosh, telnet, kubectl, docker or ssm. Default is ssh.  

            - user     (str): Username to of the node.  

            - config   (obj): Pass the object created with class configfile with 
                              key for decryption and extra configuration if you 
                              are using connection manager.  

            - tags   (dict) : Tags useful for automation and personal porpuse
                              like "os", "prompt" and "screenleght_command"
                              
            - jumphost (str): Reference another node to be used as a jumphost
        '''
        self.config = config
        if config == '':
            self.idletime = 0
            self.key = None
        else:
            self.idletime = config.config["idletime"]
            self.key = config.key
        self.unique = unique
        attr = {"host": host, "logs": logs, "options":options, "port": port, "protocol": protocol, "user": user, "tags": tags, "jumphost": jumphost}
        for key in attr:
            profile = re.search("^@(.*)", str(attr[key]))
            if profile and config != '':
                try:
                    setattr(self,key,deepcopy(config.profiles[profile.group(1)][key]))
                except KeyError:
                    setattr(self,key,"")
            elif attr[key] == '' and key == "protocol":
                try:
                    setattr(self,key,config.profiles["default"][key])
                except (KeyError, AttributeError):
                    setattr(self,key,"ssh")
            else: 
                setattr(self,key,attr[key])
        self._orig_protocol = self.protocol
        if isinstance(password,list):
            self.password = []
            for i, s in enumerate(password):
                profile = re.search("^@(.*)", password[i])
                if profile and config != '':
                    self.password.append(config.profiles[profile.group(1)]["password"])
                else:
                    self.password.append(password[i])
        else:
            self.password = [password]
        if self.jumphost != "" and config != '':
            raw_cmd, jh_passwords = self._build_jumphost_chain(self.jumphost, config)
            if jh_passwords:
                self.password = jh_passwords + self.password
            if raw_cmd:
                escaped = raw_cmd.replace('\\', '\\\\').replace('"', '\\"')
                self.jumphost = f'-o ProxyCommand="{escaped}"'
            else:
                self.jumphost = ""
        
        self.output = ""
        self.status = 1
        self.result = {}
        self.cmd_byte_positions = [(0, None)]

    @staticmethod
    def _resolve_jumphost_data(jh_dict, config):
        '''Resolve @profile references and normalize passwords in a jumphost dict.'''
        for key in jh_dict:
            profile = re.search("^@(.*)", str(jh_dict[key]))
            if profile:
                try:
                    jh_dict[key] = deepcopy(config.profiles[profile.group(1)][key])
                except KeyError:
                    jh_dict[key] = ""
            elif jh_dict[key] == '' and key == "protocol":
                try:
                    jh_dict[key] = config.profiles["default"][key]
                except KeyError:
                    jh_dict[key] = "ssh"
        if isinstance(jh_dict["password"], list):
            resolved = []
            for p in jh_dict["password"]:
                profile = re.search("^@(.*)", p)
                if profile:
                    resolved.append(config.profiles[profile.group(1)]["password"])
                else:
                    resolved.append(p)
            jh_dict["password"] = resolved
        else:
            jh_dict["password"] = [jh_dict["password"]]
        return jh_dict

    @staticmethod
    def _validate_legacy_ssh_hop_fields(host, user, port):
        """Minimal injection guard for legacy SSH jumphost fields (preserves DOMAIN\\user, %scope, etc.)."""
        host_s = str(host) if host is not None else ""
        if host_s.startswith("-"):
            raise ValueError("Host cannot start with '-'")
        if not host_s or _LEGACY_SSH_UNSAFE_RE.search(host_s):
            raise ValueError(f"Invalid characters in host: '{host}'")
        host_bare = (
            host_s[1:-1]
            if (host_s.startswith("[") and host_s.endswith("]"))
            else host_s
        )
        if not host_bare or host_bare.startswith("-"):
            raise ValueError(f"Invalid characters in host: '{host}'")

        user_s = str(user) if user is not None else ""
        if user_s.startswith("-"):
            raise ValueError("User cannot start with '-'")
        if user_s and _LEGACY_SSH_UNSAFE_RE.search(user_s):
            raise ValueError(f"Invalid characters in user: '{user}'")

        port_out = ""
        if port != "" and port is not None:
            if isinstance(port, (bool, float)):
                raise ValueError(f"Invalid TCP SSH port '{port}'")
            port_s = str(port).strip()
            if port_s:
                if (
                    not port_s.isascii()
                    or not re.fullmatch(r"[0-9]+", port_s, re.ASCII)
                    or not (1 <= int(port_s) <= 65535)
                ):
                    raise ValueError(f"Invalid TCP SSH port '{port}'")
                port_out = port_s

        return host_bare, port_out

    @staticmethod
    def _validate_mosh_ssh_fields(
        host,
        user,
        port,
        tags,
        *,
        strip_non_tunnel_flags=False,
        allow_reverse_tunnel_port=None,
    ):
        """Validate host, user, TCP SSH port, and mosh_ssh_options for Mosh SSH backend.

        Accepts IPv4, hostnames, and bare or bracketed IPv6 literals (including
        IPv4-mapped IPv6 like ::ffff:1.2.3.4, without scope-id '%').
        Returns:
            tuple[str, str, str]: (host_bare_str, validated_port_str, validated_ssh_opts_str)
        """
        host_s = str(host) if host is not None else ""
        if host_s.startswith("-"):
            raise ValueError("Host cannot start with '-'")
        if not host_s:
            raise ValueError(f"Invalid characters in host: '{host}'")

        if (host_s.startswith("[") and host_s.endswith("]")) or ":" in host_s:
            inner = host_s[1:-1] if (host_s.startswith("[") and host_s.endswith("]")) else host_s
            if "%" in inner or not re.fullmatch(r"[0-9A-Fa-f:.]+", inner, re.ASCII):
                raise ValueError(f"Invalid characters in host: '{host}'")
            try:
                ipaddress.IPv6Address(inner)
            except ValueError as exc:
                raise ValueError(f"Invalid characters in host: '{host}'") from exc
            host_bare = inner
        else:
            if not re.fullmatch(r"[A-Za-z0-9._-]+", host_s, re.ASCII):
                raise ValueError(f"Invalid characters in host: '{host}'")
            host_bare = host_s

        user_s = str(user) if user is not None else ""
        if user_s.startswith("-"):
            raise ValueError("User cannot start with '-'")
        if user_s and not re.fullmatch(
            r"[A-Za-z0-9._-]+(?:@[A-Za-z0-9._-]+)?", user_s, re.ASCII
        ):
            raise ValueError(f"Invalid characters in user: '{user}'")

        port_out = ""
        if port != "" and port is not None:
            if isinstance(port, (bool, float)):
                raise ValueError(f"Invalid TCP SSH port '{port}'")
            port_s = str(port).strip()
            if port_s:
                if (
                    not port_s.isascii()
                    or not re.fullmatch(r"[0-9]+", port_s, re.ASCII)
                    or not (1 <= int(port_s) <= 65535)
                ):
                    raise ValueError(f"Invalid TCP SSH port '{port}'")
                port_out = port_s

        allowed_r_port = None
        if allow_reverse_tunnel_port is not None:
            r_port_s = str(allow_reverse_tunnel_port).strip()
            if (
                isinstance(allow_reverse_tunnel_port, (bool, float))
                or not r_port_s.isascii()
                or not re.fullmatch(r"[0-9]+", r_port_s, re.ASCII)
                or not (1 <= int(r_port_s) <= 65535)
            ):
                raise ValueError(
                    f"Invalid allow_reverse_tunnel_port '{allow_reverse_tunnel_port}'"
                )
            allowed_r_port = r_port_s

        opts_out = ""
        raw_opts = tags.get("mosh_ssh_options", "") if isinstance(tags, dict) else ""
        if raw_opts:
            ssh_opts = str(raw_opts).strip()
            if "\x00" in ssh_opts or "\n" in ssh_opts or "\r" in ssh_opts:
                raise ValueError("Disallowed control characters in mosh_ssh_options")
            if ssh_opts:
                try:
                    tokens = shlex.split(ssh_opts)
                except ValueError as exc:
                    raise ValueError(
                        "Invalid mosh_ssh_options: unbalanced quotes"
                    ) from exc
                r_count = 0
                exit_fwd_count = 0
                out_tokens = []
                idx = 0
                # Normative branch evaluation order:
                # 1. NOARG -> 2. ARG separate -> 3. ARG attached -> 4. -R -> 5. -o/-oXXX -> 6. other
                while idx < len(tokens):
                    tok = tokens[idx]
                    if tok in _ALLOWED_SSH_NOARG_FLAGS:
                        if not (strip_non_tunnel_flags and tok in _NON_TUNNEL_SSH_FLAGS):
                            out_tokens.append(tok)
                        idx += 1
                    elif tok in _ALLOWED_SSH_ARG_FLAGS:
                        if idx + 1 >= len(tokens) or not tokens[idx + 1].strip():
                            raise ValueError(
                                f"Missing argument for ssh option '{tok}' in mosh_ssh_options"
                            )
                        arg_val = tokens[idx + 1]
                        val_re = _SAFE_SSH_PATH_RE if tok == "-i" else _SAFE_SSH_O_VALUE_RE
                        if arg_val.startswith("-") or not val_re.fullmatch(arg_val):
                            raise ValueError(
                                f"Invalid argument for ssh option '{tok}' in mosh_ssh_options"
                            )
                        out_tokens.extend([tok, arg_val])
                        idx += 2
                    elif tok[:2] in _ALLOWED_SSH_ARG_FLAGS and len(tok) > 2:
                        flag_prefix = tok[:2]
                        arg_val = tok[2:]
                        val_re = _SAFE_SSH_PATH_RE if flag_prefix == "-i" else _SAFE_SSH_O_VALUE_RE
                        if (
                            not arg_val.strip()
                            or arg_val.startswith("-")
                            or not val_re.fullmatch(arg_val)
                        ):
                            raise ValueError(
                                f"Invalid argument for ssh option '{flag_prefix}' in mosh_ssh_options"
                            )
                        out_tokens.append(tok)
                        idx += 1
                    elif tok == "-R":
                        if (
                            allowed_r_port is None
                            or r_count >= 1
                            or idx + 1 >= len(tokens)
                            or tokens[idx + 1]
                            != f"{allowed_r_port}:localhost:{allowed_r_port}"
                        ):
                            raise ValueError(
                                f"Disallowed ssh option in mosh_ssh_options: '{tok}'"
                            )
                        r_count += 1
                        out_tokens.extend([tok, tokens[idx + 1]])
                        idx += 2
                    elif tok == "-o" or tok.startswith("-o"):
                        if tok == "-o":
                            if idx + 1 >= len(tokens) or not tokens[idx + 1].strip():
                                raise ValueError(
                                    "Missing argument for ssh option '-o' in mosh_ssh_options"
                                )
                            opt_spec = tokens[idx + 1]
                            step = 2
                        else:
                            opt_spec = tok[2:]
                            step = 1
                        if re.search(r"(?:localcommand|proxycommand)", opt_spec, re.IGNORECASE):
                            raise ValueError(
                                "mosh_ssh_options cannot contain LocalCommand or ProxyCommand"
                            )
                        m = re.fullmatch(r"([A-Za-z0-9]+)=(.+)", opt_spec, re.ASCII)
                        if (
                            not m
                            or not m.group(2).strip()
                            or not _SAFE_SSH_O_VALUE_RE.fullmatch(m.group(2).strip())
                        ):
                            raise ValueError(
                                f"Disallowed -o directive in mosh_ssh_options: '{opt_spec}'"
                            )
                        key_raw = m.group(1)
                        key_lower = key_raw.lower()
                        val_str = m.group(2).strip()
                        if key_lower == "exitonforwardfailure":
                            if (
                                allowed_r_port is None
                                or exit_fwd_count >= 1
                                or val_str.lower() != "yes"
                            ):
                                raise ValueError(
                                    f"Disallowed -o directive in mosh_ssh_options: '{opt_spec}'"
                                )
                            exit_fwd_count += 1
                        elif key_lower not in _ALLOWED_SSH_O_KEYS:
                            raise ValueError(
                                f"Disallowed -o directive in mosh_ssh_options: '{opt_spec}'"
                            )
                        if not (strip_non_tunnel_flags and key_lower == "forwardagent"):
                            if tok == "-o":
                                out_tokens.extend(["-o", f"{key_raw}={val_str}"])
                            else:
                                out_tokens.append(f"-o{key_raw}={val_str}")
                        idx += step
                    else:
                        if re.search(r"(?:localcommand|proxycommand)", tok, re.IGNORECASE):
                            raise ValueError(
                                "mosh_ssh_options cannot contain LocalCommand or ProxyCommand"
                            )
                        raise ValueError(
                            f"Disallowed ssh option in mosh_ssh_options: '{tok}'"
                        )
                opts_out = shlex.join(out_tokens)

        return host_bare, port_out, opts_out

    def _build_jumphost_chain(self, jumphost_name, config, visited=None, depth=0, target_host="%h", target_port="%p"):
        '''Recursively build ProxyCommand for chained jumphosts.

        Returns:
            tuple: (raw_proxy_command, passwords_list)
                - raw_proxy_command: Command string to embed in ProxyCommand
                - passwords_list: Ordered passwords (innermost first)

        Raises:
            ValueError: On circular references or exceeding max depth (5).
        '''
        if depth >= 5:
            raise ValueError("Jumphost chain exceeds maximum depth of 5 hops")
        if visited is None:
            visited = []
        if jumphost_name in visited:
            cycle = " -> ".join(visited + [jumphost_name])
            raise ValueError(f"Circular jumphost reference detected: {cycle}")
        visited = visited + [jumphost_name]

        jh = config.getitem(jumphost_name)
        jh = self._resolve_jumphost_data(jh, config)

        if jh["protocol"] == "mosh":
            jh_host, jh_port, jh_opts = self._validate_mosh_ssh_fields(
                jh.get("host", ""),
                jh.get("user", ""),
                jh.get("port", ""),
                jh.get("tags", ""),
                strip_non_tunnel_flags=True,
            )
        elif jh["protocol"] == "ssh":
            jh_host, jh_port = self._validate_legacy_ssh_hop_fields(
                jh.get("host", ""),
                jh.get("user", ""),
                jh.get("port", ""),
            )
            jh_opts = jh.get("options", "")
        else:
            jh_host = jh.get("host", "")
            jh_port = str(jh["port"]).strip() if jh.get("port") not in ("", None) else ""
            jh_opts = jh.get("options", "")

        passwords = []
        inner_proxy_opt = ""

        # Recursively resolve inner jumphost
        if jh.get("jumphost", "") != "":
            if jh["protocol"] not in ["ssh", "mosh"]:
                raise ValueError(
                    f"Jumphost '{jumphost_name}' uses protocol '{jh['protocol']}' "
                    f"which does not support chained jumphosts. "
                    f"Only SSH or Mosh jumphosts can have their own jumphosts."
                )
            parent_port = jh_port if jh_port != "" else "22"
            inner_raw_cmd, inner_passwords = self._build_jumphost_chain(
                jh["jumphost"], config, visited, depth + 1, target_host=jh_host, target_port=parent_port
            )
            passwords = inner_passwords
            escaped = inner_raw_cmd.replace('\\', '\\\\').replace('"', '\\"')
            inner_proxy_opt = f'-o ProxyCommand="{escaped}"'

        # Collect this hop's passwords
        if jh["password"] != [""]:
            passwords = passwords + jh["password"]

        t_port = target_port if target_port != "" else "22"
        if jh["protocol"] in ["ssh", "mosh"]:
            if target_host == "%h" and t_port == "%p":
                w_target = "%h"
                w_port = "%p"
            else:
                t_host_bare, t_port_norm = self._validate_legacy_ssh_hop_fields(
                    target_host, "", t_port
                )
                w_target = f"[{t_host_bare}]" if ":" in t_host_bare else t_host_bare
                w_port = t_port_norm if t_port_norm != "" else "22"
        else:
            w_target = target_host
            w_port = t_port

        # Build raw command based on protocol
        if jh["protocol"] in ["ssh", "mosh"]:
            cmd = f"ssh -W {w_target}:{w_port}"
            if inner_proxy_opt:
                cmd += f" {inner_proxy_opt}"
            if jh_port != "":
                cmd += f" -p {jh_port}"
            if jh_opts != "":
                cmd += f" {jh_opts}"
            jh_user = str(jh.get("user", "") or "")
            user_host = f"{jh_user}@{jh_host}" if jh_user else jh_host
            cmd += f" {user_host}"
        elif jh["protocol"] == "ssm":
            ssm_target = jh["host"]
            ssm_cmd = f"aws ssm start-session --target {ssm_target} --document-name AWS-StartSSHSession --parameters 'portNumber=22'"
            if isinstance(jh.get("tags"), dict):
                if "profile" in jh["tags"]:
                    ssm_cmd += f" --profile {jh['tags']['profile']}"
                if "region" in jh["tags"]:
                    ssm_cmd += f" --region {jh['tags']['region']}"
            if jh["options"] != '':
                ssm_cmd += f" {jh['options']}"
            bastion_user_part = f"{jh['user']}@{ssm_target}" if jh['user'] else ssm_target
            ssh_opts = ""
            if isinstance(jh.get("tags"), dict) and "ssh_options" in jh["tags"]:
                ssh_opts = f" {jh['tags']['ssh_options']}"
            cmd = f"ssh{ssh_opts} -o ProxyCommand='{ssm_cmd}' -W {target_host}:{t_port} {bastion_user_part}"
        elif jh["protocol"] in ["kubectl", "docker"]:
            nc_cmd = "nc"
            if isinstance(jh.get("tags"), dict) and "nc_command" in jh["tags"]:
                nc_cmd = jh["tags"]["nc_command"]
            if jh["protocol"] == "kubectl":
                cmd = "kubectl exec "
                if jh["options"] != '':
                    cmd += f"{jh['options']} "
                cmd += f"{jh['host']} -i -- {nc_cmd} {target_host} {t_port}"
            else:
                cmd = "docker "
                if jh["options"] != '':
                    cmd += f"{jh['options']} "
                cmd += f"exec -i {jh['host']} {nc_cmd} {target_host} {t_port}"
        else:
            return "", passwords

        return cmd, passwords

    @MethodHook
    def _passtx(self, passwords, *, keyfile=None):
        # decrypts passwords, used by other methdos.
        dpass = []
        if keyfile is None:
            keyfile = self.key
        if keyfile is not None:
            from Crypto.PublicKey import RSA
            from Crypto.Cipher import PKCS1_OAEP
            with open(keyfile) as f:
                key = RSA.import_key(f.read())
            decryptor = PKCS1_OAEP.new(key)
        for passwd in passwords:
            if not re.match('^b[\"\'].+[\"\']$', passwd):
                dpass.append(passwd)
            else:
                try:
                    decrypted = decryptor.decrypt(ast.literal_eval(passwd)).decode("utf-8")
                    dpass.append(decrypted)
                except Exception:
                    printer.error("Decryption failed: Missing or corrupted key.")
                    printer.info("Verify your RSA key and configuration settings.")
                    sys.exit(1)
        return dpass

    

    @MethodHook
    def _logfile(self, logfile = None):
        # translate logs variables and generate logs path.
        if logfile == None:
            logfile = self.logs
        logfile = logfile.replace("${unique}", self.unique)
        logfile = logfile.replace("${host}", self.host)
        logfile = logfile.replace("${port}", self.port)
        logfile = logfile.replace("${user}", self.user)
        logfile = logfile.replace("${protocol}", self.protocol)
        now = datetime.datetime.now()
        dateconf = re.search(r'\$\{date \'(.*)\'}', logfile)
        if dateconf:
            logfile = re.sub(r'\$\{date (.*)}',now.strftime(dateconf.group(1)), logfile)
        return logfile

    @MethodHook
    def _logclean(self, logfile, var = False):
        """Remove special ascii characters and process terminal cursor movements to clean logs."""
        from .utils import log_cleaner
        
        if var == False:
            try:
                with open(logfile, "r") as f:
                    t = f.read()
            except:
                return
        else:
            t = logfile
            
        result = log_cleaner(t)

        if var == False:
            try:
                with open(logfile, "w") as f:
                    f.write(result)
            except:
                pass
            return
        else:
            return result

    @MethodHook
    def _savelog(self):
        '''Save the log buffer to the file at regular intervals if there are changes.'''
        t = threading.current_thread()
        prev_size = 0  # Store the previous size of the buffer

        while getattr(t, "do_run", True):  # Check if thread is signaled to stop
            current_size = self.mylog.tell()  # Current size of the buffer

            # Only save if the buffer size has changed
            if current_size != prev_size:
                with open(self.logfile, "w") as f:  # Use "w" to overwrite the file
                    f.write(self._logclean(self.mylog.getvalue().decode(), True))
                prev_size = current_size  # Update the previous size
            sleep(5)

    @MethodHook
    def _filter(self, a):
        #Set time for last input when using interact
        self.lastinput = time()
        return a

    @MethodHook
    def _keepalive(self):
        #Send keepalive ctrl+e when idletime passed without new inputs on interact
        self.lastinput = time()
        t = threading.current_thread()
        while True:
            if time() - self.lastinput >= self.idletime:
                self.child.sendcontrol("e")
                self.lastinput = time()
            sleep(1)


    def _setup_interact_environment(self, debug=False, logger=None, async_mode=False):
        try:
            size = re.search('columns=([0-9]+).*lines=([0-9]+)',str(os.get_terminal_size()))
            self.child.setwinsize(int(size.group(2)),int(size.group(1)))
        except OSError:
            pass
        if logger and self.protocol != "local":
            port_str = f":{self.port}" if self.port and self.protocol not in ["ssm", "kubectl", "docker"] else ""
            logger("success", f"Connected to {self.unique} at {self.host}{port_str} via: {self.protocol}")

        # Always initialize self.mylog to capture terminal context for the AI Copilot
        if not hasattr(self, 'mylog'):
            self.mylog = io.BytesIO()
            
        if not async_mode:
            self.child.logfile_read = self.mylog
            
        # Only start disk-logging tasks if logfile is configured
        if 'logfile' in dir(self):
            if not async_mode:
                # Start the _savelog thread (sync mode)
                log_thread = threading.Thread(target=self._savelog)
                log_thread.daemon = True
                log_thread.start()
        if 'missingtext' in dir(self):
            print(self.child.after.decode(), end='')
        if self.idletime > 0 and not async_mode:
            x = threading.Thread(target=self._keepalive)
            x.daemon = True
            x.start()
        if debug:
            if 'mylog' in dir(self):
                if not async_mode:
                    print(self.mylog.getvalue().decode())

    def _teardown_interact_environment(self):
        if 'logfile' in dir(self) and hasattr(self, 'mylog'):
            with open(self.logfile, "w") as f:
                f.write(self._logclean(self.mylog.getvalue().decode(), True))

    def _is_child_connpy_active(self, child_fd: int) -> bool:
        if self.protocol != "local":
            return False
        try:
            fg_pgid = os.tcgetpgrp(child_fd)
            if fg_pgid <= 0:
                return False
            cmdline_path = f"/proc/{fg_pgid}/cmdline"
            if os.path.exists(cmdline_path):
                with open(cmdline_path, "rb") as f:
                    raw_args = f.read().split(b"\x00")
                    cmdline_str = " ".join([arg.decode(errors="ignore") for arg in raw_args if arg])
                    
                    is_active = False
                    for raw_arg in raw_args:
                        arg_str = raw_arg.decode(errors="ignore")
                        if not arg_str:
                            continue
                        base_name = os.path.basename(arg_str)
                        if base_name in ["conn", "connpy", "connapp"] or "connpy" in arg_str or "connapp" in arg_str:
                            is_active = True
                            break

                    return is_active
        except Exception:
            pass
        return False

    async def _async_interact_loop(self, local_stream, resize_callback, copilot_handler=None):
        local_stream.setup(resize_callback=resize_callback)
        self.current_local_stream = local_stream
        try:
            child_fd = self.child.child_fd
            
            # 1. Flush ghost buffer (Clean UX)
            ghost_buffer = b''
            if getattr(self, 'missingtext', False):
                # If we are missing the password, we MUST show the password prompt
                ghost_buffer = (self.child.after or b'') + (self.child.buffer or b'')
            else:
                # We auto-logged in. Hide the messy password negotiation and just keep any pending live stream.
                ghost_buffer = self.child.buffer or b''

            # Fix user's pet peeve: Strip leading newlines to avoid the empty lines 
            # the router echoes after receiving the password or blank line.
            if not getattr(self, 'missingtext', False):
                ghost_buffer = ghost_buffer.lstrip(b'\r\n ')

            if ghost_buffer:
                # Add a single clean newline so it doesn't merge with the Connected message
                await local_stream.write(b'\r\n' + ghost_buffer)
                if hasattr(self, 'mylog'):
                    self.mylog.write(b'\n' + ghost_buffer)
                    
            self.child.buffer = b''
            self.child.before = b''
            
            # 2. Set child fd non-blocking
            flags = fcntl.fcntl(child_fd, fcntl.F_GETFL)
            fcntl.fcntl(child_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            
            loop = asyncio.get_running_loop()
            child_reader_queue = asyncio.Queue()
            
            # Reset and track command byte positions for copilot context navigation
            # Each entry is (byte_position, command_text_or_None)
            self.cmd_byte_positions = [(self.mylog.tell() if hasattr(self, 'mylog') else 0, None)]
            
            def _child_read_ready():
                try:
                    # Increase buffer to 64KB for better high-speed handling
                    data = os.read(child_fd, 65536)
                    if data:
                        child_reader_queue.put_nowait(data)
                    else:
                        child_reader_queue.put_nowait(b'')
                except BlockingIOError:
                    pass
                except OSError:
                    child_reader_queue.put_nowait(b'')
                    
            loop.add_reader(child_fd, _child_read_ready)
            self.lastinput = time()
            
            async def ingress_task():
                while True:
                    data = await local_stream.read()
                    if not data:
                        break
                    
                    # Copilot interception
                    if copilot_handler and b'\x00' in data:
                        if self._is_child_connpy_active(child_fd):
                            try:
                                os.write(child_fd, data)
                            except OSError:
                                break
                            self.lastinput = time()
                            continue

                        # Build node info from available metadata and ensure values are strings (not bytes)
                        def to_str(val):
                            if isinstance(val, bytes):
                                return val.decode(errors='replace')
                            return str(val) if val is not None else "unknown"
                            
                        node_info = {
                            "name": to_str(getattr(self, 'unique', 'unknown')), 
                            "host": to_str(getattr(self, 'host', 'unknown'))
                        }
                        if isinstance(getattr(self, 'tags', None), dict):
                            node_info["os"] = to_str(self.tags.get("os", "unknown"))
                            node_info["prompt"] = to_str(self.tags.get("prompt", r'>$|#$|\$$|>.$|#.$|\$.$'))
                        
                        # Invoke copilot (async callback handles UI)
                        await copilot_handler(self.mylog.getvalue(), node_info, local_stream, child_fd, self.cmd_byte_positions)
                        continue
                    
                    # Remove any stray \x00 bytes and forward normally
                    clean_data = data.replace(b'\x00', b'')
                    if clean_data:
                        # Track command boundaries when user hits Enter or presses Ctrl+C
                        if hasattr(self, 'mylog') and (b'\r' in clean_data or b'\n' in clean_data or b'\x03' in clean_data):
                            pos = self.mylog.tell()
                            marker_cmd = "CANCELLED" if b'\x03' in clean_data else None
                            self.cmd_byte_positions.append((pos, marker_cmd))
                            if hasattr(self, 'current_local_stream') and self.current_local_stream is not None:
                                try:
                                    await self.current_local_stream.write(f'\x1b]133;B;{pos}\x07'.encode())
                                except Exception:
                                    pass

                        try:
                            os.write(child_fd, clean_data)
                        except OSError:
                            break
                        self.lastinput = time()
                    
            async def egress_task():
                # Continue stripping newlines from the live stream until we hit real text
                skip_newlines = not getattr(self, 'missingtext', False) and not ghost_buffer
                while True:
                    data = await child_reader_queue.get()
                    if not data:
                        break
                    
                    # Batching Optimization: Drain the queue to batch writes during high-volume bursts
                    # Helps the terminal parse ANSI faster and reduces syscalls.
                    chunks = [data]
                    while not child_reader_queue.empty():
                        try:
                            extra = child_reader_queue.get_nowait()
                            if not extra:
                                chunks.append(b'') # Re-put EOF later or handle it
                                break
                            chunks.append(extra)
                        except asyncio.QueueEmpty:
                            break
                    
                    has_eof = chunks[-1] == b''
                    if has_eof:
                        chunks.pop()
                    
                    if chunks:
                        combined_data = b''.join(chunks)
                        if skip_newlines:
                            stripped = combined_data.lstrip(b'\r\n')
                            if stripped:
                                skip_newlines = False
                                combined_data = stripped
                            else:
                                if has_eof: break
                                continue
                                
                        await local_stream.write(combined_data)
                        if hasattr(self, 'mylog'):
                            self.mylog.write(combined_data)
                    
                    if has_eof:
                        break
                        
            async def keepalive_task():
                while True:
                    await asyncio.sleep(1)
                    if time() - self.lastinput >= self.idletime:
                        try:
                            self.child.sendcontrol("e")
                            self.lastinput = time()
                        except Exception:
                            pass
                            
            async def savelog_task():
                prev_size = 0
                while True:
                    await asyncio.sleep(5)
                    current_size = self.mylog.tell()
                    if current_size != prev_size:
                        try:
                            # Move heavy log cleaning to a thread to avoid freezing the interaction loop
                            raw_log = self.mylog.getvalue().decode(errors='replace')
                            cleaned_log = await asyncio.to_thread(self._logclean, raw_log, True)
                            with open(self.logfile, "w") as f:
                                f.write(cleaned_log)
                            prev_size = current_size
                        except Exception:
                            pass

            async def pwd_tracker_task():
                import socket
                hostname = socket.gethostname()
                last_cwd = None
                while True:
                    await asyncio.sleep(0.3)
                    try:
                        child_pid = getattr(self.child, 'pid', None)
                        if child_pid:
                            new_cwd = os.readlink(f"/proc/{child_pid}/cwd")
                            if new_cwd != last_cwd:
                                last_cwd = new_cwd
                                try:
                                    os.chdir(new_cwd)
                                except Exception:
                                    pass
                                try:
                                    await local_stream.write(f"\033]7;file://{hostname}{new_cwd}\007".encode())
                                except Exception:
                                    pass
                                if isinstance(self.tags, dict):
                                    self.tags["cwd"] = new_cwd
                    except Exception:
                        pass

            try:
                # We wait for either the user (ingress) or the child (egress) to finish
                tasks = [
                    asyncio.create_task(ingress_task()),
                    asyncio.create_task(egress_task())
                ]
                if self.protocol == "local":
                    tasks.append(asyncio.create_task(pwd_tracker_task()))
                if self.idletime > 0:
                    tasks.append(asyncio.create_task(keepalive_task()))
                if hasattr(self, 'logfile') and hasattr(self, 'mylog'):
                    tasks.append(asyncio.create_task(savelog_task()))
                
                done, pending = await asyncio.wait(
                    [tasks[0], tasks[1]], 
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # If ingress finished first (user quit), give egress a small window to catch up 
                # on the remaining output in the queue.
                if tasks[0] in done and tasks[1] not in done:
                    try:
                        await asyncio.wait_for(tasks[1], timeout=0.2)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
                
                for t in tasks:
                    if t not in done:
                        t.cancel()
                    
                # Final log sync on thread to avoid losing last lines
                if hasattr(self, 'logfile') and hasattr(self, 'mylog'):
                    try:
                        raw_log = self.mylog.getvalue().decode(errors='replace')
                        cleaned_log = await asyncio.to_thread(self._logclean, raw_log, True)
                        with open(self.logfile, "w") as f:
                            f.write(cleaned_log)
                    except Exception:
                        pass

            finally:
                loop.remove_reader(child_fd)
                try:
                    flags = fcntl.fcntl(child_fd, fcntl.F_GETFL)
                    fcntl.fcntl(child_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
                except Exception:
                    pass
        finally:
            self.current_local_stream = None
            local_stream.teardown()

    @MethodHook
    async def inject_commands(self, commands, child_fd, on_inject=None):
        """
        Inject a list of commands into the node's PTY.
        Handles screen_length_command, history tracking and delays.
        """
        if not commands:
            return

        # 0. Clear line
        os.write(child_fd, b'\x15')
        await asyncio.sleep(0.1)

        # 1. Prepare list (prepend screen_length if exists)
        slc = self.tags.get("screen_length_command") if hasattr(self, 'tags') and isinstance(self.tags, dict) else None
        
        to_send = list(commands)
        if slc and slc not in to_send: # avoid duplicates if already there
             to_send.insert(0, slc)

        # 2. Inject one by one
        for cmd in to_send:
            # Register in node's official history (SKIP if it's the administrative screen length command)
            if cmd != slc and hasattr(self, 'cmd_byte_positions') and self.cmd_byte_positions is not None:
                log_pos = self.mylog.tell() if hasattr(self, 'mylog') else 0
                self.cmd_byte_positions.append((log_pos, cmd))
                if hasattr(self, 'current_local_stream') and self.current_local_stream is not None:
                    try:
                        await self.current_local_stream.write(f'\x1b]133;B;{log_pos}\x07'.encode())
                    except Exception:
                        pass
            
            # Write physically to PTY
            os.write(child_fd, (cmd + "\n").encode())
            
            # Notify (e.g., for gRPC or logs) - SKIP for administrative SLC
            if on_inject and cmd != slc:
                if asyncio.iscoroutinefunction(on_inject):
                    await on_inject(cmd)
                else:
                    on_inject(cmd)
            
            # Delay to avoid overwhelming the router
            await asyncio.sleep(0.8)

    @MethodHook
    def interact(self, debug=False, logger=None):
        '''
        Asynchronous interactive session using Smart Tunnel architecture.
        Allows multiplexing I/O and handling SIGWINCH events locally without blocking.
        '''
        connect = self._connect(debug=debug, logger=logger)
        if connect == True:
            try:
                self._setup_interact_environment(debug=debug, logger=logger, async_mode=True)
                
                local_stream = LocalStream()
                
                def resize_callback(rows, cols):
                    try:
                        self.child.setwinsize(rows, cols)
                    except Exception:
                        pass
                
                # Build local copilot handler
                copilot_handler = self._build_local_copilot_handler()
                
                asyncio.run(self._async_interact_loop(local_stream, resize_callback, copilot_handler=copilot_handler))
            finally:
                self._teardown_interact_environment()
        else:
            if logger:
                logger("error", str(connect))
            else:
                printer.error(f"Connection failed: {str(connect)}")
            sys.exit(1)

    def _build_local_copilot_handler(self):
        """Build copilot handler for local CLI sessions using rich for rendering."""
        config = getattr(self, 'config', None) if hasattr(self, 'config') else None
        return self._copilot_handler(config)

    def _copilot_handler(self, config):
        """Unified copilot handler for local session."""
        import asyncio
        import os

        async def handler(buffer, node_info, stream, child_fd, cmd_byte_positions=None):
            try:
                from .cli.terminal_ui import CopilotInterface
                from .services.ai_service import AIService

                interface = CopilotInterface(
                    config, 
                    history=getattr(stream, 'copilot_history', None),
                    session_state=getattr(stream, 'copilot_state', None)
                )
                # Save history back to stream for persistence in current session
                stream.copilot_history = interface.history
                stream.copilot_state = interface.session_state
                interface.session_state['banner_shown'] = False
                
                ai_service = AIService(config)
                
                async def on_ai_call(active_buffer, question, chunk_callback, merged_node_info):
                    return await ai_service.aask_copilot(
                        active_buffer,
                        question,
                        node_info=merged_node_info,
                        chunk_callback=chunk_callback
                    )
                # Get raw bytes from BytesIO
                raw_bytes = self.mylog.getvalue()
                
                # Stop terminal reading so prompt_toolkit (in run_session) 
                # has exclusive control of stdin without LocalStream interference.
                if hasattr(stream, 'stop_reading'):
                    stream.stop_reading()
                elif hasattr(stream, '_loop') and hasattr(stream, 'stdin_fd'):
                    # Fallback if the method is missing (in LocalStream)
                    stream._loop.remove_reader(stream.stdin_fd)
                
                try:
                    with copilot_terminal_mode():
                        while True:
                            action, commands, custom_cmd = await interface.run_session(
                                raw_bytes=raw_bytes,
                                cmd_byte_positions=self.cmd_byte_positions,
                                node_info=node_info,
                                on_ai_call=on_ai_call
                            )
                            if action in ("send_all", "custom"):
                                cmds_to_send = commands if action == "send_all" else custom_cmd
                                await self.inject_commands(cmds_to_send, child_fd)
                                
                                # Dynamic Wait for Device Prompt Settle
                                prompt_pattern = node_info.get("prompt", r'>$|#$|\$$|>.$|#.$|\$.$')
                                start_t = time()
                                last_len = 0
                                quiet_count = 0
                                while time() - start_t < 60:
                                    await asyncio.sleep(0.15)
                                    cur_bytes = self.mylog.getvalue()
                                    if len(cur_bytes) == last_len:
                                        quiet_count += 1
                                    else:
                                        quiet_count = 0
                                        last_len = len(cur_bytes)
                                    
                                    clean_txt = self._logclean(cur_bytes.decode(errors='replace'), True).strip()
                                    lines = [l.strip() for l in clean_txt.split('\n') if l.strip()]
                                    last_line = lines[-1] if lines else ""
                                    if quiet_count >= 2 and re.search(prompt_pattern, last_line):
                                        break

                                raw_bytes = self.mylog.getvalue()
                                continue
                            if action == "continue":
                                continue
                            break
                finally:
                    print("\033[2m Returning to session...\033[0m", flush=True)
                    # Restart terminal reading to return to interactive SSH/Telnet mode
                    if hasattr(stream, 'start_reading'):
                        stream.start_reading()
                    elif hasattr(stream, '_loop') and hasattr(stream, 'stdin_fd'):
                        stream._loop.add_reader(stream.stdin_fd, stream._read_ready)
                
                os.write(child_fd, b'\x15\r')
            except Exception as e:
                import traceback
                print(f"\n[ERROR in Copilot Handler] {e}", flush=True)
                traceback.print_exc()
                os.write(child_fd, b'\x15\r')

        return handler

    @MethodHook
    def run(self, commands, vars = None,*, folder = '', prompt = r'>$|#$|\$$|>.$|#.$|\$.$', stdout = False, timeout = 10, logger = None):
        '''
        Run a command or list of commands on the node and return the output.


        ### Parameters:  

            - commands (str/list): Commands to run on the node. Should be 
                                   str or a list of str. You can use variables
                                   as {varname} and defining them in optional
                                   parameter vars.

        ### Optional Parameters:  

            - vars  (dict): Dictionary containing the definition of variables
                            used in commands parameter.
                            Keys: Variable names.
                            Values: strings.

        ### Optional Named Parameters:  

            - folder (str): Path where output log should be stored, leave 
                            empty to disable logging.  

            - prompt (str): Prompt to be expected after a command is finished 
                            running. Usually linux uses  ">" or EOF while 
                            routers use ">" or "#". The default value should 
                            work for most nodes. Change it if your connection 
                            need some special symbol.  

            - stdout (bool):Set True to send the command output to stdout. 
                            default False.

            - timeout (int):Time in seconds for expect to wait for prompt/EOF.
                            default 10.

        ### Returns:  

            str: Output of the commands you ran on the node.

        '''
        force_ssh = (self.protocol == "mosh")
        connect = self._connect(timeout=timeout, logger=logger, force_ssh=force_ssh)
        now = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
        if connect == True:
            if logger:
                effective_protocol = "ssh" if force_ssh else self.protocol
                port_str = f":{self.port}" if self.port and effective_protocol not in ["ssm", "kubectl", "docker"] else ""
                logger("success", f"Connected to {self.unique} at {self.host}{port_str} via: {effective_protocol}")

            if "prompt" in self.tags:
                prompt = self.tags["prompt"]
            expects = [prompt, pexpect.EOF, pexpect.TIMEOUT]
            output = ''
            status = ''
            if not isinstance(commands, list):
                commands = [commands]
            if "screen_length_command" in self.tags:
                commands.insert(0, self.tags["screen_length_command"])
            self.mylog = io.BytesIO()
            self.child.logfile_read = self.mylog
            for c in commands:
                if vars is not None:
                    try:
                        c = c.format(**vars)
                    except KeyError as e:
                        self.output = f"Error: Variable {e} not defined in task or inventory"
                        self.status = 1
                        return self.output
                result = self.child.expect(expects, timeout = timeout)
                # Only set terminal size on devices without a
                # screen_length_command (e.g. Linux/bash servers).
                # Routers already disable pagination via that command.
                # After setwinsize, consume any SIGWINCH re-render
                # prompt (~40ms on bash) with a short timeout.
                if c == commands[0] and "screen_length_command" not in self.tags:
                    try:
                        self.child.setwinsize(65535, 65535)
                    except Exception:
                        try:
                            self.child.setwinsize(10000, 10000)
                        except Exception:
                            pass
                    self.child.expect(expects, timeout = 1)
                self.child.sendline(c)
                if result == 2:
                    break
            if not result == 2:
                result = self.child.expect(expects, timeout = timeout)
            self.child.close()
            output = self._logclean(self.mylog.getvalue().decode(), True)
            if logger:
                logger("output", output)
            if folder != '':
                with open(folder + "/" + self.unique + "_" + now + ".txt", "w") as f:
                    f.write(output)
                    f.close()
            self.output = output
            if result == 2:
                self.status = 2
            else:
                self.status = 0
            return output
        else:
            self.output = connect
            self.status = 1
            if logger:
                logger("error", f"Connection failed: {connect}")
            if folder != '':
                with open(folder + "/" + self.unique + "_" + now + ".txt", "w") as f:
                    f.write(connect)

                    f.close()
            return connect

    @MethodHook
    def test(self, commands, expected, vars = None,*, folder = '', prompt = r'>$|#$|\$$|>.$|#.$|\$.$', timeout = 10, logger = None):
        '''
        Run a command or list of commands on the node, then check if expected value appears on the output after the last command.


        ### Parameters:  

            - commands (str/list): Commands to run on the node. Should be
                                   str or a list of str. You can use variables
                                   as {varname} and defining them in optional
                                   parameter vars.

            - expected (str)     : Expected text to appear after running 
                                   all the commands on the node.You can use
                                   variables as {varname} and defining them
                                   in optional parameter vars.

        ### Optional Parameters:  

            - vars  (dict): Dictionary containing the definition of variables
                            used in commands and expected parameters.
                            Keys: Variable names.
                            Values: strings.

        ### Optional Named Parameters: 

            - folder (str): Path where output log should be stored, leave 
                            empty to not store logs.

            - prompt (str): Prompt to be expected after a command is finished
                            running. Usually linux uses  ">" or EOF while 
                            routers use ">" or "#". The default value should 
                            work for most nodes. Change it if your connection 
                            need some special symbol.

            - timeout (int):Time in seconds for expect to wait for prompt/EOF.
                            default 10.

        ### Returns: 
            bool: true if expected value is found after running the commands 
                  false if prompt is found before.

        '''
        now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        force_ssh = (self.protocol == "mosh")
        connect = self._connect(timeout=timeout, logger=logger, force_ssh=force_ssh)
        if connect == True:
            if logger:
                effective_protocol = "ssh" if force_ssh else self.protocol
                port_str = f":{self.port}" if self.port and effective_protocol not in ["ssm", "kubectl", "docker"] else ""
                logger("success", f"Connected to {self.unique} at {self.host}{port_str} via: {effective_protocol}")

            if "prompt" in self.tags:
                prompt = self.tags["prompt"]
            expects = [prompt, pexpect.EOF, pexpect.TIMEOUT]
            output = ''
            if not isinstance(commands, list):
                commands = [commands]
            if not isinstance(expected, list):
                expected = [expected]
            if "screen_length_command" in self.tags:
                commands.insert(0, self.tags["screen_length_command"])
            self.mylog = io.BytesIO()
            self.child.logfile_read = self.mylog
            for c in commands:
                if vars is not None:
                    try:
                        c = c.format(**vars)
                    except KeyError as e:
                        self.output = f"Error: Variable {e} not defined in task or inventory"
                        self.status = 1
                        return self.output
                result = self.child.expect(expects, timeout = timeout)
                if c == commands[0] and "screen_length_command" not in self.tags:
                    try:
                        self.child.setwinsize(65535, 65535)
                    except Exception:
                        try:
                            self.child.setwinsize(10000, 10000)
                        except Exception:
                            pass
                    self.child.expect(expects, timeout = 1)
                self.child.sendline(c)
                if result == 2:
                    break
            if not result == 2:
                result = self.child.expect(expects, timeout = timeout)
            self.child.close()
            output = self._logclean(self.mylog.getvalue().decode(), True)
            if logger:
                logger("output", output)
            if folder != '':
                with open(folder + "/" + self.unique + "_" + now + ".txt", "w") as f:
                    f.write(output)
                    f.close()
            self.output = output
            if result in [0, 1]:
                # lastcommand = commands[-1]
                # if vars is not None:
                    # lastcommand = lastcommand.format(**vars)
                # last_command_index = output.rfind(lastcommand)
                # cleaned_output = output[last_command_index + len(lastcommand):].strip()
                self.result = {}
                for e in expected:
                    if vars is not None:
                        e = e.format(**vars)
                    updatedprompt = re.sub(r'(?<!\\)\$', '', prompt)
                    cleaned_output = output
                    try:
                        newpattern = f".*({updatedprompt}).*{e}.*"
                        cleaned_output = re.sub(newpattern, '', cleaned_output)
                    except re.error:
                        try:
                            escaped_e = re.escape(e)
                            newpattern = f".*({updatedprompt}).*{escaped_e}.*"
                            cleaned_output = re.sub(newpattern, '', cleaned_output)
                        except re.error:
                            pass

                    if e in cleaned_output:
                        self.result[e] = True
                    else:
                        try:
                            if re.search(e, cleaned_output):
                                self.result[e] = True
                            else:
                                self.result[e] = False
                        except re.error:
                            self.result[e] = False
                self.status = 0
                return self.result
            if result == 2:
                self.result = None
                self.status = 2
                return output
        else:
            self.result = None
            self.output = connect
            self.status = 1
            return connect

    @MethodHook
    def _generate_ssh_sftp_cmd(self, force_ssh=False, allow_reverse_tunnel_port=None):
        is_from_mosh = (
            self.protocol == "mosh"
            or getattr(self, "_orig_protocol", "") == "mosh"
        )
        proto = "ssh" if force_ssh else self.protocol
        cmd = proto
        if is_from_mosh:
            host_bare, port_s, raw_opts = self._validate_mosh_ssh_fields(
                self.host,
                self.user,
                self.port,
                self.tags,
                allow_reverse_tunnel_port=allow_reverse_tunnel_port,
            )
            if port_s:
                cmd += (" -p " if proto == "ssh" else " -P ") + port_s
            if raw_opts and proto == "sftp":
                sftp_tokens = []
                toks = shlex.split(raw_opts)
                i = 0
                while i < len(toks):
                    t = toks[i]
                    if t in ("-X", "-x", "-Y", "-t", "-A", "-a"):
                        i += 1
                    elif t == "-m":
                        i += 2
                    elif t.startswith("-m") and len(t) > 2:
                        i += 1
                    else:
                        sftp_tokens.append(t)
                        i += 1
                opts = shlex.join(sftp_tokens)
            else:
                opts = str(raw_opts).strip() if raw_opts else ""
            if opts:
                cmd += " " + opts
            target_h = f"[{host_bare}]" if (proto == "sftp" and ":" in host_bare) else host_bare
        else:
            if self.port != "" and self.port is not None:
                if proto == "ssh":
                    cmd += " -p " + str(self.port)
                elif proto == "sftp":
                    cmd += " -P " + str(self.port)
            raw_opts = self.options
            if raw_opts:
                opts = str(raw_opts).strip()
                if proto == "sftp":
                    opts = re.sub(r'(?<!\S)-[XxtTAaNf]\b', '', opts).strip()
                if opts:
                    cmd += " " + opts
            target_h = self.host

        if self.jumphost:
            cmd += " " + self.jumphost
        user_host = f"{self.user}@{target_h}" if self.user else target_h
        cmd += f" {user_host}"
        return cmd

    @MethodHook
    def _generate_mosh_cmd(self):
        if str(self.host).startswith("-"):
            raise ValueError("Host cannot start with '-'")
        if self.user and str(self.user).startswith("-"):
            raise ValueError("User cannot start with '-'")
        if not self.host or re.search(r'[\s;|&$`><"\'\\]', str(self.host)):
            raise ValueError(f"Invalid characters in host: '{self.host}'")
        if self.user and re.search(r'[\s;|&$`><"\'\\]', str(self.user)):
            raise ValueError(f"Invalid characters in user: '{self.user}'")

        cmd_parts = ["mosh"]
        if isinstance(self.tags, dict):
            if self.tags.get("mosh_server"):
                server_bin = str(self.tags["mosh_server"]).strip()
                if server_bin:
                    cmd_parts.append(f"--server={shlex.quote(server_bin)}")
            if self.tags.get("mosh_udp_port"):
                udp_port = str(self.tags["mosh_udp_port"]).strip()
                if udp_port:
                    if not re.match(r"^\d+(:\d+)?$", udp_port):
                        raise ValueError(
                            f"Invalid mosh_udp_port '{udp_port}': must be PORT or PORT1:PORT2"
                        )
                    cmd_parts.append(f"-p {shlex.quote(udp_port)}")

        ssh_parts = ["ssh"]
        if self.port != "" and self.port is not None:
            port_s = str(self.port).strip()
            if port_s:
                if not re.fullmatch(r"^\d+$", port_s) or not (1 <= int(port_s) <= 65535):
                    raise ValueError(f"Invalid TCP SSH port '{self.port}'")
                ssh_parts.append(f"-p {port_s}")
        if isinstance(self.tags, dict) and self.tags.get("mosh_ssh_options"):
            ssh_opts = str(self.tags["mosh_ssh_options"]).strip()
            if ssh_opts:
                if re.search(r'(?:LocalCommand|ProxyCommand)', ssh_opts, re.IGNORECASE):
                    raise ValueError("mosh_ssh_options cannot contain LocalCommand or ProxyCommand")
                try:
                    shlex.split(ssh_opts)
                except ValueError as exc:
                    raise ValueError("Invalid mosh_ssh_options: unbalanced quotes") from exc
                ssh_parts.append(ssh_opts)
        if self.jumphost:
            ssh_parts.append(self.jumphost)

        if len(ssh_parts) > 1:
            raw_ssh = " ".join(ssh_parts)
            cmd_parts.append(f"--ssh={shlex.quote(raw_ssh)}")

        if self.options:
            opts = str(self.options).strip()
            if opts:
                if re.search(r'--(?:ssh|server|client)(?:=|\s|$)', opts):
                    raise ValueError("Options cannot override --ssh, --server or --client for mosh")
                if re.search(r'(?:LocalCommand|ProxyCommand)', opts, re.IGNORECASE):
                    raise ValueError("Options cannot contain LocalCommand or ProxyCommand")
                try:
                    shlex.split(opts)
                except ValueError as exc:
                    raise ValueError("Invalid options: unbalanced quotes") from exc
                cmd_parts.append(opts)

        user_host = f"{self.user}@{self.host}" if self.user else self.host
        cmd_parts.extend(["--", user_host])
        return " ".join(cmd_parts)

    @MethodHook
    def _generate_telnet_cmd(self):
        cmd = f"telnet {self.host}"
        if self.port:
            cmd += f" {self.port}"
        if self.options:
            cmd += f" {self.options}"
        return cmd

    @MethodHook
    def _generate_kube_cmd(self):
        cmd = f"kubectl exec {self.options} {self.host} -it --"
        kube_command = self.tags.get("kube_command", "/bin/bash") if isinstance(self.tags, dict) else "/bin/bash"
        cmd += f" {kube_command}"
        return cmd

    @MethodHook
    def _generate_docker_cmd(self):
        cmd = f"docker {self.options} exec -it {self.host}"
        docker_command = self.tags.get("docker_command", "/bin/bash") if isinstance(self.tags, dict) else "/bin/bash"
        cmd += f" {docker_command}"
        return cmd

    @MethodHook
    def _generate_ssm_cmd(self):
        region = self.tags.get("region", "") if isinstance(self.tags, dict) else ""
        profile = self.tags.get("profile", "") if isinstance(self.tags, dict) else ""
        cmd = f"aws ssm start-session --target {self.host}"
        if region:
            cmd += f" --region {region}"
        if profile:
            cmd += f" --profile {profile}"
        if self.options:
            cmd += f" {self.options}"
        return cmd


    @MethodHook
    def _get_cmd(self, force_ssh=False, allow_reverse_tunnel_port=None):
        if force_ssh or self.protocol in ["ssh", "sftp"]:
            return self._generate_ssh_sftp_cmd(
                force_ssh=force_ssh,
                allow_reverse_tunnel_port=allow_reverse_tunnel_port,
            )
        elif self.protocol == "mosh":
            return self._generate_mosh_cmd()
        elif self.protocol == "telnet":
            return self._generate_telnet_cmd()
        elif self.protocol == "kubectl":
            return self._generate_kube_cmd()
        elif self.protocol == "docker":
            return self._generate_docker_cmd()
        elif self.protocol == "ssm":
            return self._generate_ssm_cmd()
        elif self.protocol == "local":
            return self.host
        else:
            printer.error(f"Invalid protocol: {self.protocol}")
            sys.exit(1)

    def _spawn_child(self, args, protocol=None):
        if protocol is None:
            protocol = getattr(self, "protocol", "")
        env = None
        if protocol == "mosh":
            env = os.environ.copy()
            if not any(
                "utf-8" in env.get(k, "").lower() or "utf8" in env.get(k, "").lower()
                for k in ("LC_ALL", "LC_CTYPE", "LANG")
            ):
                env["LC_ALL"] = "C.UTF-8"
            env.setdefault("TERM", "xterm-256color")
        try:
            if env is not None:
                return pexpect.spawn(args[0], args[1:], env=env), None
            return pexpect.spawn(args[0], args[1:]), None
        except pexpect.exceptions.ExceptionPexpect as e:
            return None, f"Connection failed: {e}"

    @MethodHook
    def _connect(
        self,
        debug=False,
        timeout=10,
        max_attempts=3,
        logger=None,
        force_ssh=False,
        allow_reverse_tunnel_port=None,
    ):
        effective_protocol = "ssh" if force_ssh else self.protocol
        if effective_protocol == "mosh":
            import shutil
            if shutil.which("mosh") is None:
                return "Connection failed: mosh binary not found in PATH"
            timeout = max(timeout, 15)

        if effective_protocol == "local":
            cmd = self._get_cmd()
            args = shlex.split(cmd)
            child, err = self._spawn_child(args, protocol=effective_protocol)
            if err:
                return err
            self.child = child
            from pexpect import fdpexpect
            self.raw_child = fdpexpect.fdspawn(self.child.child_fd)
            if self.logs != '':
                self.logfile = self._logfile()
            return True

        cmd = self._get_cmd(
            force_ssh=force_ssh,
            allow_reverse_tunnel_port=allow_reverse_tunnel_port,
        )
        passwords = self._passtx(self.password) if self.password and any(self.password) else []
        if self.logs != '':
            self.logfile = self._logfile()
        default_prompt = r'>$|#$|\$$|>.$|#.$|\$.$'
        prompt = self.tags.get("prompt", default_prompt) if isinstance(self.tags, dict) else default_prompt
        password_prompt = '[p|P]assword:|[u|U]sername:' if effective_protocol != 'telnet' else '[p|P]assword:'
        prompt_unanchored = "|".join([t[:-1] if t.endswith("$") else t for t in prompt.split("|")])
        mosh_prompt = r"(?:" + prompt_unanchored + r")\s*(?:\[[0-9;?]*[a-zA-Z])*$"

        expects = {
            "ssh": ['yes/no', 'refused', 'supported', 'Invalid|[u|U]sage: ssh', 'ssh-keygen.*\"', 'timeout|timed.out', 'unavailable', 'closed', password_prompt, prompt, 'suspend', pexpect.EOF, pexpect.TIMEOUT, "No route to host", "resolve hostname", "no matching", "[b|B]ad (owner|permissions)"],
            "sftp": ['yes/no', 'refused', 'supported', 'Invalid|[u|U]sage: sftp', 'ssh-keygen.*\"', 'timeout|timed.out', 'unavailable', 'closed', password_prompt, prompt, 'suspend', pexpect.EOF, pexpect.TIMEOUT, "No route to host", "resolve hostname", "no matching", "[b|B]ad (owner|permissions)"],
            "telnet": ['[u|U]sername:', 'refused', 'supported', 'invalid|unrecognized option', 'ssh-keygen.*\"', 'timeout|timed.out', 'unavailable', 'closed', password_prompt, prompt, 'suspend', pexpect.EOF, pexpect.TIMEOUT, "No route to host", "resolve hostname", "no matching", "[b|B]ad (owner|permissions)"],
            "kubectl": ['[u|U]sername:', '[r|R]efused', '[E|e]rror', 'DEPRECATED', pexpect.TIMEOUT, password_prompt, prompt, pexpect.EOF, "expired|invalid"],
            "docker": ['[u|U]sername:', 'Cannot', '[E|e]rror', 'failed', 'not a docker command', 'unknown', 'unable to resolve', pexpect.TIMEOUT, password_prompt, prompt, pexpect.EOF],
            "ssm": ['[u|U]sername:', 'Cannot', '[E|e]rror', 'failed', 'SessionManagerPlugin', '[u|U]nknown', 'unable to resolve', pexpect.TIMEOUT, password_prompt, prompt, pexpect.EOF]
        }
        expects["mosh"] = [
            'yes/no',                                                                              # 0  (initial)
            r'(?:ssh: connect to host .* port .*: Connection refused|Connection refused)',         # 1  (error)
            r'(?:ssh|mosh): .*supported',                                                          # 2  (error)
            r'(?:(?:ssh|mosh): (?:Invalid|usage)|Unknown option:)',                                # 3  (error)
            'ssh-keygen.*\"',                                                                      # 4  (error)
            r'(?:ssh: connect to host .* port .*: Connection timed out|mosh: .* timed out)',       # 5  (error)
            r'(?:ssh|mosh): .*unavailable',                                                        # 6  (error)
            r'(?:Connection to .* closed|Connection closed by remote host)',                       # 7  (error)
            password_prompt,                                                                       # 8  (eof)
            mosh_prompt,                                                                           # 9  (eof)
            'suspend',                                                                             # 10 (eof)
            pexpect.EOF,                                                                           # 11 (eof)
            pexpect.TIMEOUT,                                                                       # 12 (error)
            "No route to host",                                                                    # 13 (error)
            "resolve hostname",                                                                    # 14 (error)
            r'(?:ssh|mosh): .*no matching',                                                        # 15 (error)
            "[b|B]ad (owner|permissions)",                                                         # 16 (error)
            "mosh-server:.*not found|command not found",                                           # 17 (error)
            r"(?:Did not find mosh server startup message|(?:mosh: )?Nothing received from server on UDP port \d+\.?|Nothing received from server)", # 18 (error)
            "needs a UTF-8 native locale to run|requires a UTF-8 locale|locale.*not available",    # 19 (error)
        ]

        error_indices = {
            "ssh": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16],
            "sftp": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16],
            "telnet": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16],
            "kubectl": [1, 2, 3, 4, 8],  # Define error indices for kube
            "docker": [1, 2, 3, 4, 5, 6, 7],  # Define error indices for docker
            "ssm": [1, 2, 3, 4, 5, 6, 7],
            "mosh": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16, 17, 18, 19]
        }

        eof_indices = {
            "ssh": [8, 9, 10, 11],
            "sftp": [8, 9, 10, 11],
            "telnet": [8, 9, 10, 11],
            "kubectl": [5, 6, 7],  # Define eof indices for kube
            "docker": [8, 9, 10],  # Define eof indices for docker
            "ssm": [8, 9, 10],
            "mosh": [8, 9, 10, 11]
        }

        initial_indices = {
            "ssh": [0],
            "sftp": [0],
            "telnet": [0],
            "kubectl": [0],  # Define special indices for kube
            "docker": [0],  # Define special indices for docker
            "ssm": [0],
            "mosh": [0]
        }

        attempts = 1
        handshake_progressed = False
        while attempts <= max_attempts:
            args = shlex.split(cmd)
            child, err = self._spawn_child(args, protocol=effective_protocol)
            if err:
                return err
            if isinstance(self.tags, dict) and self.tags.get("console"):
                child.sendline()
            if debug:
                if logger:
                    logger("debug", f"Command:\n{cmd}")
                self.mylog = io.BytesIO()
                self.mylog.write(f"[i] [DEBUG] Command:\r\n    {cmd}\r\n".encode())
                child.logfile_read = self.mylog


            endloop = False
            for i in range(len(passwords) if passwords else 1):
                while True:
                    results = child.expect(expects[effective_protocol], timeout=timeout)
                    results_value = expects[effective_protocol][results]
                    
                    if results in initial_indices[effective_protocol]:
                        if effective_protocol in ["ssh", "sftp", "mosh"]:
                            child.sendline('yes')
                            handshake_progressed = True
                        elif effective_protocol in ["telnet", "kubectl", "docker", "ssm"]:
                            if self.user:
                                child.sendline(self.user)
                            else:
                                self.missingtext = True
                                break
                    
                    elif results in error_indices[effective_protocol]:
                        try:
                            child.terminate()
                        except Exception:
                            pass
                        can_retry = (
                            results_value == pexpect.TIMEOUT
                            and attempts != max_attempts
                            and not (effective_protocol == "mosh" and handshake_progressed)
                        )
                        if can_retry:
                            attempts += 1
                            endloop = True
                            break
                        else:
                            def _to_str(val):
                                if val is None:
                                    return ""
                                if hasattr(val, "decode"):
                                    return val.decode()
                                return str(val)
                            after = "Connection timeout" if results_value == pexpect.TIMEOUT else _to_str(child.after)
                            before = _to_str(child.before)
                            readline_val = _to_str(child.readline())
                            if effective_protocol == "mosh":
                                after = self._logclean(after, True)
                                before = self._logclean(before, True)
                                readline_val = self._logclean(readline_val, True)
                            if effective_protocol == "mosh" and results == 18 and ("Nothing received from server" in after or "Nothing received from server" in before):
                                err_msg = after.strip() if "Nothing received from server" in after else before.strip()
                                return f"Connection failed code: {results}\n{err_msg} — UDP traffic is blocked or unreachable. Verify firewall rules for UDP ports (60000-61000 or mosh_udp_port)."
                            return f"Connection failed code: {results}\n{before.lstrip()}{after}{readline_val}".rstrip()
                    
                    elif results in eof_indices[effective_protocol]:
                        if results_value == password_prompt:
                            if passwords:
                                child.sendline(passwords[i])
                                handshake_progressed = True
                            else:
                                self.missingtext = True
                            break
                        elif results_value == "suspend":
                            child.sendline("\r")
                            sleep(2)
                        else:
                            endloop = True
                            child.sendline()
                            break
                    
                if endloop:
                    break
            if results_value == pexpect.TIMEOUT:
                continue
            else:
                break

        if isinstance(self.tags, dict) and self.tags.get("post_connect_commands"):
            cmds = self.tags.get("post_connect_commands")
            commands = [cmds] if isinstance(cmds, str) else cmds
            for command in commands:
                child.sendline(command)
                sleep(1)
        child.readline(0)
        self.child = child
        from pexpect import fdpexpect
        self.raw_child = fdpexpect.fdspawn(self.child.child_fd)
        return True

@ClassHook
class nodes:
    ''' This class generates a nodes object. Contains a list of node class objects and methods to run multiple tasks on nodes simultaneously.

    ### Attributes:  

        - nodelist (list): List of node class objects passed to the init 
                           function.  

        - output   (dict): Dictionary formed by nodes unique as keys, 
                           output of the commands you ran on the node as 
                           value. Created after running methods run or test.  

        - result   (dict): Dictionary formed by nodes unique as keys, value 
                           is True if expected value is found after running 
                           the commands, False if prompt is found before. 
                           Created after running method test.  

        - status   (dict): Dictionary formed by nodes unique as keys, value: 
                           0 if method run or test ended successfully.
                           1 if connection failed.
                           2 if expect timeouts without prompt or EOF.

        - <unique> (obj):  For each item in nodelist, there is an attribute
                           generated with the node unique.
        '''

    def __init__(self, nodes: dict, config = ''):
        ''' 
        ### Parameters:  

            - nodes (dict): Dictionary formed by node information:  
                            Keys: Unique name for each node.  
                            Mandatory Subkeys: host(str).  
                            Optional Subkeys: options(str), logs(str), password(str),
                            port(str), protocol(str), user(str).  
                            For reference on subkeys check node class.

        ### Optional Parameters:  

            - config (obj): Pass the object created with class configfile with key 
                            for decryption and extra configuration if you are using 
                            connection manager.
        '''
        self.nodelist = []
        self.config = config
        for n in nodes:
            this = node(n, **nodes[n], config = config)
            self.nodelist.append(this)
            setattr(self,n,this)

    
    @MethodHook
    def _splitlist(self, lst, n):
        #split a list in lists of n members.
        for i in range(0, len(lst), n):
            yield lst[i:i + n]


    @MethodHook
    def run(self, commands, vars = None,*, folder = None, prompt = None, stdout = None, parallel = 10, timeout = None, on_complete = None, logger = None):
        '''
        Run a command or list of commands on all the nodes in nodelist.


        ### Parameters:  

            - commands (str/list): Commands to run on the nodes. Should be str or 
                                   list of str. You can use variables as {varname}
                                   and defining them in optional parameter vars.

        ### Optional Parameters:  

            - vars  (dict): Dictionary containing the definition of variables for
                            each node, used in commands parameter.
                            Keys should be formed by nodes unique names. Use
                            special key name __global__ for global variables.
                            Subkeys: Variable names.
                            Values: strings.

        ### Optional Named Parameters:  

            - folder   (str): Path where output log should be stored, leave empty 
                              to disable logging.  

            - prompt   (str): Prompt to be expected after a command is finished 
                              running. Usually linux uses  ">" or EOF while routers 
                              use ">" or "#". The default value should work for 
                              most nodes. Change it if your connection need some 
                              special symbol.  

            - stdout  (bool): Set True to send the command output to stdout. 
                              Default False.  

            - parallel (int): Number of nodes to run the commands simultaneously. 
                              Default is 10, if there are more nodes that this 
                              value, nodes are groups in groups with max this 
                              number of members.
            
            - timeout  (int): Time in seconds for expect to wait for prompt/EOF.
                              default 10.

            - on_complete (callable): Optional callback called when each node 
                                      finishes. Receives (unique, output, status).
                                      Called from the node's thread so it must
                                      be thread-safe.

        ###Returns:  

            dict: Dictionary formed by nodes unique as keys, Output of the 
                  commands you ran on the node as value.

        '''
        args = {}
        nodesargs = {}
        args["commands"] = commands
        if folder != None:
            args["folder"] = folder
            Path(folder).mkdir(parents=True, exist_ok=True)
        if prompt != None:
            args["prompt"] = prompt
        if stdout != None and on_complete is None:
            args["stdout"] = stdout
        if timeout != None:
            args["timeout"] = timeout
        output = {}
        status = {}
        tasks = []

        def _run_node(node_obj, node_args, callback):
            """Wrapper that runs a node and fires the callback on completion."""
            node_obj.run(**node_args)
            if callback:
                callback(node_obj.unique, node_obj.output, node_obj.status)

        for n in self.nodelist:
            nodesargs[n.unique] = deepcopy(args)
            if vars != None:
                nodesargs[n.unique]["vars"] = {}
                if "__global__" in vars.keys():
                    nodesargs[n.unique]["vars"].update(vars["__global__"])
                for var_key, var_val in vars.items():
                    if var_key == "__global__":
                        continue
                    try:
                        if re.search(var_key, n.unique, re.IGNORECASE):
                            nodesargs[n.unique]["vars"].update(var_val)
                    except re.error:
                        if var_key == n.unique:
                            nodesargs[n.unique]["vars"].update(var_val)
            
            # Pass the logger to the node
            nodesargs[n.unique]["logger"] = logger

            if on_complete:
                tasks.append(threading.Thread(target=_run_node, args=(n, nodesargs[n.unique], on_complete)))
            else:
                tasks.append(threading.Thread(target=n.run, kwargs=nodesargs[n.unique]))

        taskslist = list(self._splitlist(tasks, parallel))

        for t in taskslist:
            for i in t:
                i.start()
            for i in t:
                i.join()
        for i in self.nodelist:
            output[i.unique] = i.output
            status[i.unique] = i.status
        self.output = output
        self.status = status
        return output

    @MethodHook
    def test(self, commands, expected, vars = None,*, folder = None, prompt = None, parallel = 10, timeout = None, on_complete = None, logger = None):
        '''
        Run a command or list of commands on all the nodes in nodelist, then check if expected value appears on the output after the last command.


        ### Parameters:  

            - commands (str/list): Commands to run on the node. Should be str or 
                                   list of str.  

            - expected (str)     : Expected text to appear after running all the 
                                   commands on the node.

        ### Optional Parameters:  

            - vars  (dict): Dictionary containing the definition of variables for
                            each node, used in commands and expected parameters.
                            Keys should be formed by nodes unique names. Use
                            special key name __global__ for global variables.
                            Subkeys: Variable names.
                            Values: strings.

        ### Optional Named Parameters:  

            - prompt   (str): Prompt to be expected after a command is finished 
                              running. Usually linux uses  ">" or EOF while 
                              routers use ">" or "#". The default value should 
                              work for most nodes. Change it if your connection 
                              need some special symbol.


            - parallel (int): Number of nodes to run the commands simultaneously. 
                              Default is 10, if there are more nodes that this 
                              value, nodes are groups in groups with max this 
                              number of members.

            - timeout  (int): Time in seconds for expect to wait for prompt/EOF.
                              default 10.

            - on_complete (callable): Optional callback called when each node 
                                      finishes. Receives (unique, output, status).
                                      Called from the node's thread so it must
                                      be thread-safe.

        ### Returns:  

            dict: Dictionary formed by nodes unique as keys, value is True if 
                  expected value is found after running the commands, False 
                  if prompt is found before.

        '''
        args = {}
        nodesargs = {}
        args["commands"] = commands
        args["expected"] = expected
        if folder != None:
            args["folder"] = folder
            Path(folder).mkdir(parents=True, exist_ok=True)
        if prompt != None:
            args["prompt"] = prompt
        if timeout != None:
            args["timeout"] = timeout
        output = {}
        result = {}
        status = {}
        tasks = []

        def _test_node(node_obj, node_args, callback):
            """Wrapper that runs a node test and fires the callback on completion."""
            node_obj.test(**node_args)
            if callback:
                callback(node_obj.unique, node_obj.output, node_obj.status, node_obj.result)

        for n in self.nodelist:
            nodesargs[n.unique] = deepcopy(args)
            if vars != None:
                nodesargs[n.unique]["vars"] = {}
                if "__global__" in vars.keys():
                    nodesargs[n.unique]["vars"].update(vars["__global__"])
                for var_key, var_val in vars.items():
                    if var_key == "__global__":
                        continue
                    try:
                        if re.search(var_key, n.unique, re.IGNORECASE):
                            nodesargs[n.unique]["vars"].update(var_val)
                    except re.error:
                        if var_key == n.unique:
                            nodesargs[n.unique]["vars"].update(var_val)
            nodesargs[n.unique]["logger"] = logger
            
            if on_complete:
                tasks.append(threading.Thread(target=_test_node, args=(n, nodesargs[n.unique], on_complete)))
            else:
                tasks.append(threading.Thread(target=n.test, kwargs=nodesargs[n.unique]))

        taskslist = list(self._splitlist(tasks, parallel))
        for t in taskslist:
            for i in t:
                i.start()
            for i in t:
                i.join()
        for i in self.nodelist:
            result[i.unique] = i.result
            output[i.unique] = i.output
            status[i.unique] = i.status
        self.output = output
        self.result = result
        self.status = status
        return result

# script
