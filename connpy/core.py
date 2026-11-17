#!/usr/bin/env python3
import ast
import asyncio
import datetime
import fcntl
import io
import ipaddress
import logging
import os
import re
import shlex
import shutil
import sys
import threading
import time as _time_mod
from contextlib import contextmanager, suppress
from copy import deepcopy
from pathlib import Path
from time import sleep, time

import pexpect

from . import printer
from .auth_utils import (
    DEFAULT_MAX_AUTH_PROMPTS,
    DEFAULT_MAX_HOP_HOST_ATTEMPTS,
    DEFAULT_MAX_TOTAL_AUTH_ATTEMPTS,
    MAX_AUTH_PROMPTS_CAP,
    MAX_AUTH_STREAM_BYTES,
    MAX_COMPOSE_VARIANTS,
    MAX_EOF_FALLBACK_ADVANCES,
    MAX_HOP_HOST_ATTEMPTS_CAP,
    MAX_PROFILE_DEPTH,
    MAX_TOTAL_AUTH_ATTEMPTS_CAP,
    MIN_AUTH_PROMPTS,
    MIN_HOP_HOST_ATTEMPTS,
    MIN_TOTAL_AUTH_ATTEMPTS,
    AuthAttemptTracker,
    AuthCandidate,
    RuntimeAuthCandidate,
    SanitizedAuthError,
    has_insecure_hostkey_checking,
    host_exact_re,
    is_compose_expression,
    normalize_prompt_line,
    parse_compose_expression,
    resolve_fallback_entries,
    sanitize_exception,
    sanitize_log_message,
    validate_strict_int_range,
)
from .hooks import ClassHook, MethodHook
from .tunnels import LocalStream

_LOGGER = logging.getLogger(__name__)

DEFAULT_DIRECT_CONNECT_TIMEOUT = 10
DEFAULT_MOSH_CONNECT_TIMEOUT = 15
DEFAULT_ADAPTIVE_BASE_TIMEOUT = 15
DEFAULT_SSM_DIRECT_COST = 20
DEFAULT_SSM_HOP_COST = 25
DEFAULT_OTHER_HOP_COST = 10
DEFAULT_AUTH_TIMEOUT = 25
DEFAULT_MAX_PROGRESS_EXTENSIONS = 3
MAX_STAGE_TIMEOUT_CAP = 600
DEFAULT_MAX_TOTAL_CONNECT_SECONDS = 180
MAX_PRE_AUTH_BUFFER_BYTES = 65536

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_SSM_TARGET_RE = re.compile(
    r"\A(?:(?:i|mi)-[0-9a-f]{4,17}|arn:aws[a-z-]*:ssm:[a-z0-9-]+:\d{12}:[A-Za-z0-9._/-]{1,256})\Z",
    re.ASCII,
)
_SSM_TAG_RE = re.compile(r"\A[A-Za-z0-9._-]{1,128}\Z", re.ASCII)
_SSM_UNSAFE_OPTIONS_RE = re.compile(r'[;|&$`><"\'\\\x00-\x1f\x7f]')


def validate_strict_timeout_int(
    value,
    name: str,
    min_val: int = 1,
    max_val: int = MAX_STAGE_TIMEOUT_CAP,
) -> int:
    """Strictly validate that value is a plain int within [min_val, max_val]."""
    if type(value) is not int or value < min_val or value > max_val:
        raise ValueError(
            f"Invalid {name}: expected integer in [{min_val}, {max_val}], got {value!r}"
        )
    return value


def resolve_timeouts(
    protocol,
    tags,
    hop_chain_meta,
    timeout=None,
    connect_timeout=None,
    auth_timeout=None,
    *,
    _default_cmd_timeout=10,
    _default_max_total_seconds=None,
) -> tuple[int, int, int, int, int]:
    """Resolve effective connect, auth, command, extension, and total deadline budgets."""
    default_cmd = validate_strict_timeout_int(_default_cmd_timeout, "_default_cmd_timeout")
    tags_dict = tags if isinstance(tags, dict) else {}

    if timeout is not None:
        validate_strict_timeout_int(timeout, "timeout")
    if connect_timeout is not None:
        validate_strict_timeout_int(connect_timeout, "connect_timeout")
    if auth_timeout is not None:
        validate_strict_timeout_int(auth_timeout, "auth_timeout")

    tag_ct = tags_dict.get("connect_timeout")
    tag_at = tags_dict.get("auth_timeout")
    tag_cmd = tags_dict.get("command_timeout")
    if tag_ct is not None:
        validate_strict_timeout_int(tag_ct, "tags.connect_timeout")
    if tag_at is not None:
        validate_strict_timeout_int(tag_at, "tags.auth_timeout")
    if tag_cmd is not None:
        validate_strict_timeout_int(tag_cmd, "tags.command_timeout")

    hops = hop_chain_meta or []
    if hops:
        adaptive_ct = DEFAULT_ADAPTIVE_BASE_TIMEOUT
        for hop in hops:
            hop_proto = hop.get("protocol", "ssh") if isinstance(hop, dict) else str(hop)
            if hop_proto == "ssm":
                adaptive_ct += DEFAULT_SSM_HOP_COST
            else:
                adaptive_ct += DEFAULT_OTHER_HOP_COST
        if protocol == "mosh":
            adaptive_ct = max(adaptive_ct, DEFAULT_MOSH_CONNECT_TIMEOUT)
    elif protocol == "ssm":
        adaptive_ct = DEFAULT_SSM_DIRECT_COST
    elif protocol == "mosh":
        adaptive_ct = DEFAULT_MOSH_CONNECT_TIMEOUT
    else:
        adaptive_ct = DEFAULT_DIRECT_CONNECT_TIMEOUT

    adaptive_ct = min(adaptive_ct, MAX_STAGE_TIMEOUT_CAP)

    if timeout is not None:
        eff_cmd = timeout
    elif tag_cmd is not None:
        eff_cmd = tag_cmd
    else:
        eff_cmd = default_cmd

    if auth_timeout is not None:
        eff_at = auth_timeout
    elif timeout is not None:
        eff_at = timeout
    elif tag_at is not None:
        eff_at = tag_at
    else:
        eff_at = DEFAULT_AUTH_TIMEOUT

    if connect_timeout is not None:
        eff_ct = connect_timeout
    elif timeout is not None:
        eff_ct = max(timeout, DEFAULT_MOSH_CONNECT_TIMEOUT) if protocol == "mosh" else timeout
    elif tag_ct is not None:
        eff_ct = max(tag_ct, DEFAULT_MOSH_CONNECT_TIMEOUT) if protocol == "mosh" else tag_ct
    else:
        eff_ct = adaptive_ct

    tag_max_ext = tags_dict.get("max_progress_extensions")
    if tag_max_ext is not None:
        max_ext = validate_strict_timeout_int(
            tag_max_ext,
            "tags.max_progress_extensions",
            min_val=0,
            max_val=10,
        )
    else:
        max_ext = DEFAULT_MAX_PROGRESS_EXTENSIONS

    max_total = DEFAULT_MAX_TOTAL_CONNECT_SECONDS
    if _default_max_total_seconds is not None:
        ceiling = validate_strict_timeout_int(
            _default_max_total_seconds,
            "_default_max_total_seconds",
            min_val=1,
            max_val=MAX_STAGE_TIMEOUT_CAP,
        )
        max_total = min(max_total, ceiling)

    return eff_ct, eff_at, eff_cmd, max_ext, max_total


def compile_expects(expects_map):
    """Compile ASCII expect regex strings into bytes patterns with re.DOTALL."""
    def _compile_list(items):
        compiled = []
        for pat in items:
            if isinstance(pat, str):
                compiled.append(re.compile(pat.encode("ascii"), re.DOTALL))
            elif isinstance(pat, bytes):
                compiled.append(re.compile(pat, re.DOTALL))
            elif isinstance(pat, re.Pattern):
                if isinstance(pat.pattern, str):
                    compiled.append(
                        re.compile(
                            pat.pattern.encode("ascii"),
                            (pat.flags & ~re.UNICODE) | re.DOTALL,
                        )
                    )
                else:
                    compiled.append(pat)
            else:
                compiled.append(pat)
        return compiled

    if isinstance(expects_map, dict):
        return {proto: _compile_list(pats) for proto, pats in expects_map.items()}
    return _compile_list(expects_map)


def _extract_ssh_o_keys(tokens: list[str]) -> set[str]:
    """Return lowercased -o directive keys present in tokenized SSH options."""
    keys: set[str] = set()
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok == "-o" and idx + 1 < len(tokens):
            spec = tokens[idx + 1]
            if "=" in spec:
                keys.add(spec.split("=", 1)[0].strip().lower())
            idx += 2
        elif tok.startswith("-o") and len(tok) > 2:
            spec = tok.removeprefix("-o")
            if "=" in spec:
                keys.add(spec.split("=", 1)[0].strip().lower())
            idx += 1
        else:
            idx += 1
    return keys


def _redact_known_secrets(text: str, secrets: list[str]) -> str:
    """Replace any non-trivial known secret strings with '***'."""
    if not text or not secrets:
        return text
    out = text
    unique_secrets = sorted(
        {s for s in secrets if isinstance(s, str) and len(s) >= 4},
        key=len,
        reverse=True,
    )
    for sec in unique_secrets:
        out = out.replace(sec, "***")
    return out

@contextmanager
def copilot_terminal_mode():
    import termios
    import tty

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
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Failed to restore terminal attributes: %s", exc)

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
    
    def __init__(
        self,
        unique,
        host,
        options='',
        logs='',
        password='',
        port='',
        protocol='',
        user='',
        config='',
        tags='',
        jumphost='',
        allow_unsafe_options=None,
        fallback=None,
        disable_fallback_on_insecure_hostkey=None,
    ):
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

            - allow_unsafe_options (bool): Explicitly override safety restrictions.
        '''
        self.config = config
        if config == '':
            self.idletime = 0
            self.key = None
        else:
            self.idletime = config.config["idletime"] if (hasattr(config, "config") and isinstance(config.config, dict) and "idletime" in config.config) else 0
            self.key = getattr(config, "key", None)
        if allow_unsafe_options is not None:
            self.allow_unsafe_options = bool(allow_unsafe_options)
        elif config != '' and config is not None and hasattr(config, "config") and isinstance(config.config, dict):
            self.allow_unsafe_options = bool(config.config.get("allow_unsafe_options", False))
        else:
            self.allow_unsafe_options = False
        self.unique = unique
        profiles_map = getattr(config, "profiles", {}) if (config != '' and config is not None) else {}
        attr = {"host": host, "logs": logs, "options":options, "port": port, "protocol": protocol, "user": user, "tags": tags, "jumphost": jumphost}
        for key, attr_val in attr.items():
            profile = re.search("^@(.*)", str(attr_val))
            if profile and config != '':
                try:
                    setattr(self, key, deepcopy(profiles_map[profile.group(1)][key]))
                except KeyError:
                    setattr(self, key, "")
            elif attr_val == '' and key == "protocol":
                try:
                    setattr(self, key, profiles_map["default"][key])
                except (KeyError, AttributeError):
                    setattr(self, key, "ssh")
            else:
                setattr(self, key, attr_val)
        self._orig_protocol = self.protocol
        if isinstance(password, list):
            self.password = []
            for item in password:
                if (
                    isinstance(item, str)
                    and not is_compose_expression(item)
                    and item.startswith("@")
                    and config != ''
                    and item[1:] in profiles_map
                ):
                    self.password.append(deepcopy(profiles_map[item[1:]].get("password", "")))
                else:
                    self.password.append(item)
        else:
            if (
                isinstance(password, str)
                and not is_compose_expression(password)
                and password.startswith("@")
                and config != ''
                and password[1:] in profiles_map
            ):
                resolved_pw = profiles_map[password[1:]].get("password", "")
                self.password = deepcopy(resolved_pw) if isinstance(resolved_pw, list) else [resolved_pw]
            else:
                self.password = [password]

        target_passwords_snapshot = list(self.password)

        raw_fallback = fallback
        if raw_fallback in (None, "", []) and isinstance(self.tags, dict):
            if "_fallback" in self.tags:
                raw_fallback = self.tags.pop("_fallback", None)
            elif "fallback" in self.tags:
                raw_fallback = self.tags.get("fallback")

        if raw_fallback not in (None, "", []):
            resolved_fb_entries = resolve_fallback_entries(raw_fallback, profiles_map)
        else:
            resolved_fb_entries = []

        candidates: list[AuthCandidate] = []
        primary_specs = tuple(
            p for p in target_passwords_snapshot if isinstance(p, str) and p != ""
        )
        if primary_specs:
            candidates.append(
                AuthCandidate(
                    user=str(self.user or ""),
                    password_spec=primary_specs if len(primary_specs) > 1 else primary_specs[0],
                    is_fallback=False,
                    label="primary",
                )
            )
        elif self.user:
            candidates.append(
                AuthCandidate(
                    user=str(self.user or ""),
                    password_spec="",
                    is_fallback=False,
                    label="primary",
                )
            )

        for fb_idx, entry in enumerate(resolved_fb_entries, start=1):
            fb_user = str(entry.get("user") or self.user or "")
            fb_pw = entry.get("password", "")
            if isinstance(fb_pw, list):
                pw_spec: str | tuple[str, ...] = tuple(str(x) for x in fb_pw if x != "")
            else:
                pw_spec = str(fb_pw or "")
            candidates.append(
                AuthCandidate(
                    user=fb_user,
                    password_spec=pw_spec,
                    is_fallback=True,
                    label=f"fallback#{fb_idx}",
                )
            )
        self._auth_candidates: tuple[AuthCandidate, ...] = tuple(candidates)

        if disable_fallback_on_insecure_hostkey is not None:
            self.disable_fallback_on_insecure_hostkey = bool(disable_fallback_on_insecure_hostkey)
        elif isinstance(self.tags, dict) and "disable_fallback_on_insecure_hostkey" in self.tags:
            self.disable_fallback_on_insecure_hostkey = bool(
                self.tags["disable_fallback_on_insecure_hostkey"]
            )
        elif (
            config != ''
            and config is not None
            and hasattr(config, "config")
            and isinstance(config.config, dict)
        ):
            self.disable_fallback_on_insecure_hostkey = bool(
                config.config.get("disable_fallback_on_insecure_hostkey", False)
            )
        else:
            self.disable_fallback_on_insecure_hostkey = False

        self._jumphost_name = self.jumphost if (self.jumphost != "" and config != '') else ""
        self._hop_chain_meta = []
        self._jumphost_hops: tuple[dict, ...] = ()
        if self.jumphost != "" and config != '':
            chain_res = self._build_jumphost_chain(self.jumphost, config, include_meta=True)
            if len(chain_res) == 3:
                raw_cmd, jh_passwords, self._hop_chain_meta = chain_res
            else:
                raw_cmd, jh_passwords = chain_res
                self._hop_chain_meta = []
            self._jumphost_hops = tuple(self._collect_jumphost_hops(self._jumphost_name, config))
            if jh_passwords:
                self.password = jh_passwords + self.password
            if raw_cmd:
                escaped = raw_cmd.replace('\\', '\\\\').replace('"', '\\"')
                self.jumphost = f'-o ProxyCommand="{escaped}"'
            else:
                self.jumphost = ""

        self.last_fallback_used = False
        self.output = ""
        self.status = 1
        self.result = {}
        self.cmd_byte_positions = [(0, None)]

    def _collect_jumphost_hops(self, jumphost_name, config, visited=None, depth=0):
        """Collect structured hop metadata in SSH ProxyCommand prompt order."""
        if depth >= 5:
            return []
        if visited is None:
            visited = []
        if jumphost_name in visited:
            return []
        visited = [*visited, jumphost_name]
        try:
            jh = config.getitem(jumphost_name)
        except Exception:  # noqa: BLE001
            return []
        jh = self._resolve_jumphost_data(deepcopy(jh), config)
        hops = []
        if jh.get("jumphost", "") != "":
            hops.extend(
                self._collect_jumphost_hops(
                    jh["jumphost"],
                    config,
                    visited,
                    depth + 1,
                )
            )
        jh_tags = jh.get("tags")
        resolved_fqdn = ""
        if isinstance(jh_tags, dict) and jh_tags.get("fqdn"):
            resolved_fqdn = str(jh_tags["fqdn"])
        hops.append(
            {
                "name": jumphost_name,
                "host": str(jh.get("host", "") or ""),
                "port": str(jh.get("port", "") or ""),
                "protocol": str(jh.get("protocol", "ssh") or "ssh"),
                "user": str(jh.get("user", "") or ""),
                "options": str(jh.get("options", "") or ""),
                "tags": deepcopy(jh_tags) if isinstance(jh_tags, dict) else jh_tags,
                "resolved_fqdn": resolved_fqdn,
                "passwords": tuple(p for p in (jh.get("password") or []) if p != ""),
            }
        )
        return hops

    @staticmethod
    def _resolve_jumphost_data(jh_dict, config):
        '''Resolve @profile references and normalize passwords in a jumphost dict.'''
        profiles_map = getattr(config, "profiles", {}) if config else {}
        for key, jh_val in list(jh_dict.items()):
            if key == "password":
                continue
            profile = re.search("^@(.*)", str(jh_val))
            if profile:
                try:
                    jh_dict[key] = deepcopy(profiles_map[profile.group(1)][key])
                except KeyError:
                    jh_dict[key] = ""
            elif jh_val == '' and key == "protocol":
                try:
                    jh_dict[key] = profiles_map["default"][key]
                except KeyError:
                    jh_dict[key] = "ssh"
        raw_pw = jh_dict.get("password", "")
        if isinstance(raw_pw, list):
            resolved = []
            for p in raw_pw:
                if (
                    isinstance(p, str)
                    and not is_compose_expression(p)
                    and p.startswith("@")
                    and p[1:] in profiles_map
                ):
                    resolved.append(deepcopy(profiles_map[p[1:]].get("password", "")))
                else:
                    resolved.append(p)
            jh_dict["password"] = resolved
        else:
            if (
                isinstance(raw_pw, str)
                and not is_compose_expression(raw_pw)
                and raw_pw.startswith("@")
                and raw_pw[1:] in profiles_map
            ):
                res = profiles_map[raw_pw[1:]].get("password", "")
                jh_dict["password"] = deepcopy(res) if isinstance(res, list) else [res]
            else:
                jh_dict["password"] = [raw_pw]
        return jh_dict

    def _is_unsafe_allowed(self) -> bool:
        if getattr(self, "allow_unsafe_options", False):
            return True
        if hasattr(self, "config") and self.config and hasattr(self.config, "config") and isinstance(self.config.config, dict):
            return bool(self.config.config.get("allow_unsafe_options", False))
        return False

    def _validate_base_fields(self, check_port=True):
        """Sanitize host, user, and port against argument injection and shell metacharacters."""
        host_s = str(self.host) if self.host is not None else ""
        if not host_s:
            raise ValueError("Host cannot be empty")
        if host_s.startswith("-"):
            raise ValueError(f"Host cannot start with '-': '{self.host}'")
        host_bare = (
            host_s[1:-1]
            if (host_s.startswith("[") and host_s.endswith("]"))
            else host_s
        )
        if not host_bare or host_bare.startswith("-"):
            raise ValueError(f"Invalid characters in host: '{self.host}'")
        if _LEGACY_SSH_UNSAFE_RE.search(host_bare):
            raise ValueError(f"Invalid characters in host: '{self.host}'")

        user_s = str(self.user) if self.user is not None else ""
        if user_s:
            if user_s.startswith("-"):
                raise ValueError(f"User cannot start with '-': '{self.user}'")
            if _LEGACY_SSH_UNSAFE_RE.search(user_s):
                raise ValueError(f"Invalid characters in user: '{self.user}'")

        if check_port and self.port != "" and self.port is not None:
            if isinstance(self.port, (bool, float)):
                raise ValueError(f"Invalid TCP SSH port '{self.port}'")
            port_s = str(self.port).strip()
            if port_s and (
                not port_s.isascii()
                or not re.fullmatch(r"[0-9]+", port_s, re.ASCII)
                or not (1 <= int(port_s) <= 65535)
            ):
                raise ValueError(f"Invalid TCP SSH port '{self.port}'")

    def _validate_options_and_safety(self):
        """Validate options string for unbalanced quotes and dangerous control characters."""
        if not self.options:
            return
        opts_str = str(self.options).strip()
        if not opts_str:
            return
        if not self._is_unsafe_allowed():
            if any(c in opts_str for c in ("\n", "\r", "\x00")):
                raise ValueError("Options cannot contain newlines or null bytes")
            try:
                shlex.split(opts_str)
            except ValueError as e:
                raise ValueError(f"Invalid options: unbalanced quotes ({e})") from e

    def _validate_ssh_options(self):
        """Sanitize SSH options against local command execution directives."""
        self._validate_options_and_safety()
        if not self.options:
            return
        opts_str = str(self.options).strip()
        if not opts_str:
            return
        if not self._is_unsafe_allowed():
            dangerous_directives = ("ProxyCommand", "LocalCommand", "PermitLocalCommand", "KnownHostsCommand")
            pattern = r'(?:' + '|'.join(dangerous_directives) + r')'
            match = re.search(pattern, opts_str, re.IGNORECASE)
            if match:
                raise ValueError(f"Disallowed SSH directive in options: '{match.group(0)}'")

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

    @staticmethod
    def _validate_and_prepare_ssh_options(
        raw_opts,
        *,
        field_name: str = "options",
        strip_non_tunnel_flags: bool = False,
        allow_unsafe: bool = False,
        connect_timeout: int | None = None,
    ) -> str:
        """Validate SSH options against _ALLOWED_SSH_O_KEYS and optionally inject ConnectTimeout/ServerAliveInterval."""
        out_tokens: list[str] = []
        if raw_opts:
            ssh_opts = str(raw_opts).strip()
            if _CONTROL_CHAR_RE.search(ssh_opts):
                raise ValueError(f"Disallowed control characters in {field_name}")
            if ssh_opts:
                try:
                    tokens = shlex.split(ssh_opts)
                except ValueError as exc:
                    raise ValueError(f"Invalid {field_name}: unbalanced quotes") from exc
                if allow_unsafe:
                    out_tokens = list(tokens)
                else:
                    idx = 0
                    while idx < len(tokens):
                        tok = tokens[idx]
                        if tok in _ALLOWED_SSH_NOARG_FLAGS:
                            if not (strip_non_tunnel_flags and tok in _NON_TUNNEL_SSH_FLAGS):
                                out_tokens.append(tok)
                            idx += 1
                        elif tok in _ALLOWED_SSH_ARG_FLAGS:
                            if idx + 1 >= len(tokens) or not tokens[idx + 1].strip():
                                raise ValueError(
                                    f"Missing argument for ssh option '{tok}' in {field_name}"
                                )
                            arg_val = tokens[idx + 1]
                            val_re = _SAFE_SSH_PATH_RE if tok == "-i" else _SAFE_SSH_O_VALUE_RE
                            if arg_val.startswith("-") or not val_re.fullmatch(arg_val):
                                raise ValueError(
                                    f"Invalid argument for ssh option '{tok}' in {field_name}"
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
                                    f"Invalid argument for ssh option '{flag_prefix}' in {field_name}"
                                )
                            out_tokens.append(tok)
                            idx += 1
                        elif tok == "-o" or tok.startswith("-o"):
                            if tok == "-o":
                                if idx + 1 >= len(tokens) or not tokens[idx + 1].strip():
                                    raise ValueError(
                                        f"Missing argument for ssh option '-o' in {field_name}"
                                    )
                                opt_spec = tokens[idx + 1]
                                step = 2
                            else:
                                opt_spec = tok[2:]
                                step = 1
                            if re.search(
                                r"(?:localcommand|proxycommand|permitlocalcommand|knownhostscommand)",
                                opt_spec,
                                re.IGNORECASE,
                            ):
                                raise ValueError(
                                    f"{field_name} cannot contain LocalCommand or ProxyCommand"
                                )
                            m = re.fullmatch(r"([A-Za-z0-9]+)=(.+)", opt_spec, re.ASCII)
                            if (
                                not m
                                or not m.group(2).strip()
                                or not _SAFE_SSH_O_VALUE_RE.fullmatch(m.group(2).strip())
                            ):
                                raise ValueError(
                                    f"Disallowed -o directive in {field_name}: '{opt_spec}'"
                                )
                            key_raw = m.group(1)
                            key_lower = key_raw.lower()
                            val_str = m.group(2).strip()
                            if key_lower not in _ALLOWED_SSH_O_KEYS:
                                raise ValueError(
                                    f"Disallowed -o directive in {field_name}: '{opt_spec}'"
                                )
                            if not (strip_non_tunnel_flags and key_lower == "forwardagent"):
                                if tok == "-o":
                                    out_tokens.extend(["-o", f"{key_raw}={val_str}"])
                                else:
                                    out_tokens.append(f"-o{key_raw}={val_str}")
                            idx += step
                        else:
                            if re.search(
                                r"(?:localcommand|proxycommand|permitlocalcommand|knownhostscommand)",
                                tok,
                                re.IGNORECASE,
                            ):
                                raise ValueError(
                                    f"{field_name} cannot contain LocalCommand or ProxyCommand"
                                )
                            raise ValueError(
                                f"Disallowed ssh option in {field_name}: '{tok}'"
                            )

        if connect_timeout is not None:
            ct_val = validate_strict_timeout_int(connect_timeout, "connect_timeout")
            existing_keys = _extract_ssh_o_keys(out_tokens)
            if "connecttimeout" not in existing_keys and "connecttimeout" in _ALLOWED_SSH_O_KEYS:
                out_tokens.extend(["-o", f"ConnectTimeout={ct_val}"])
            if (
                "serveraliveinterval" not in existing_keys
                and "serveraliveinterval" in _ALLOWED_SSH_O_KEYS
            ):
                out_tokens.extend(["-o", "ServerAliveInterval=15"])

        return shlex.join(out_tokens) if out_tokens else ""

    def _build_jumphost_chain(
        self,
        jumphost_name,
        config,
        visited=None,
        depth=0,
        target_host="%h",
        target_port="%p",
        *,
        include_meta=False,
        connect_timeout=None,
    ):
        """Recursively build ProxyCommand for chained jumphosts.

        Returns:
            tuple: (raw_proxy_command, passwords_list) when include_meta=False, or
                   (raw_proxy_command, passwords_list, hop_meta) when include_meta=True.

        Raises:
            ValueError: On circular references, invalid hop fields, or exceeding max depth (5).
        """
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

        # Unconditional control-character guard on all hop metadata fields
        for fld_name in ("host", "user", "port"):
            fld_val = jh.get(fld_name)
            if fld_val not in (None, "") and _CONTROL_CHAR_RE.search(str(fld_val)):
                raise ValueError(f"Control characters disallowed in jumphost {fld_name}")
        jh_tags = jh.get("tags")
        if isinstance(jh_tags, dict):
            for tag_fld in ("profile", "region"):
                tag_val = jh_tags.get(tag_fld)
                if tag_val not in (None, "") and _CONTROL_CHAR_RE.search(str(tag_val)):
                    raise ValueError(f"Control characters disallowed in jumphost {tag_fld}")

        if connect_timeout is not None:
            connect_timeout = validate_strict_timeout_int(connect_timeout, "connect_timeout")

        jh_proto = jh.get("protocol", "ssh")
        if jh_proto == "mosh":
            jh_host, jh_port, jh_opts = self._validate_mosh_ssh_fields(
                jh.get("host", ""),
                jh.get("user", ""),
                jh.get("port", ""),
                jh.get("tags", ""),
                strip_non_tunnel_flags=True,
            )
            if connect_timeout is not None:
                jh_opts = self._validate_and_prepare_ssh_options(
                    jh_opts,
                    field_name="mosh_ssh_options",
                    strip_non_tunnel_flags=True,
                    connect_timeout=connect_timeout,
                )
        elif jh_proto == "ssh":
            jh_host, jh_port = self._validate_legacy_ssh_hop_fields(
                jh.get("host", ""),
                jh.get("user", ""),
                jh.get("port", ""),
            )
            jh_opts = self._validate_and_prepare_ssh_options(
                jh.get("options", ""),
                field_name="jumphost options",
                allow_unsafe=self._is_unsafe_allowed(),
                connect_timeout=connect_timeout,
            )
        else:
            jh_host = str(jh.get("host", "") or "")
            jh_port = str(jh["port"]).strip() if jh.get("port") not in ("", None) else ""
            jh_opts = jh.get("options", "")

        passwords = []
        inner_proxy_opt = ""
        inner_meta: list[dict[str, str]] = []

        # Recursively resolve inner jumphost
        if jh.get("jumphost", "") != "":
            if jh_proto not in ["ssh", "mosh"]:
                raise ValueError(
                    f"Jumphost '{jumphost_name}' uses protocol '{jh_proto}' "
                    "which does not support chained jumphosts. "
                    "Only SSH or Mosh jumphosts can have their own jumphosts."
                )
            parent_port = jh_port if jh_port != "" else "22"
            inner_res = self._build_jumphost_chain(
                jh["jumphost"],
                config,
                visited,
                depth + 1,
                target_host=jh_host,
                target_port=parent_port,
                include_meta=True,
                connect_timeout=connect_timeout,
            )
            if len(inner_res) == 3:
                inner_raw_cmd, inner_passwords, inner_meta = inner_res
            else:
                inner_raw_cmd, inner_passwords = inner_res
                inner_meta = []
            passwords = inner_passwords
            escaped = inner_raw_cmd.replace("\\", "\\\\").replace('"', '\\"')
            inner_proxy_opt = f'-o ProxyCommand="{escaped}"'

        # Collect this hop's passwords
        if jh["password"] != [""]:
            passwords = passwords + jh["password"]

        hop_meta = [*inner_meta, {"name": jumphost_name, "protocol": jh_proto}]

        t_port = target_port if target_port != "" else "22"
        if jh_proto in ["ssh", "mosh", "ssm"]:
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
        if jh_proto in ["ssh", "mosh"]:
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
        elif jh_proto == "ssm":
            ssm_target = str(jh.get("host", "") or "")
            if (
                ".." in ssm_target
                or ssm_target.startswith("-")
                or not _SSM_TARGET_RE.fullmatch(ssm_target)
            ):
                raise ValueError(f"Invalid SSM jumphost target: '{ssm_target}'")

            ssm_user = str(jh.get("user", "") or "")
            if ssm_user and (ssm_user.startswith("-") or _LEGACY_SSH_UNSAFE_RE.search(ssm_user)):
                raise ValueError(f"Invalid characters in SSM jumphost user: '{ssm_user}'")

            ssm_parts = [
                "aws",
                "ssm",
                "start-session",
                "--target",
                ssm_target,
                "--document-name",
                "AWS-StartSSHSession",
                "--parameters",
                "portNumber=22",
            ]
            if isinstance(jh_tags, dict):
                for tag_key in ("profile", "region"):
                    raw_val = jh_tags.get(tag_key)
                    if raw_val not in (None, ""):
                        tag_str = str(raw_val)
                        if (
                            ".." in tag_str
                            or tag_str.startswith("-")
                            or not _SSM_TAG_RE.fullmatch(tag_str)
                        ):
                            raise ValueError(f"Invalid SSM jumphost {tag_key}: '{tag_str}'")
                        ssm_parts.extend([f"--{tag_key}", tag_str])

            ssm_opts_raw = str(jh.get("options", "") or "").strip()
            if ssm_opts_raw:
                if _CONTROL_CHAR_RE.search(ssm_opts_raw) or (
                    not self._is_unsafe_allowed() and _SSM_UNSAFE_OPTIONS_RE.search(ssm_opts_raw)
                ):
                    raise ValueError("Invalid characters in SSM jumphost options")
                try:
                    ssm_opt_tokens = shlex.split(ssm_opts_raw)
                except ValueError as exc:
                    raise ValueError("Invalid SSM jumphost options: unbalanced quotes") from exc
                ssm_parts.extend(ssm_opt_tokens)

            ssm_cmd = shlex.join(ssm_parts)
            raw_ssh_opts = jh_tags.get("ssh_options", "") if isinstance(jh_tags, dict) else ""
            ssh_opts = self._validate_and_prepare_ssh_options(
                raw_ssh_opts,
                field_name="ssm ssh_options",
                strip_non_tunnel_flags=True,
                allow_unsafe=self._is_unsafe_allowed(),
                connect_timeout=connect_timeout,
            )
            bastion_user_part = f"{ssm_user}@{ssm_target}" if ssm_user else ssm_target
            outer_parts = ["ssh"]
            if ssh_opts:
                outer_parts.extend(shlex.split(ssh_opts))
            outer_parts.extend(
                ["-o", f"ProxyCommand={ssm_cmd}", "-W", f"{w_target}:{w_port}", bastion_user_part]
            )
            cmd = shlex.join(outer_parts)
        elif jh_proto in ["kubectl", "docker"]:
            if not jh_host or jh_host.startswith("-") or _LEGACY_SSH_UNSAFE_RE.search(jh_host):
                raise ValueError(f"Invalid characters in {jh_proto} jumphost host: '{jh_host}'")
            nc_cmd = "nc"
            if isinstance(jh_tags, dict) and "nc_command" in jh_tags:
                nc_cmd = str(jh_tags["nc_command"])
                if _CONTROL_CHAR_RE.search(nc_cmd) or (
                    not self._is_unsafe_allowed() and _SSM_UNSAFE_OPTIONS_RE.search(nc_cmd)
                ):
                    raise ValueError(f"Invalid nc_command in {jh_proto} jumphost")
            jh_opt_joined = ""
            jh_opt_str = str(jh.get("options", "") or "").strip()
            if jh_opt_str:
                if _CONTROL_CHAR_RE.search(jh_opt_str):
                    raise ValueError(f"Control characters disallowed in {jh_proto} options")
                if not self._is_unsafe_allowed() and _SSM_UNSAFE_OPTIONS_RE.search(jh_opt_str):
                    raise ValueError(f"Invalid characters in {jh_proto} options")
                try:
                    jh_opt_tokens = shlex.split(jh_opt_str)
                except ValueError as exc:
                    raise ValueError(f"Invalid {jh_proto} options: unbalanced quotes") from exc
                jh_opt_joined = shlex.join(jh_opt_tokens)
            if jh_proto == "kubectl":
                cmd = "kubectl exec "
                if jh_opt_joined:
                    cmd += f"{jh_opt_joined} "
                cmd += f"{jh_host} -i -- {nc_cmd} {w_target} {w_port}"
            else:
                cmd = "docker "
                if jh_opt_joined:
                    cmd += f"{jh_opt_joined} "
                cmd += f"exec -i {jh_host} {nc_cmd} {w_target} {w_port}"
        else:
            if include_meta:
                return "", passwords, inner_meta
            return "", passwords

        if include_meta:
            return cmd, passwords, hop_meta
        return cmd, passwords

    @MethodHook
    def _passtx(self, passwords, *, keyfile=None):
        # decrypts passwords, used by other methdos.
        dpass = []
        if keyfile is None:
            keyfile = self.key
        if keyfile is not None:
            from Crypto.Cipher import PKCS1_OAEP
            from Crypto.PublicKey import RSA
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
                except Exception:  # noqa: BLE001
                    printer.error("Decryption failed: Missing or corrupted key.")
                    printer.info("Verify your RSA key and configuration settings.")
                    sys.exit(1)
        return dpass

    

    @MethodHook
    def _logfile(self, logfile = None):
        # translate logs variables and generate logs path.
        if logfile is None:
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
        
        if not var:
            try:
                with open(logfile) as f:
                    t = f.read()
            except Exception:  # noqa: BLE001
                return
        else:
            t = logfile
            
        result = log_cleaner(t)

        if not var:
            try:
                with open(logfile, "w") as f:
                    f.write(result)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Failed writing cleaned log %s: %s", logfile, exc)
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
        if hasattr(self, "logfile") and not async_mode:
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
        if debug and hasattr(self, "mylog") and not async_mode:
            print(self.mylog.getvalue().decode())

    def _teardown_interact_environment(self):
        if 'logfile' in dir(self) and hasattr(self, 'mylog'):
            with open(self.logfile, "w") as f:
                f.write(self._logclean(self.mylog.getvalue().decode(), True))

    def _write_clean_logfile(self, raw_log: str) -> None:
        """Clean and synchronously write raw log output to self.logfile."""
        cleaned_log = self._logclean(raw_log, True)
        with open(self.logfile, "w") as f:
            f.write(cleaned_log)

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
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Failed checking active connpy process: %s", exc)
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
                                except Exception as exc:  # noqa: BLE001
                                    _LOGGER.debug("Failed writing OSC 133 marker: %s", exc)

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
                                if has_eof:
                                    break
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
                        except Exception as exc:  # noqa: BLE001
                            _LOGGER.debug("Keepalive sendcontrol failed: %s", exc)
                            
            async def savelog_task():
                prev_size = 0
                while True:
                    await asyncio.sleep(5)
                    current_size = self.mylog.tell()
                    if current_size != prev_size:
                        try:
                            # Move heavy log cleaning & file I/O to a thread to avoid freezing the interaction loop
                            raw_log = self.mylog.getvalue().decode(errors='replace')
                            await asyncio.to_thread(self._write_clean_logfile, raw_log)
                            prev_size = current_size
                        except Exception as exc:  # noqa: BLE001
                            _LOGGER.debug("Periodic savelog sync failed: %s", exc)

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
                                except Exception as exc:  # noqa: BLE001
                                    _LOGGER.debug("Failed syncing cwd: %s", exc)
                                try:
                                    await local_stream.write(f"\033]7;file://{hostname}{new_cwd}\007".encode())
                                except Exception as exc:  # noqa: BLE001
                                    _LOGGER.debug("Failed writing OSC 7 update: %s", exc)
                                if isinstance(self.tags, dict):
                                    self.tags["cwd"] = new_cwd
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.debug("pwd_tracker_task iteration failed: %s", exc)

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
                
                done, _pending = await asyncio.wait(
                    [tasks[0], tasks[1]], 
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # If ingress finished first (user quit), give egress a small window to catch up 
                # on the remaining output in the queue.
                if tasks[0] in done and tasks[1] not in done:
                    try:
                        await asyncio.wait_for(tasks[1], timeout=0.2)
                    except (asyncio.TimeoutError, TimeoutError, asyncio.CancelledError):  # noqa: UP041
                        pass
                
                for t in tasks:
                    if t not in done:
                        t.cancel()
                    
                # Final log sync on thread to avoid losing last lines
                if hasattr(self, 'logfile') and hasattr(self, 'mylog'):
                    try:
                        raw_log = self.mylog.getvalue().decode(errors='replace')
                        await asyncio.to_thread(self._write_clean_logfile, raw_log)
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.debug("Final savelog sync failed: %s", exc)

            finally:
                loop.remove_reader(child_fd)
                try:
                    flags = fcntl.fcntl(child_fd, fcntl.F_GETFL)
                    fcntl.fcntl(child_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("Failed restoring blocking mode on child_fd: %s", exc)
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
        if slc and slc not in to_send:  # avoid duplicates if already there
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
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.debug("Failed writing command boundary marker: %s", exc)
            
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
        if connect is True:
            try:
                self._setup_interact_environment(debug=debug, logger=logger, async_mode=True)
                
                local_stream = LocalStream()
                
                def resize_callback(rows, cols):
                    try:
                        self.child.setwinsize(rows, cols)
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.debug("Failed resizing child window: %s", exc)
                
                # Build local copilot handler
                copilot_handler = self._build_local_copilot_handler()
                
                asyncio.run(self._async_interact_loop(local_stream, resize_callback, copilot_handler=copilot_handler))
            finally:
                self._teardown_interact_environment()
        else:
            if logger:
                logger("error", str(connect))
            else:
                printer.error(f"Connection failed: {connect!s}")
            sys.exit(1)

    def _build_local_copilot_handler(self):
        """Build copilot handler for local CLI sessions using rich for rendering."""
        config = getattr(self, 'config', None) if hasattr(self, 'config') else None
        return self._copilot_handler(config)

    def _copilot_handler(self, config):
        """Unified copilot handler for local session."""

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
                                    lines = [line.strip() for line in clean_txt.split('\n') if line.strip()]
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
            except Exception as e:  # noqa: BLE001
                import traceback
                print(f"\n[ERROR in Copilot Handler] {e}", flush=True)
                traceback.print_exc()
                os.write(child_fd, b'\x15\r')

        return handler

    @MethodHook
    def run(
        self,
        commands,
        vars=None,
        *,
        folder='',
        prompt=r'>$|#$|\$$|>.$|#.$|\$.$',
        stdout=False,
        timeout=None,
        logger=None,
        connect_timeout=None,
        auth_timeout=None,
        _default_cmd_timeout=10,
        unattended=True,
        attempt_tracker=None,
    ):
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
        force_ssh = self.protocol == "mosh"
        effective_protocol = "ssh" if force_ssh else self.protocol
        try:
            _, _, eff_cmd, _, _ = resolve_timeouts(
                effective_protocol,
                self.tags,
                getattr(self, "_hop_chain_meta", None) or [],
                timeout=timeout,
                connect_timeout=connect_timeout,
                auth_timeout=auth_timeout,
                _default_cmd_timeout=_default_cmd_timeout,
            )
        except ValueError as e:
            err = f"Connection failed code: 12\nInvalid timeout configuration: {e}"
            self.output = err
            self.status = 1
            if logger:
                logger("error", f"Connection failed: {err}")
            return err

        connect = self._connect(
            timeout=timeout,
            logger=logger,
            force_ssh=force_ssh,
            connect_timeout=connect_timeout,
            auth_timeout=auth_timeout,
            _default_cmd_timeout=_default_cmd_timeout,
            unattended=unattended,
            attempt_tracker=attempt_tracker,
        )
        now = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
        if connect is True:
            if logger:
                port_str = f":{self.port}" if self.port and effective_protocol not in ["ssm", "kubectl", "docker"] else ""
                logger("success", f"Connected to {self.unique} at {self.host}{port_str} via: {effective_protocol}")

            if isinstance(self.tags, dict) and "prompt" in self.tags:
                prompt = self.tags["prompt"]
            prompt_pat = re.compile(prompt.encode("utf-8"), re.DOTALL)
            expects = [prompt_pat, pexpect.EOF, pexpect.TIMEOUT]
            output = ''
            result = 0
            if not isinstance(commands, list):
                commands = [commands]
            if isinstance(self.tags, dict) and "screen_length_command" in self.tags:
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
                result = self.child.expect(expects, timeout=eff_cmd)
                # Only set terminal size on devices without a
                # screen_length_command (e.g. Linux/bash servers).
                # Routers already disable pagination via that command.
                # After setwinsize, consume any SIGWINCH re-render
                # prompt (~40ms on bash) with a short timeout.
                if c == commands[0] and (not isinstance(self.tags, dict) or "screen_length_command" not in self.tags):
                    try:
                        self.child.setwinsize(65535, 65535)
                    except Exception:  # noqa: BLE001
                        try:
                            self.child.setwinsize(10000, 10000)
                        except Exception as exc:  # noqa: BLE001
                            _LOGGER.debug("Fallback setwinsize failed in run: %s", exc)
                    self.child.expect(expects, timeout=1)
                self.child.sendline(c)
                if result == 2:
                    break
            if result != 2:
                result = self.child.expect(expects, timeout=eff_cmd)
            self.child.close()
            output = self._logclean(self.mylog.getvalue().decode("utf-8", errors="replace"), True)
            if logger:
                logger("output", output)
            if folder != '':
                with open(folder + "/" + self.unique + "_" + now + ".txt", "w") as f:
                    f.write(output)
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
            return connect

    @MethodHook
    def test(
        self,
        commands,
        expected,
        vars=None,
        *,
        folder='',
        prompt=r'>$|#$|\$$|>.$|#.$|\$.$',
        timeout=None,
        logger=None,
        connect_timeout=None,
        auth_timeout=None,
        _default_cmd_timeout=10,
        unattended=True,
        attempt_tracker=None,
    ):
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
        force_ssh = self.protocol == "mosh"
        effective_protocol = "ssh" if force_ssh else self.protocol
        try:
            _, _, eff_cmd, _, _ = resolve_timeouts(
                effective_protocol,
                self.tags,
                getattr(self, "_hop_chain_meta", None) or [],
                timeout=timeout,
                connect_timeout=connect_timeout,
                auth_timeout=auth_timeout,
                _default_cmd_timeout=_default_cmd_timeout,
            )
        except ValueError as e:
            err = f"Connection failed code: 12\nInvalid timeout configuration: {e}"
            self.result = None
            self.output = err
            self.status = 1
            if logger:
                logger("error", f"Connection failed: {err}")
            return err

        connect = self._connect(
            timeout=timeout,
            logger=logger,
            force_ssh=force_ssh,
            connect_timeout=connect_timeout,
            auth_timeout=auth_timeout,
            _default_cmd_timeout=_default_cmd_timeout,
            unattended=unattended,
            attempt_tracker=attempt_tracker,
        )
        if connect is True:
            if logger:
                port_str = f":{self.port}" if self.port and effective_protocol not in ["ssm", "kubectl", "docker"] else ""
                logger("success", f"Connected to {self.unique} at {self.host}{port_str} via: {effective_protocol}")

            if isinstance(self.tags, dict) and "prompt" in self.tags:
                prompt = self.tags["prompt"]
            prompt_pat = re.compile(prompt.encode("utf-8"), re.DOTALL)
            expects = [prompt_pat, pexpect.EOF, pexpect.TIMEOUT]
            output = ''
            result = 0
            if not isinstance(commands, list):
                commands = [commands]
            if not isinstance(expected, list):
                expected = [expected]
            if isinstance(self.tags, dict) and "screen_length_command" in self.tags:
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
                result = self.child.expect(expects, timeout=eff_cmd)
                if c == commands[0] and (not isinstance(self.tags, dict) or "screen_length_command" not in self.tags):
                    try:
                        self.child.setwinsize(65535, 65535)
                    except Exception:  # noqa: BLE001
                        try:
                            self.child.setwinsize(10000, 10000)
                        except Exception as exc:  # noqa: BLE001
                            _LOGGER.debug("Fallback setwinsize failed in test: %s", exc)
                    self.child.expect(expects, timeout=1)
                self.child.sendline(c)
                if result == 2:
                    break
            if result != 2:
                result = self.child.expect(expects, timeout=eff_cmd)
            self.child.close()
            output = self._logclean(self.mylog.getvalue().decode("utf-8", errors="replace"), True)
            if logger:
                logger("output", output)
            if folder != '':
                with open(folder + "/" + self.unique + "_" + now + ".txt", "w") as f:
                    f.write(output)
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

    @staticmethod
    def _inject_ssh_timeouts(raw_opts: str, connect_timeout: int | None) -> str:
        """Append -o ConnectTimeout and -o ServerAliveInterval=15 unless already present."""
        opts_str = str(raw_opts).strip() if raw_opts else ""
        if connect_timeout is None:
            return opts_str
        ct_val = validate_strict_timeout_int(connect_timeout, "connect_timeout")
        tokens = shlex.split(opts_str) if opts_str else []
        existing_keys = _extract_ssh_o_keys(tokens)
        if "connecttimeout" not in existing_keys and "connecttimeout" in _ALLOWED_SSH_O_KEYS:
            tokens.extend(["-o", f"ConnectTimeout={ct_val}"])
        if (
            "serveraliveinterval" not in existing_keys
            and "serveraliveinterval" in _ALLOWED_SSH_O_KEYS
        ):
            tokens.extend(["-o", "ServerAliveInterval=15"])
        return shlex.join(tokens)

    def _effective_jumphost_opt(self, connect_timeout: int | None = None) -> str:
        """Return ProxyCommand option, rebuilding hop chain with connect_timeout when set."""
        if (
            connect_timeout is not None
            and getattr(self, "_jumphost_name", "")
            and getattr(self, "config", "") != ""
        ):
            chain_res = self._build_jumphost_chain(
                self._jumphost_name,
                self.config,
                include_meta=True,
                connect_timeout=connect_timeout,
            )
            raw_cmd = chain_res[0]
            if raw_cmd:
                escaped = raw_cmd.replace("\\", "\\\\").replace('"', '\\"')
                return f'-o ProxyCommand="{escaped}"'
            return ""
        return self.jumphost

    @MethodHook
    def _generate_ssh_sftp_cmd(
        self,
        force_ssh=False,
        allow_reverse_tunnel_port=None,
        *,
        connect_timeout=None,
        user_override=None,
    ):
        self._validate_base_fields(check_port=True)
        is_from_mosh = (
            self.protocol == "mosh"
            or getattr(self, "_orig_protocol", "") == "mosh"
        )
        if not is_from_mosh:
            self._validate_ssh_options()
        proto = "ssh" if force_ssh else self.protocol
        eff_user = self.user if user_override is None else user_override
        if eff_user:
            u_s = str(eff_user)
            if u_s.startswith("-") or _LEGACY_SSH_UNSAFE_RE.search(u_s):
                raise ValueError(f"Invalid characters in user: '{u_s}'")
        cmd = proto
        if is_from_mosh:
            host_bare, port_s, raw_opts = self._validate_mosh_ssh_fields(
                self.host,
                eff_user,
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
            opts = self._inject_ssh_timeouts(opts, connect_timeout)
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
            opts = ""
            if raw_opts:
                opts = str(raw_opts).strip()
                if proto == "sftp":
                    opts = re.sub(r'(?<!\S)-[XxtTAaNf]\b', '', opts).strip()
            opts = self._inject_ssh_timeouts(opts, connect_timeout)
            if opts:
                cmd += " " + opts
            target_h = self.host

        jh_opt = self._effective_jumphost_opt(connect_timeout=connect_timeout)
        if jh_opt:
            cmd += " " + jh_opt
        user_host = f"{eff_user}@{target_h}" if eff_user else target_h
        cmd += f" {user_host}"
        return cmd

    @MethodHook
    def _generate_mosh_cmd(self, *, connect_timeout=None, user_override=None):
        eff_user = self.user if user_override is None else user_override
        if str(self.host).startswith("-"):
            raise ValueError("Host cannot start with '-'")
        if eff_user and str(eff_user).startswith("-"):
            raise ValueError("User cannot start with '-'")
        if not self.host or re.search(r'[\s;|&$`><"\'\\]', str(self.host)):
            raise ValueError(f"Invalid characters in host: '{self.host}'")
        if eff_user and re.search(r'[\s;|&$`><"\'\\]', str(eff_user)):
            raise ValueError(f"Invalid characters in user: '{eff_user}'")

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
        ssh_opts = ""
        if isinstance(self.tags, dict) and self.tags.get("mosh_ssh_options"):
            ssh_opts = str(self.tags["mosh_ssh_options"]).strip()
            if ssh_opts:
                if not self._is_unsafe_allowed() and re.search(
                    r'(?:LocalCommand|ProxyCommand|PermitLocalCommand|KnownHostsCommand)',
                    ssh_opts,
                    re.IGNORECASE,
                ):
                    raise ValueError("mosh_ssh_options cannot contain LocalCommand or ProxyCommand")
                try:
                    shlex.split(ssh_opts)
                except ValueError as exc:
                    raise ValueError("Invalid mosh_ssh_options: unbalanced quotes") from exc
        if connect_timeout is not None:
            ssh_opts = self._inject_ssh_timeouts(ssh_opts, connect_timeout)
        if ssh_opts:
            ssh_parts.append(ssh_opts)
        jh_opt = self._effective_jumphost_opt(connect_timeout=connect_timeout)
        if jh_opt:
            ssh_parts.append(jh_opt)

        if len(ssh_parts) > 1:
            raw_ssh = " ".join(ssh_parts)
            cmd_parts.append(f"--ssh={shlex.quote(raw_ssh)}")

        if self.options:
            opts = str(self.options).strip()
            if opts:
                if re.search(r'--(?:ssh|server|client)(?:=|\s|$)', opts):
                    raise ValueError("Options cannot override --ssh, --server or --client for mosh")
                if not self._is_unsafe_allowed() and re.search(
                    r'(?:LocalCommand|ProxyCommand|PermitLocalCommand|KnownHostsCommand)',
                    opts,
                    re.IGNORECASE,
                ):
                    raise ValueError("Options cannot contain LocalCommand or ProxyCommand")
                try:
                    shlex.split(opts)
                except ValueError as exc:
                    raise ValueError("Invalid options: unbalanced quotes") from exc
                cmd_parts.append(opts)

        user_host = f"{eff_user}@{self.host}" if eff_user else self.host
        cmd_parts.extend(["--", user_host])
        return " ".join(cmd_parts)

    @MethodHook
    def _generate_telnet_cmd(self):
        self._validate_base_fields(check_port=True)
        self._validate_options_and_safety()
        cmd = f"telnet {self.host}"
        if self.port:
            cmd += f" {self.port}"
        if self.options:
            cmd += f" {self.options}"
        return cmd

    @MethodHook
    def _generate_kube_cmd(self):
        self._validate_base_fields(check_port=False)
        self._validate_options_and_safety()
        kube_command = self.tags.get("kube_command", "/bin/bash") if isinstance(self.tags, dict) else "/bin/bash"
        if not self._is_unsafe_allowed() and kube_command:
            k_str = str(kube_command)
            if any(c in k_str for c in ("\n", "\r", "\x00")):
                raise ValueError("kube_command cannot contain newlines or null bytes")
            try:
                shlex.split(k_str)
            except ValueError as e:
                raise ValueError(f"Invalid kube_command: unbalanced quotes ({e})") from e
        cmd = f"kubectl exec {self.options} {self.host} -it --"
        cmd += f" {kube_command}"
        return cmd

    @MethodHook
    def _generate_docker_cmd(self):
        self._validate_base_fields(check_port=False)
        self._validate_options_and_safety()
        docker_command = self.tags.get("docker_command", "/bin/bash") if isinstance(self.tags, dict) else "/bin/bash"
        if not self._is_unsafe_allowed() and docker_command:
            d_str = str(docker_command)
            if any(c in d_str for c in ("\n", "\r", "\x00")):
                raise ValueError("docker_command cannot contain newlines or null bytes")
            try:
                shlex.split(d_str)
            except ValueError as e:
                raise ValueError(f"Invalid docker_command: unbalanced quotes ({e})") from e
        cmd = f"docker {self.options} exec -it {self.host}"
        cmd += f" {docker_command}"
        return cmd

    @MethodHook
    def _generate_ssm_cmd(self):
        self._validate_base_fields(check_port=False)
        self._validate_options_and_safety()
        region = self.tags.get("region", "") if isinstance(self.tags, dict) else ""
        profile = self.tags.get("profile", "") if isinstance(self.tags, dict) else ""
        if not self._is_unsafe_allowed():
            for field_name, field_val in [("region", region), ("profile", profile)]:
                if field_val:
                    f_str = str(field_val)
                    if f_str.startswith("-"):
                        raise ValueError(f"SSM tag '{field_name}' cannot start with '-': '{field_val}'")
                    if re.search(r'[\s\x00-\x1f\x7f;|&$`><"\']', f_str):
                        raise ValueError(f"Invalid characters in SSM tag '{field_name}': '{field_val}'")
        cmd = f"aws ssm start-session --target {self.host}"
        if region:
            cmd += f" --region {region}"
        if profile:
            cmd += f" --profile {profile}"
        if self.options:
            cmd += f" {self.options}"
        return cmd


    @MethodHook
    def _get_cmd(
        self,
        force_ssh=False,
        allow_reverse_tunnel_port=None,
        *,
        connect_timeout=None,
        user_override=None,
    ):
        if force_ssh or self.protocol in ["ssh", "sftp"]:
            return self._generate_ssh_sftp_cmd(
                force_ssh=force_ssh,
                allow_reverse_tunnel_port=allow_reverse_tunnel_port,
                connect_timeout=connect_timeout,
                user_override=user_override,
            )
        elif self.protocol == "mosh":
            return self._generate_mosh_cmd(
                connect_timeout=connect_timeout,
                user_override=user_override,
            )
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

    def _spawn_child(self, args, protocol=None, encoding=None, *, _secrets=()):
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
        spawn_kwargs = {}
        if env is not None:
            spawn_kwargs["env"] = env
        if encoding is not None:
            spawn_kwargs["encoding"] = encoding
        try:
            return pexpect.spawn(args[0], args[1:], **spawn_kwargs), None
        except Exception as exc:  # noqa: BLE001
            sanitized = sanitize_exception(
                exc,
                code="SPAWN_ERROR",
                secrets=_secrets,
                context_label="Connection failed",
            )
            return None, str(sanitized)

    def _extract_auth_budgets(self) -> tuple[int, int | None, int | None, int | None]:
        cfg_dict = (
            self.config.config
            if (self.config and hasattr(self.config, "config") and isinstance(self.config.config, dict))
            else {}
        )
        tags_dict = self.tags if isinstance(self.tags, dict) else {}

        def _pick(key: str, default_val):
            if key in tags_dict and tags_dict[key] is not None:
                return tags_dict[key]
            if key in cfg_dict and cfg_dict[key] is not None:
                return cfg_dict[key]
            return default_val

        max_auth_prompts = validate_strict_int_range(
            _pick("max_auth_prompts", DEFAULT_MAX_AUTH_PROMPTS),
            "max_auth_prompts",
            MIN_AUTH_PROMPTS,
            MAX_AUTH_PROMPTS_CAP,
        )
        raw_total = _pick("max_total_auth_attempts", None)
        max_total_auth_attempts = (
            None
            if raw_total is None
            else validate_strict_int_range(
                raw_total,
                "max_total_auth_attempts",
                MIN_TOTAL_AUTH_ATTEMPTS,
                MAX_TOTAL_AUTH_ATTEMPTS_CAP,
            )
        )
        raw_hop = _pick("max_hop_host_attempts", None)
        max_hop_host_attempts = (
            None
            if raw_hop is None
            else validate_strict_int_range(
                raw_hop,
                "max_hop_host_attempts",
                MIN_HOP_HOST_ATTEMPTS,
                MAX_HOP_HOST_ATTEMPTS_CAP,
            )
        )
        raw_global_user = _pick("max_global_user_attempts", None)
        max_global_user_attempts = (
            None
            if raw_global_user is None
            else validate_strict_int_range(
                raw_global_user,
                "max_global_user_attempts",
                1,
                1000,
            )
        )
        return (
            max_auth_prompts,
            max_total_auth_attempts,
            max_hop_host_attempts,
            max_global_user_attempts,
        )

    @staticmethod
    def _emit_aaa_fallback_log(
        logger,
        *,
        unique: str,
        host: str,
        protocol: str,
        user: str,
        label: str,
        secrets: list[str],
    ) -> None:
        raw_msg = (
            f"[AAA-FALLBACK] Activating backup credentials ({label}) "
            f"for node={unique} host={host} proto={protocol} user={user or '<default>'}"
        )
        safe_msg = sanitize_log_message(raw_msg, secrets=secrets)
        if logger:
            try:
                logger("warning", safe_msg)
                return
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Fallback logger failed; falling back to stderr")
        with suppress(Exception):
            sys.stderr.write(safe_msg + "\n")
            sys.stderr.flush()

    def _build_local_decryptor(self):
        decryptor_cache = {}

        def _decrypt_one(cipher_text: str) -> str:
            if not re.match(r'^b["\'].+["\']$', cipher_text, re.DOTALL):
                return cipher_text
            if "fn" not in decryptor_cache:
                keyfile = getattr(self, "key", None)
                if not keyfile:
                    raise SanitizedAuthError("No RSA key configured", code="AUTH_FAILED")
                from Crypto.Cipher import PKCS1_OAEP
                from Crypto.PublicKey import RSA

                with open(keyfile) as kf:
                    rsa_key = RSA.import_key(kf.read())
                decryptor_cache["fn"] = PKCS1_OAEP.new(rsa_key)
            raw_bytes = ast.literal_eval(cipher_text)
            return decryptor_cache["fn"].decrypt(raw_bytes).decode("utf-8")

        return _decrypt_one

    def _resolve_runtime_credentials(
        self,
        *,
        allow_fallback: bool,
        unattended: bool,
    ) -> tuple[list[RuntimeAuthCandidate], list[list[str]], list[str]]:
        profiles_map = (
            getattr(self.config, "profiles", {})
            if (getattr(self, "config", "") != "" and self.config is not None)
            else {}
        )
        local_decrypt = self._build_local_decryptor()
        passtx_desc = type(self).__dict__.get("_passtx")
        passtx_mocked = "_passtx" in self.__dict__ or not isinstance(passtx_desc, MethodHook)

        def _decrypt_plain_list(specs: list[str]) -> list[str]:
            if not specs:
                return []
            if passtx_mocked or not unattended:
                return list(self._passtx(specs))
            return [local_decrypt(s) for s in specs]

        def _expand_spec(spec: str | tuple[str, ...]) -> list[str]:
            items = list(spec) if isinstance(spec, (tuple, list)) else ([spec] if spec != "" else [])
            out: list[str] = []
            for item in items:
                if not isinstance(item, str) or item == "":
                    continue
                if is_compose_expression(item):
                    variants = parse_compose_expression(
                        item,
                        profiles=profiles_map,
                        decrypt_fn=local_decrypt,
                        max_depth=MAX_PROFILE_DEPTH,
                        max_variants=MAX_COMPOSE_VARIANTS,
                    )
                    out.extend(variants)
                else:
                    out.extend(_decrypt_plain_list([item]))
            return out

        candidates_src = list(getattr(self, "_auth_candidates", ()))
        if not candidates_src:
            # Compatibility if a subclass or test mutated self.password directly
            raw_pw_list = self.password if isinstance(self.password, list) else [self.password]
            non_empty = tuple(p for p in raw_pw_list if isinstance(p, str) and p != "")
            if non_empty or self.user:
                candidates_src = [
                    AuthCandidate(
                        user=str(self.user or ""),
                        password_spec=non_empty if len(non_empty) > 1 else (non_empty[0] if non_empty else ""),
                        is_fallback=False,
                        label="primary",
                    )
                ]

        runtime_candidates: list[RuntimeAuthCandidate] = []
        raw_secrets: list[str] = []

        for cand in candidates_src:
            if cand.is_fallback and not allow_fallback:
                continue
            if isinstance(cand.password_spec, tuple):
                raw_secrets.extend(s for s in cand.password_spec if isinstance(s, str) and s)
            elif isinstance(cand.password_spec, str) and cand.password_spec:
                raw_secrets.append(cand.password_spec)

            expanded_pws = _expand_spec(cand.password_spec)
            raw_secrets.extend(p for p in expanded_pws if isinstance(p, str) and p)
            if expanded_pws:
                for pw_val in expanded_pws:
                    runtime_candidates.append(
                        RuntimeAuthCandidate(
                            user=cand.user,
                            password=pw_val,
                            has_password=True,
                            is_fallback=cand.is_fallback,
                            label=cand.label,
                        )
                    )
            else:
                runtime_candidates.append(
                    RuntimeAuthCandidate(
                        user=cand.user,
                        password="",
                        has_password=False,
                        is_fallback=cand.is_fallback,
                        label=cand.label,
                    )
                )

        hop_runtime_passwords: list[list[str]] = []
        for hop in getattr(self, "_jumphost_hops", ()):
            h_pws = _expand_spec(hop.get("passwords", ()))
            raw_secrets.extend(p for p in h_pws if isinstance(p, str) and p)
            hop_runtime_passwords.append(h_pws)

        return runtime_candidates, hop_runtime_passwords, raw_secrets

    def _disambiguate_prompt(self, line: str) -> tuple[str, int | None]:
        """Classify password prompt line strictly using exact host boundaries."""
        tags_dict = self.tags if isinstance(self.tags, dict) else {}
        target_names = [str(self.host)]
        if tags_dict.get("fqdn"):
            target_names.append(str(tags_dict["fqdn"]))
        matched_target = any(
            host_exact_re(h).search(line) for h in target_names if h
        )

        matched_hops: list[int] = []
        for idx, hop in enumerate(getattr(self, "_jumphost_hops", ())):
            h_names = [hop.get("host", "")]
            if hop.get("resolved_fqdn"):
                h_names.append(str(hop["resolved_fqdn"]))
            if any(host_exact_re(h).search(line) for h in h_names if h):
                matched_hops.append(idx)

        total_matches = len(matched_hops) + (1 if matched_target else 0)
        if total_matches > 1:
            return "conflict", None
        if matched_hops:
            return "hop", matched_hops[0]
        if matched_target:
            return "target", None
        return "ambiguous", None

    @MethodHook
    def _connect(
        self,
        debug=False,
        timeout=None,
        max_attempts=3,
        logger=None,
        force_ssh=False,
        allow_reverse_tunnel_port=None,
        *,
        connect_timeout=None,
        auth_timeout=None,
        _default_cmd_timeout=10,
        _default_max_total_seconds=None,
        clock=_time_mod.monotonic,
        unattended=False,
        attempt_tracker=None,
    ):
        self.last_fallback_used = False
        effective_protocol = "ssh" if force_ssh else self.protocol
        if effective_protocol == "mosh" and shutil.which("mosh") is None:
            return "Connection failed: mosh binary not found in PATH"

        hop_meta = getattr(self, "_hop_chain_meta", None) or []
        try:
            validate_strict_timeout_int(
                max_attempts,
                "max_attempts",
                min_val=1,
                max_val=MAX_STAGE_TIMEOUT_CAP,
            )
            eff_ct, eff_at, _eff_cmd, max_ext, max_total = resolve_timeouts(
                effective_protocol,
                self.tags,
                hop_meta,
                timeout=timeout,
                connect_timeout=connect_timeout,
                auth_timeout=auth_timeout,
                _default_cmd_timeout=_default_cmd_timeout,
                _default_max_total_seconds=_default_max_total_seconds,
            )
        except ValueError as e:
            return f"Connection failed code: 12\nInvalid timeout configuration: {e}"

        try:
            (
                max_auth_prompts,
                max_total_auth_attempts,
                max_hop_host_attempts,
                max_global_user_attempts,
            ) = self._extract_auth_budgets()
        except ValueError as exc:
            san = sanitize_exception(
                exc,
                code="AUTH_FAILED",
                context_label="invalid auth budget config",
            )
            return str(san)

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

        cfg_dict = (
            self.config.config
            if (self.config and hasattr(self.config, "config") and isinstance(self.config.config, dict))
            else {}
        )
        tags_dict = self.tags if isinstance(self.tags, dict) else {}
        allow_fallback = not (tags_dict.get("allow_fallback") is False)
        disable_insecure_hk = bool(
            getattr(self, "disable_fallback_on_insecure_hostkey", False)
            or tags_dict.get("disable_fallback_on_insecure_hostkey")
            or cfg_dict.get("disable_fallback_on_insecure_hostkey")
        )
        if (
            allow_fallback
            and disable_insecure_hk
            and has_insecure_hostkey_checking(
                self.options,
                self.tags,
                getattr(self, "_jumphost_hops", ()),
            )
        ):
            allow_fallback = False

        raw_secrets: list[str] = []
        try:
            runtime_candidates, hop_runtime_passwords, raw_secrets = self._resolve_runtime_credentials(
                allow_fallback=allow_fallback,
                unattended=unattended,
            )
        except Exception as exc:  # noqa: BLE001
            san = sanitize_exception(
                exc,
                code="AUTH_FAILED",
                secrets=raw_secrets,
                context_label="credential resolution failed",
            )
            return str(san)

        if self.logs != '':
            self.logfile = self._logfile()
        default_prompt = r'>$|#$|\$$|>.$|#.$|\$.$'
        prompt = tags_dict.get("prompt", default_prompt) if tags_dict else default_prompt
        password_prompt = (
            r'[pP]assword:|[uU]sername:' if effective_protocol != 'telnet' else r'[pP]assword:'
        )
        prompt_unanchored = "|".join(t.removesuffix("$") for t in prompt.split("|"))
        mosh_prompt = r"(?:" + prompt_unanchored + r")\s*(?:\x1b\[[0-9;?]*[a-zA-Z])*$"

        def _precompile_user_pat(pat_str: str):
            try:
                pat_str.encode("ascii")
            except UnicodeEncodeError:
                return re.compile(pat_str.encode("utf-8"), re.DOTALL)
            return pat_str

        user_prompt_pat = _precompile_user_pat(prompt)
        mosh_prompt_pat = _precompile_user_pat(mosh_prompt)

        expects = {
            "ssh": [
                'yes/no',
                r'(?m:^(?:(?:ssh|sftp):[^\r\n]{1,256} )?Connection refused(?:\r?\n))',
                r'(?im:^(?:(?:ssh|sftp|debug\d+):[^\r\n]{1,256}not supported|Permission denied \([^)\r\n]{1,256}\)\.)(?:\r?\n))',
                r'(?im:^(?:(?:ssh|sftp):[^\r\n]{1,256}Invalid|usage:\s*(?:ssh|sftp)\b[^\r\n]{0,256})(?:\r?\n))',
                'ssh-keygen.*"',
                r'(?im:^(?:(?:ssh|sftp):[^\r\n]{1,256}(?:Operation timed out|Connection timed out)|kex_exchange_identification:[^\r\n]{1,256}timed out|Connection timed out during banner exchange)(?:\r?\n))',
                r'(?im:^(?:ssh|sftp|ssh_exchange_identification):[^\r\n]{1,256}(?:Resource temporarily unavailable|Name or service not known|Temporary failure in name resolution|No address associated with hostname|unavailable)(?:\r?\n))',
                r'(?im:^(?:Connection to [^\r\n]{1,256} closed(?: by remote host)?\.?|Connection closed by (?:remote host|foreign host|authenticating user [^\r\n]{1,128}|[^\r\n]{1,256} port \d{1,5})|(?:kex|ssh)_exchange_identification: Connection closed[^\r\n]{0,256})(?:\r?\n))',
                password_prompt,
                user_prompt_pat,
                'suspend',
                pexpect.EOF,
                pexpect.TIMEOUT,
                r'(?im:^(?:(?:ssh|sftp):[^\r\n]{1,256})?No route to host(?:\r?\n))',
                r'(?im:^(?:ssh|sftp):[^\r\n]{1,256}resolve hostname[^\r\n]{0,256}(?:\r?\n))',
                r'(?im:^(?:(?:ssh|sftp):|Unable to negotiate with )[^\r\n]{1,256}no matching[^\r\n]{0,256}(?:\r?\n))',
                r'(?im:^Bad (?:owner|permissions)[^\r\n]{0,256}(?:\r?\n))',
            ],
            "sftp": [
                'yes/no',
                r'(?m:^(?:(?:ssh|sftp):[^\r\n]{1,256} )?Connection refused(?:\r?\n))',
                r'(?im:^(?:(?:ssh|sftp|debug\d+):[^\r\n]{1,256}not supported|Permission denied \([^)\r\n]{1,256}\)\.)(?:\r?\n))',
                r'(?im:^(?:(?:ssh|sftp):[^\r\n]{1,256}Invalid|usage:\s*(?:ssh|sftp)\b[^\r\n]{0,256})(?:\r?\n))',
                'ssh-keygen.*"',
                r'(?im:^(?:(?:ssh|sftp):[^\r\n]{1,256}(?:Operation timed out|Connection timed out)|kex_exchange_identification:[^\r\n]{1,256}timed out|Connection timed out during banner exchange)(?:\r?\n))',
                r'(?im:^(?:ssh|sftp|ssh_exchange_identification):[^\r\n]{1,256}(?:Resource temporarily unavailable|Name or service not known|Temporary failure in name resolution|No address associated with hostname|unavailable)(?:\r?\n))',
                r'(?im:^(?:Connection to [^\r\n]{1,256} closed(?: by remote host)?\.?|Connection closed by (?:remote host|foreign host|authenticating user [^\r\n]{1,128}|[^\r\n]{1,256} port \d{1,5})|(?:kex|ssh)_exchange_identification: Connection closed[^\r\n]{0,256})(?:\r?\n))',
                password_prompt,
                user_prompt_pat,
                'suspend',
                pexpect.EOF,
                pexpect.TIMEOUT,
                r'(?im:^(?:(?:ssh|sftp):[^\r\n]{1,256})?No route to host(?:\r?\n))',
                r'(?im:^(?:ssh|sftp):[^\r\n]{1,256}resolve hostname[^\r\n]{0,256}(?:\r?\n))',
                r'(?im:^(?:(?:ssh|sftp):|Unable to negotiate with )[^\r\n]{1,256}no matching[^\r\n]{0,256}(?:\r?\n))',
                r'(?im:^Bad (?:owner|permissions)[^\r\n]{0,256}(?:\r?\n))',
            ],
            "telnet": [
                r'[uU]sername:',
                r'(?im:^(?:telnet:[^\r\n]{1,256} )?(?:Unable to connect to remote host: )?Connection refused(?:\r?\n))',
                r'(?im:^(?:telnet:[^\r\n]{1,256}not supported|[^\r\n]{0,64}Authentication not supported)(?:\r?\n))',
                r'(?im:^(?:telnet:|usage:\s*telnet\b)[^\r\n]{0,256}(?:invalid|unrecognized option)[^\r\n]{0,256}(?:\r?\n))',
                'ssh-keygen.*"',
                r'(?im:^(?:telnet:[^\r\n]{1,256}(?:Operation timed out|Connection timed out)|Connection timed out)(?:\r?\n))',
                r'(?im:^(?:telnet:[^\r\n]{1,256}(?:Name or service not known|Temporary failure in name resolution|unavailable)|[^\r\n]{0,128}: Name or service not known)(?:\r?\n))',
                r'(?im:^(?:Connection closed by (?:foreign host|remote host)|telnet:[^\r\n]{1,256}closed)(?:\r?\n))',
                password_prompt,
                user_prompt_pat,
                'suspend',
                pexpect.EOF,
                pexpect.TIMEOUT,
                r'(?im:^(?:telnet:[^\r\n]{1,256})?No route to host(?:\r?\n))',
                r'(?im:^telnet:[^\r\n]{1,256}resolve hostname[^\r\n]{0,256}(?:\r?\n))',
                r'(?im:^telnet:[^\r\n]{1,256}no matching[^\r\n]{0,256}(?:\r?\n))',
                r'(?im:^Bad (?:owner|permissions)[^\r\n]{0,256}(?:\r?\n))',
            ],
            "kubectl": [
                r'[uU]sername:',
                r'[rR]efused',
                r'[Ee]rror',
                'DEPRECATED',
                pexpect.TIMEOUT,
                password_prompt,
                user_prompt_pat,
                pexpect.EOF,
                "expired|invalid",
            ],
            "docker": [
                r'[uU]sername:',
                'Cannot',
                r'[Ee]rror',
                'failed',
                'not a docker command',
                'unknown',
                'unable to resolve',
                pexpect.TIMEOUT,
                password_prompt,
                user_prompt_pat,
                pexpect.EOF,
            ],
            "ssm": [
                r'[uU]sername:',
                r'(?im:^(?:Cannot (?:perform start-session|connect to instance|start session)[^\r\n]{0,256})(?:\r?\n))',
                r'(?im:^(?:An error occurred \([^)\r\n]{1,128}\)[^\r\n]{0,256}|aws: error:[^\r\n]{1,512}|Error: (?:Target|An error|Unable|The security token|Invalid|AccessDenied|SessionManager)[^\r\n]{1,256})(?:\r?\n))',
                r'(?im:^(?:TargetNotConnected[^\r\n]{0,256}|(?:SessionManagerPlugin|Starting session|An error occurred)[^\r\n]{0,256}failed[^\r\n]{0,256})(?:\r?\n))',
                'SessionManagerPlugin',
                r'(?im:^(?:Unknown(?:Error|ClientException)[^\r\n]{0,256}|An error occurred \([^)\r\n]{0,128}Unknown[^)\r\n]{0,128}\)[^\r\n]{0,256}|aws: [^\r\n]{1,256}invalid choice[^\r\n]{0,256})(?:\r?\n))',
                r'(?im:^(?:Could not resolve host|Unable to locate credentials|Unable to resolve [^\r\n]{1,256})(?:\r?\n))',
                pexpect.TIMEOUT,
                password_prompt,
                user_prompt_pat,
                pexpect.EOF,
            ],
        }
        expects["mosh"] = [
            'yes/no',
            r'(?:ssh: connect to host .* port .*: Connection refused|Connection refused)',
            r'(?:(?:ssh|mosh): .*supported|Permission denied \([^)\r\n]{1,256}\)\.)',
            r'(?:(?:ssh|mosh): (?:Invalid|usage)|Unknown option:)',
            'ssh-keygen.*"',
            r'(?:ssh: connect to host .* port .*: Connection timed out|mosh: .* timed out)',
            r'(?:ssh|mosh): .*unavailable',
            r'(?:Connection to .* closed|Connection closed by remote host)',
            password_prompt,
            mosh_prompt_pat,
            'suspend',
            pexpect.EOF,
            pexpect.TIMEOUT,
            "No route to host",
            "resolve hostname",
            r'(?:ssh|mosh): .*no matching',
            r"[bB]ad (owner|permissions)",
            "mosh-server:.*not found|command not found",
            r"(?:Did not find mosh server startup message|(?:mosh: )?Nothing received from server on UDP port \d+\.?|Nothing received from server)",
            "needs a UTF-8 native locale to run|requires a UTF-8 locale|locale.*not available",
        ]

        compiled_expects = compile_expects(expects)

        error_indices = {
            "ssh": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16],
            "sftp": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16],
            "telnet": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16],
            "kubectl": [1, 2, 3, 4, 8],
            "docker": [1, 2, 3, 4, 5, 6, 7],
            "ssm": [1, 2, 3, 4, 5, 6, 7],
            "mosh": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16, 17, 18, 19],
        }

        eof_indices = {
            "ssh": [8, 9, 10, 11],
            "sftp": [8, 9, 10, 11],
            "telnet": [8, 9, 10, 11],
            "kubectl": [5, 6, 7],
            "docker": [8, 9, 10],
            "ssm": [8, 9, 10],
            "mosh": [8, 9, 10, 11],
        }

        initial_indices = {
            "ssh": [0],
            "sftp": [0],
            "telnet": [0],
            "kubectl": [0],
            "docker": [0],
            "ssm": [0],
            "mosh": [0],
        }

        def _to_bytes(val) -> bytes:
            if val is None:
                return b""
            if isinstance(val, bytes):
                return val
            return str(val).encode("utf-8", errors="replace")

        def _to_str(val) -> str:
            if val is None:
                return ""
            if isinstance(val, bytes):
                return val.decode("utf-8", errors="replace")
            return str(val)

        tracker = (
            attempt_tracker
            if attempt_tracker is not None
            else AuthAttemptTracker(
                default_max_host_attempts=DEFAULT_MAX_TOTAL_AUTH_ATTEMPTS,
                default_max_hop_attempts=DEFAULT_MAX_HOP_HOST_ATTEMPTS,
                clock=clock,
            )
        )
        deadline = clock() + max_total
        budget_warned = False
        proto_expects = compiled_expects[effective_protocol]
        pw_prompt_idx = 5 if effective_protocol == "kubectl" else 8
        suspend_idx = 10 if effective_protocol in ("ssh", "sftp", "telnet", "mosh") else None
        stream_limit = MAX_AUTH_STREAM_BYTES if unattended else MAX_PRE_AUTH_BUFFER_BYTES
        jumphost_hops = getattr(self, "_jumphost_hops", ())

        st: dict[str, object] = {
            "cand_idx": 0,
            "connect_retries": 0,
            "eof_fallback_advances": 0,
            "target_creds_sent": False,
            "cur_cand": None,
            "cur_user": "",
            "handshake_progressed": False,
            "prompts_this_conn": 0,
            "cand_sent_pw_this_conn": False,
            "logged_fallback_this_cand": False,
            "stage": "connect",
            "last_observed_bytes_len": 0,
        }

        def _handle_username_prompt(child_obj) -> tuple[bool, str | None]:
            if not unattended:
                if self.user:
                    child_obj.sendline(self.user)
                    st["last_observed_bytes_len"] = 0
                    return False, None
                self.missingtext = True
                return True, None

            cand_idx_cur = int(st["cand_idx"])
            prompts_cur = int(st["prompts_this_conn"])
            cur_cand = st["cur_cand"]

            if st["cand_sent_pw_this_conn"]:
                if (cand_idx_cur + 1) >= len(runtime_candidates):
                    return False, str(sanitize_exception(None, code="AUTH_FAILED", secrets=raw_secrets))
                if prompts_cur >= max_auth_prompts:
                    st["cand_idx"] = cand_idx_cur + 1
                    return False, "__NEXT__:next_candidate"
                cand_idx_cur += 1
                st["cand_idx"] = cand_idx_cur
                cur_cand = runtime_candidates[cand_idx_cur]
                st["cur_cand"] = cur_cand
                st["cur_user"] = cur_cand.user
                st["cand_sent_pw_this_conn"] = False
                st["logged_fallback_this_cand"] = False

            u_to_send = cur_cand.user if cur_cand is not None else ""
            if not u_to_send:
                if (cand_idx_cur + 1) < len(runtime_candidates) and runtime_candidates[cand_idx_cur + 1].user:
                    cand_idx_cur += 1
                    st["cand_idx"] = cand_idx_cur
                    cur_cand = runtime_candidates[cand_idx_cur]
                    st["cur_cand"] = cur_cand
                    st["cur_user"] = cur_cand.user
                    u_to_send = cur_cand.user
                    st["logged_fallback_this_cand"] = False
                else:
                    return False, str(sanitize_exception(None, code="NO_CREDS", secrets=raw_secrets))

            if not tracker.try_acquire_user(u_to_send, limit=max_global_user_attempts):
                return False, str(
                    sanitize_exception(None, code="USER_BUDGET_EXHAUSTED", secrets=raw_secrets)
                )

            if cur_cand is not None and cur_cand.is_fallback and not st["logged_fallback_this_cand"]:
                self._emit_aaa_fallback_log(
                    logger,
                    unique=str(self.unique),
                    host=str(self.host),
                    protocol=effective_protocol,
                    user=u_to_send,
                    label=cur_cand.label,
                    secrets=raw_secrets,
                )
                self.last_fallback_used = True
                st["logged_fallback_this_cand"] = True

            child_obj.sendline(u_to_send)
            st["handshake_progressed"] = True
            st["stage"] = "auth"
            st["last_observed_bytes_len"] = 0
            return False, None

        def _handle_target_password_prompt(child_obj) -> tuple[bool, str | None]:
            cand_idx_cur = int(st["cand_idx"])
            prompts_cur = int(st["prompts_this_conn"])
            cur_cand = st["cur_cand"]
            cur_user = str(st["cur_user"])

            if not unattended:
                if cur_cand is not None and cur_cand.has_password and not st["cand_sent_pw_this_conn"]:
                    child_obj.sendline(cur_cand.password)
                    st["cand_sent_pw_this_conn"] = True
                    st["target_creds_sent"] = True
                    st["handshake_progressed"] = True
                    st["stage"] = "auth"
                    st["last_observed_bytes_len"] = 0
                    return False, None
                if (cand_idx_cur + 1) < len(runtime_candidates) and not runtime_candidates[cand_idx_cur + 1].is_fallback:
                    cand_idx_cur += 1
                    st["cand_idx"] = cand_idx_cur
                    cur_cand = runtime_candidates[cand_idx_cur]
                    st["cur_cand"] = cur_cand
                    if cur_cand.has_password:
                        child_obj.sendline(cur_cand.password)
                        st["cand_sent_pw_this_conn"] = True
                        st["target_creds_sent"] = True
                        st["handshake_progressed"] = True
                        st["stage"] = "auth"
                        st["last_observed_bytes_len"] = 0
                        return False, None
                self.missingtext = True
                return True, None

            if st["cand_sent_pw_this_conn"]:
                if (cand_idx_cur + 1) >= len(runtime_candidates):
                    return False, str(sanitize_exception(None, code="AUTH_FAILED", secrets=raw_secrets))
                nxt = runtime_candidates[cand_idx_cur + 1]
                if nxt.user == cur_user and prompts_cur < max_auth_prompts:
                    cand_idx_cur += 1
                    st["cand_idx"] = cand_idx_cur
                    cur_cand = nxt
                    st["cur_cand"] = cur_cand
                    st["cand_sent_pw_this_conn"] = False
                    st["logged_fallback_this_cand"] = False
                else:
                    st["cand_idx"] = cand_idx_cur + 1
                    return False, "__NEXT__:next_candidate"

            if cur_cand is None or not cur_cand.has_password:
                if (cand_idx_cur + 1) < len(runtime_candidates):
                    nxt = runtime_candidates[cand_idx_cur + 1]
                    if nxt.user == cur_user and prompts_cur < max_auth_prompts:
                        cand_idx_cur += 1
                        st["cand_idx"] = cand_idx_cur
                        cur_cand = nxt
                        st["cur_cand"] = cur_cand
                        st["cand_sent_pw_this_conn"] = False
                        st["logged_fallback_this_cand"] = False
                    else:
                        st["cand_idx"] = cand_idx_cur + 1
                        return False, "__NEXT__:next_candidate"
                else:
                    return False, str(sanitize_exception(None, code="NO_CREDS", secrets=raw_secrets))

            if not cur_cand.has_password:
                return False, str(sanitize_exception(None, code="NO_CREDS", secrets=raw_secrets))

            if prompts_cur >= max_auth_prompts:
                if (cand_idx_cur + 1) < len(runtime_candidates):
                    st["cand_idx"] = cand_idx_cur + 1
                    return False, "__NEXT__:next_candidate"
                return False, str(sanitize_exception(None, code="AUTH_FAILED", secrets=raw_secrets))

            eff_u = cur_cand.user or str(self.user or "")
            acquired, budget_err_code = tracker.try_acquire_target_attempt(
                host=str(self.host),
                port=str(self.port or ""),
                protocol=effective_protocol,
                user=eff_u,
                host_limit=max_total_auth_attempts,
                user_limit=max_global_user_attempts,
            )
            if not acquired:
                return False, str(
                    sanitize_exception(
                        None,
                        code=budget_err_code or "HOST_BUDGET_EXHAUSTED",
                        secrets=raw_secrets,
                    )
                )

            if cur_cand.is_fallback and not st["logged_fallback_this_cand"]:
                self._emit_aaa_fallback_log(
                    logger,
                    unique=str(self.unique),
                    host=str(self.host),
                    protocol=effective_protocol,
                    user=eff_u,
                    label=cur_cand.label,
                    secrets=raw_secrets,
                )
                self.last_fallback_used = True
                st["logged_fallback_this_cand"] = True

            child_obj.sendline(cur_cand.password)
            st["cand_sent_pw_this_conn"] = True
            st["target_creds_sent"] = True
            st["prompts_this_conn"] = prompts_cur + 1
            st["handshake_progressed"] = True
            st["stage"] = "auth"
            st["last_observed_bytes_len"] = 0
            return False, None

        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                return _redact_known_secrets(
                    f"Connection failed code: 12\nConnection timeout (global deadline {max_total}s exceeded)",
                    raw_secrets,
                )

            cand_idx_val = int(st["cand_idx"])
            cur_cand = runtime_candidates[cand_idx_val] if cand_idx_val < len(runtime_candidates) else None
            cur_user = cur_cand.user if cur_cand is not None else str(self.user or "")
            st["cur_cand"] = cur_cand
            st["cur_user"] = cur_user
            user_override = (
                cur_user
                if (cand_idx_val > 0 or (cur_user and cur_user != str(self.user or "")))
                else None
            )

            cmd = self._get_cmd(
                force_ssh=force_ssh,
                allow_reverse_tunnel_port=allow_reverse_tunnel_port,
                connect_timeout=eff_ct,
                user_override=user_override,
            )

            st["handshake_progressed"] = False
            args = shlex.split(cmd)
            child, err = self._spawn_child(
                args,
                protocol=effective_protocol,
                _secrets=raw_secrets,
            )
            if err:
                return _redact_known_secrets(err, raw_secrets)

            connected_ok = False
            local_mylog = None
            next_action: str | None = None
            hop_pw_idx = [0] * len(jumphost_hops)
            hop_cursor = 0
            st["prompts_this_conn"] = 0
            st["cand_sent_pw_this_conn"] = False
            st["logged_fallback_this_cand"] = False
            transport_error_seen = False

            try:
                child.logfile = None
                child.logfile_send = None
                if unattended:
                    child.logfile_read = None

                if isinstance(self.tags, dict) and self.tags.get("console"):
                    child.sendline()
                if debug:
                    if logger:
                        logger("debug", f"Command:\n{_redact_known_secrets(cmd, raw_secrets)}")
                    local_mylog = io.BytesIO()
                    self.mylog = local_mylog
                    redacted_cmd = _redact_known_secrets(cmd, raw_secrets)
                    local_mylog.write(f"[i] [DEBUG] Command:\r\n    {redacted_cmd}\r\n".encode())
                    if not unattended:
                        child.logfile_read = local_mylog

                st["stage"] = "connect"
                progress_extensions = 0
                st["last_observed_bytes_len"] = 0

                while True:
                    remaining = deadline - clock()
                    if remaining <= 0:
                        return _redact_known_secrets(
                            f"Connection failed code: 12\nConnection timeout (global deadline {max_total}s exceeded)",
                            raw_secrets,
                        )
                    stage_timeout = eff_ct if st["stage"] == "connect" else eff_at
                    expect_timeout = max(0.001, min(stage_timeout, remaining))
                    results = child.expect(proto_expects, timeout=expect_timeout)
                    results_value = proto_expects[results]
                    before_bytes = _to_bytes(getattr(child, "before", b""))
                    after_bytes = _to_bytes(getattr(child, "after", b""))

                    if len(before_bytes) > stream_limit:
                        return _redact_known_secrets(
                            f"Connection failed code: 12\nPre-auth buffer exceeded maximum size ({stream_limit} bytes)",
                            raw_secrets,
                        )

                    if results in initial_indices[effective_protocol]:
                        if effective_protocol in ["ssh", "sftp", "mosh"]:
                            child.sendline('yes')
                            st["handshake_progressed"] = True
                            st["last_observed_bytes_len"] = 0
                        elif effective_protocol in ["telnet", "kubectl", "docker", "ssm"]:
                            should_break, msg_or_action = _handle_username_prompt(child)
                            if msg_or_action == "__NEXT__:next_candidate":
                                next_action = "next_candidate"
                                break
                            if msg_or_action is not None:
                                return msg_or_action
                            if should_break:
                                break

                    elif results in error_indices[effective_protocol]:
                        if results_value == pexpect.TIMEOUT:
                            cur_len = len(before_bytes)
                            bytes_grew = cur_len > int(st["last_observed_bytes_len"])
                            st["last_observed_bytes_len"] = cur_len
                            remaining = deadline - clock()
                            can_extend = (
                                bytes_grew
                                and progress_extensions < max_ext
                                and remaining > 0
                                and not (effective_protocol == "mosh" and st["handshake_progressed"])
                            )
                            if can_extend:
                                progress_extensions += 1
                                if not budget_warned:
                                    budget_warned = True
                                    if logger:
                                        logger(
                                            "warning",
                                            f"Slow {effective_protocol} handshake making progress; extending connect wait (deadline {max_total}s)",
                                        )
                                continue

                            if (
                                unattended
                                and st["stage"] == "auth"
                                and st["target_creds_sent"]
                                and (int(st["cand_idx"]) + 1) < len(runtime_candidates)
                                and remaining > 0
                            ):
                                st["cand_idx"] = int(st["cand_idx"]) + 1
                                next_action = "next_candidate"
                                break

                            can_retry = (
                                not st["target_creds_sent"]
                                and (int(st["connect_retries"]) + 1) < max_attempts
                                and remaining > 0
                                and not (effective_protocol == "mosh" and st["handshake_progressed"])
                            )
                            if can_retry:
                                st["connect_retries"] = int(st["connect_retries"]) + 1
                                next_action = "retry_connect"
                                break

                            before_str = _to_str(before_bytes)
                            if effective_protocol == "mosh":
                                before_str = self._logclean(before_str, True)
                            if remaining <= 0:
                                after_str = f"Connection timeout (global deadline {max_total}s exceeded)"
                            else:
                                after_str = "Connection timeout"
                            return _redact_known_secrets(
                                f"Connection failed code: {results}\n{before_str.lstrip()}{after_str}".rstrip(),
                                raw_secrets,
                            )

                        is_perm_denied = (
                            effective_protocol in ("ssh", "sftp", "mosh")
                            and results == 2
                            and b"permission denied" in (before_bytes + after_bytes).lower()
                        )
                        if (
                            unattended
                            and is_perm_denied
                            and (int(st["cand_idx"]) + 1) < len(runtime_candidates)
                            and remaining > 0
                        ):
                            st["cand_idx"] = int(st["cand_idx"]) + 1
                            next_action = "next_candidate"
                            break

                        transport_error_seen = not is_perm_denied
                        after = _to_str(after_bytes)
                        before = _to_str(before_bytes)
                        readline_val = _to_str(child.readline())
                        if effective_protocol == "mosh":
                            after = self._logclean(after, True)
                            before = self._logclean(before, True)
                            readline_val = self._logclean(readline_val, True)
                        if (
                            effective_protocol == "mosh"
                            and results == 18
                            and ("Nothing received from server" in after or "Nothing received from server" in before)
                        ):
                            err_msg = (
                                after.strip()
                                if "Nothing received from server" in after
                                else before.strip()
                            )
                            return _redact_known_secrets(
                                f"Connection failed code: {results}\n{err_msg} — UDP traffic is blocked or unreachable. Verify firewall rules for UDP ports (60000-61000 or mosh_udp_port).",
                                raw_secrets,
                            )
                        return _redact_known_secrets(
                            f"Connection failed code: {results}\n{before.lstrip()}{after}{readline_val}".rstrip(),
                            raw_secrets,
                        )

                    elif results in eof_indices[effective_protocol]:
                        if results == pw_prompt_idx:
                            after_lower = after_bytes.lower()
                            if b"username" in after_lower and b"password" not in after_lower:
                                should_break, msg_or_action = _handle_username_prompt(child)
                                if msg_or_action == "__NEXT__:next_candidate":
                                    next_action = "next_candidate"
                                    break
                                if msg_or_action is not None:
                                    return msg_or_action
                                if should_break:
                                    break
                                continue

                            if jumphost_hops:
                                prompt_line = normalize_prompt_line(before_bytes, after_bytes)
                                kind, hop_idx = self._disambiguate_prompt(prompt_line)
                                if kind == "conflict":
                                    return str(
                                        sanitize_exception(None, code="JUMP_AUTH_FAILED", secrets=raw_secrets)
                                    )
                                if kind == "hop":
                                    assert hop_idx is not None
                                    if st["target_creds_sent"] or hop_idx < hop_cursor:
                                        return str(
                                            sanitize_exception(None, code="JUMP_AUTH_FAILED", secrets=raw_secrets)
                                        )
                                    hop_cursor = hop_idx
                                    hop_obj = jumphost_hops[hop_idx]
                                    hop_pws = hop_runtime_passwords[hop_idx]
                                    if hop_pw_idx[hop_idx] >= len(hop_pws):
                                        return str(
                                            sanitize_exception(None, code="JUMP_AUTH_FAILED", secrets=raw_secrets)
                                        )
                                    if not tracker.try_acquire_hop(
                                        hop_obj["host"],
                                        hop_obj["port"],
                                        hop_obj["protocol"],
                                        limit=max_hop_host_attempts,
                                    ):
                                        return str(
                                            sanitize_exception(None, code="HOST_BUDGET_EXHAUSTED", secrets=raw_secrets)
                                        )
                                    child.sendline(hop_pws[hop_pw_idx[hop_idx]])
                                    hop_pw_idx[hop_idx] += 1
                                    st["handshake_progressed"] = True
                                    st["last_observed_bytes_len"] = 0
                                    continue
                                if kind == "target":
                                    hop_cursor = len(jumphost_hops)
                                else:
                                    allow_ambig = bool(
                                        tags_dict.get("allow_ambiguous_single_hop_prompt", False)
                                    )
                                    if len(jumphost_hops) > 1 or not allow_ambig:
                                        return str(
                                            sanitize_exception(None, code="JUMP_AUTH_FAILED", secrets=raw_secrets)
                                        )
                                    if not st["target_creds_sent"] and hop_pw_idx[0] < len(hop_runtime_passwords[0]):
                                        hop_obj = jumphost_hops[0]
                                        if not tracker.try_acquire_hop(
                                            hop_obj["host"],
                                            hop_obj["port"],
                                            hop_obj["protocol"],
                                            limit=max_hop_host_attempts,
                                        ):
                                            return str(
                                                sanitize_exception(
                                                    None,
                                                    code="HOST_BUDGET_EXHAUSTED",
                                                    secrets=raw_secrets,
                                                )
                                            )
                                        child.sendline(hop_runtime_passwords[0][hop_pw_idx[0]])
                                        hop_pw_idx[0] += 1
                                        st["handshake_progressed"] = True
                                        st["last_observed_bytes_len"] = 0
                                        continue

                            should_break, msg_or_action = _handle_target_password_prompt(child)
                            if msg_or_action == "__NEXT__:next_candidate":
                                next_action = "next_candidate"
                                break
                            if msg_or_action is not None:
                                return msg_or_action
                            if should_break:
                                break

                        elif suspend_idx is not None and results == suspend_idx:
                            child.sendline("\r")
                            sleep(2)
                            st["last_observed_bytes_len"] = 0
                        elif results_value == pexpect.EOF:
                            norm_buf = (
                                before_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n") + b"\n"
                            )
                            eof_perm_denied = False
                            for err_idx in error_indices[effective_protocol]:
                                err_pat = proto_expects[err_idx]
                                if isinstance(err_pat, re.Pattern):
                                    m = err_pat.search(norm_buf)
                                    if m:
                                        is_pd = (
                                            effective_protocol in ("ssh", "sftp", "mosh")
                                            and err_idx == 2
                                            and b"permission denied" in m.group(0).lower()
                                        )
                                        if (
                                            unattended
                                            and is_pd
                                            and (int(st["cand_idx"]) + 1) < len(runtime_candidates)
                                            and remaining > 0
                                        ):
                                            eof_perm_denied = True
                                            break
                                        transport_error_seen = not is_pd
                                        b_str = _to_str(norm_buf[: m.start()])
                                        a_str = _to_str(m.group(0))
                                        if effective_protocol == "mosh":
                                            b_str = self._logclean(b_str, True)
                                            a_str = self._logclean(a_str, True)
                                        return _redact_known_secrets(
                                            f"Connection failed code: {err_idx}\n{b_str.lstrip()}{a_str}".rstrip(),
                                            raw_secrets,
                                        )
                            if eof_perm_denied:
                                st["cand_idx"] = int(st["cand_idx"]) + 1
                                next_action = "next_candidate"
                                break

                            if (
                                unattended
                                and not transport_error_seen
                                and st["target_creds_sent"]
                            ):
                                if (int(st["cand_idx"]) + 1) < len(runtime_candidates):
                                    eof_adv = int(st["eof_fallback_advances"])
                                    if eof_adv < MAX_EOF_FALLBACK_ADVANCES:
                                        st["eof_fallback_advances"] = eof_adv + 1
                                        st["cand_idx"] = int(st["cand_idx"]) + 1
                                        next_action = "next_candidate"
                                        break
                                    return str(
                                        sanitize_exception(None, code="EOF_BUDGET_EXHAUSTED", secrets=raw_secrets)
                                    )
                                if int(st["eof_fallback_advances"]) > 0:
                                    return str(
                                        sanitize_exception(None, code="AUTH_FAILED", secrets=raw_secrets)
                                    )

                            try:
                                child.sendline()
                            except Exception as exc:  # noqa: BLE001
                                _LOGGER.debug("EOF fallback sendline ignored: %s", type(exc).__name__)
                            break
                        else:
                            cur_cand_now = st["cur_cand"]
                            if (
                                cur_cand_now is not None
                                and cur_cand_now.is_fallback
                                and not st["logged_fallback_this_cand"]
                            ):
                                self._emit_aaa_fallback_log(
                                    logger,
                                    unique=str(self.unique),
                                    host=str(self.host),
                                    protocol=effective_protocol,
                                    user=str(st["cur_user"]),
                                    label=cur_cand_now.label,
                                    secrets=raw_secrets,
                                )
                                self.last_fallback_used = True
                                st["logged_fallback_this_cand"] = True
                            child.sendline()
                            break

                if next_action in ("retry_connect", "next_candidate"):
                    continue

                if isinstance(self.tags, dict) and self.tags.get("post_connect_commands"):
                    cmds = self.tags.get("post_connect_commands")
                    post_commands = [cmds] if isinstance(cmds, str) else cmds
                    for command in post_commands:
                        child.sendline(command)
                        sleep(1)
                child.readline(0)
                self.child = child
                from pexpect import fdpexpect

                self.raw_child = fdpexpect.fdspawn(self.child.child_fd)
                connected_ok = True
                return True
            except Exception as exc:  # noqa: BLE001
                san = sanitize_exception(
                    exc,
                    code="AUTH_FAILED",
                    secrets=raw_secrets,
                    context_label="unexpected connection error",
                )
                return str(san)
            finally:
                if not connected_ok:
                    try:
                        child.logfile_read = None
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.debug("Cleanup logfile_read reset failed: %s", type(exc).__name__)
                    try:
                        child.terminate(force=True)
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.debug("Cleanup terminate failed: %s", type(exc).__name__)
                    if local_mylog is not None and not getattr(local_mylog, "closed", False):
                        try:
                            local_mylog.close()
                        except Exception as exc:  # noqa: BLE001
                            _LOGGER.debug("Cleanup local_mylog close failed: %s", type(exc).__name__)

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
        self.fallback_used = {}
        for n, node_cfg in nodes.items():
            this = node(n, **node_cfg, config=config)
            self.nodelist.append(this)
            setattr(self, n, this)

    @MethodHook
    def _splitlist(self, lst, n):
        #split a list in lists of n members.
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    @MethodHook
    def run(
        self,
        commands,
        vars=None,
        *,
        folder=None,
        prompt=None,
        stdout=None,
        parallel=10,
        timeout=None,
        on_complete=None,
        logger=None,
        connect_timeout=None,
        auth_timeout=None,
        _default_cmd_timeout=10,
        unattended=True,
        attempt_tracker=None,
    ):
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
        if folder is not None:
            args["folder"] = folder
            Path(folder).mkdir(parents=True, exist_ok=True)
        if prompt is not None:
            args["prompt"] = prompt
        if stdout is not None and on_complete is None:
            args["stdout"] = stdout
        if timeout is not None:
            args["timeout"] = timeout
        if connect_timeout is not None:
            args["connect_timeout"] = connect_timeout
        if auth_timeout is not None:
            args["auth_timeout"] = auth_timeout
        if _default_cmd_timeout != 10:
            args["_default_cmd_timeout"] = _default_cmd_timeout
        if unattended is not True:
            args["unattended"] = unattended
        output = {}
        status = {}
        fallback_used = {}
        tasks = []

        def _run_node(node_obj, node_args, callback):
            """Wrapper that runs a node and fires the callback on completion."""
            node_obj.run(**node_args)
            if callback:
                callback(node_obj.unique, node_obj.output, node_obj.status)

        for n in self.nodelist:
            nodesargs[n.unique] = deepcopy(args)
            if attempt_tracker is not None:
                nodesargs[n.unique]["attempt_tracker"] = attempt_tracker
            if vars is not None:
                nodesargs[n.unique]["vars"] = {}
                if "__global__" in vars:
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
            fallback_used[i.unique] = bool(getattr(i, "last_fallback_used", False))
        self.output = output
        self.status = status
        self.fallback_used = fallback_used
        return output

    @MethodHook
    def test(
        self,
        commands,
        expected,
        vars=None,
        *,
        folder=None,
        prompt=None,
        parallel=10,
        timeout=None,
        on_complete=None,
        logger=None,
        connect_timeout=None,
        auth_timeout=None,
        _default_cmd_timeout=10,
        unattended=True,
        attempt_tracker=None,
    ):
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
        if folder is not None:
            args["folder"] = folder
            Path(folder).mkdir(parents=True, exist_ok=True)
        if prompt is not None:
            args["prompt"] = prompt
        if timeout is not None:
            args["timeout"] = timeout
        if connect_timeout is not None:
            args["connect_timeout"] = connect_timeout
        if auth_timeout is not None:
            args["auth_timeout"] = auth_timeout
        if _default_cmd_timeout != 10:
            args["_default_cmd_timeout"] = _default_cmd_timeout
        if unattended is not True:
            args["unattended"] = unattended
        output = {}
        result = {}
        status = {}
        fallback_used = {}
        tasks = []

        def _test_node(node_obj, node_args, callback):
            """Wrapper that runs a node test and fires the callback on completion."""
            node_obj.test(**node_args)
            if callback:
                callback(node_obj.unique, node_obj.output, node_obj.status, node_obj.result)

        for n in self.nodelist:
            nodesargs[n.unique] = deepcopy(args)
            if attempt_tracker is not None:
                nodesargs[n.unique]["attempt_tracker"] = attempt_tracker
            if vars is not None:
                nodesargs[n.unique]["vars"] = {}
                if "__global__" in vars:
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
            fallback_used[i.unique] = bool(getattr(i, "last_fallback_used", False))
        self.output = output
        self.result = result
        self.status = status
        self.fallback_used = fallback_used
        return result

# script
