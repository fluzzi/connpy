#!/usr/bin/env python3
#Imports
import os
import re
import pexpect
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import ast
from time import sleep,time
import datetime
import sys
import threading
from pathlib import Path
from copy import deepcopy
from .hooks import ClassHook, MethodHook
import io

#functions and classes
@ClassHook
class node:
    ''' This class generates a node object. Containts all the information and methods to connect and interact with a device using ssh or telnet.

    ### Attributes:  

        - output (str): Output of the commands you ran with run or test 
                        method.  

        - result(bool): True if expected value is found after running 
                        the commands using test method.

        - status (int): 0 if the method run or test run succesfully.
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

            - protocol (str): Select ssh, telnet, kubectl or docker. Default is ssh.  

            - user     (str): Username to of the node.  

            - config   (obj): Pass the object created with class configfile with 
                              key for decryption and extra configuration if you 
                              are using connection manager.  

            - tags   (dict) : Tags useful for automation and personal porpuse
                              like "os", "prompt" and "screenleght_command"
                              
            - jumphost (str): Reference another node to be used as a jumphost
        '''
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
                    setattr(self,key,config.profiles[profile.group(1)][key])
                except:
                    setattr(self,key,"")
            elif attr[key] == '' and key == "protocol":
                try:
                    setattr(self,key,config.profiles["default"][key])
                except:
                    setattr(self,key,"ssh")
            else: 
                setattr(self,key,attr[key])
        if isinstance(password,list):
            self.password = []
            for i, s in enumerate(password):
                profile = re.search("^@(.*)", password[i])
                if profile and config != '':
                    self.password.append(config.profiles[profile.group(1)]["password"])
        else:
            self.password = [password]
        if self.jumphost != "" and config != '':
            self.jumphost = config.getitem(self.jumphost)
            for key in self.jumphost:
                profile = re.search("^@(.*)", str(self.jumphost[key]))
                if profile:
                    try:
                        self.jumphost[key] = config.profiles[profile.group(1)][key]
                    except:
                        self.jumphost[key] = ""
                elif self.jumphost[key] == '' and key == "protocol":
                    try:
                        self.jumphost[key] = config.profiles["default"][key]
                    except:
                        self.jumphost[key] = "ssh"
            if isinstance(self.jumphost["password"],list):
                jumphost_password = []
                for i, s in enumerate(self.jumphost["password"]):
                    profile = re.search("^@(.*)", self.jumphost["password"][i])
                    if profile:
                        jumphost_password.append(config.profiles[profile.group(1)]["password"])
                self.jumphost["password"] = jumphost_password
            else:
                self.jumphost["password"] = [self.jumphost["password"]]
            if self.jumphost["password"] != [""]:
                self.password = self.jumphost["password"] + self.password

            if self.jumphost["protocol"] == "ssh":
                jumphost_cmd = self.jumphost["protocol"] + " -W %h:%p"
                if self.jumphost["port"] != '':
                    jumphost_cmd = jumphost_cmd + " -p " + self.jumphost["port"]
                if self.jumphost["options"] != '':
                    jumphost_cmd = jumphost_cmd + " " + self.jumphost["options"]
                if self.jumphost["user"] == '':
                    jumphost_cmd = jumphost_cmd + " {}".format(self.jumphost["host"])
                else:
                    jumphost_cmd = jumphost_cmd + " {}".format("@".join([self.jumphost["user"],self.jumphost["host"]]))
                self.jumphost = f"-o ProxyCommand=\"{jumphost_cmd}\""
            else:
                self.jumphost = ""

    @MethodHook
    def _passtx(self, passwords, *, keyfile=None):
        # decrypts passwords, used by other methdos.
        dpass = []
        if keyfile is None:
            keyfile = self.key
        if keyfile is not None:
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
                except:
                    raise ValueError("Missing or corrupted key")
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
        #Remove special ascii characters and other stuff from logfile.
        if var == False:
            t = open(logfile, "r").read()
        else:
            t = logfile
        while t.find("\b") != -1:
            t = re.sub('[^\b]\b', '', t)
        t = t.replace("\n","",1)
        t = t.replace("\a","")
        t = t.replace('\n\n', '\n')
        t = re.sub(r'.\[K', '', t)
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/ ]*[@-~])')
        t = ansi_escape.sub('', t)
        t = t.lstrip(" \n\r")
        t = t.replace("\r","")
        t = t.replace("\x0E","")
        t = t.replace("\x0F","")
        if var == False:
            d = open(logfile, "w")
            d.write(t)
            d.close()
            return
        else:
            return t

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


    @MethodHook
    def interact(self, debug = False):
        '''
        Allow user to interact with the node directly, mostly used by connection manager.

        ### Optional Parameters:  

            - debug (bool): If True, display all the connecting information 
                            before interact. Default False.  
        '''
        connect = self._connect(debug = debug)
        if connect == True:
            size = re.search('columns=([0-9]+).*lines=([0-9]+)',str(os.get_terminal_size()))
            self.child.setwinsize(int(size.group(2)),int(size.group(1)))
            print("Connected to " + self.unique + " at " + self.host + (":" if self.port != '' else '') + self.port + " via: " + self.protocol)
            if 'logfile' in dir(self):
                # Initialize self.mylog
                if not 'mylog' in dir(self):
                    self.mylog = io.BytesIO()
                self.child.logfile_read = self.mylog
                
                # Start the _savelog thread
                log_thread = threading.Thread(target=self._savelog)
                log_thread.daemon = True
                log_thread.start()
            if 'missingtext' in dir(self):
                print(self.child.after.decode(), end='')
            if self.idletime > 0:
                x = threading.Thread(target=self._keepalive)
                x.daemon = True
                x.start()
            if debug:
                print(self.mylog.getvalue().decode())
            self.child.interact(input_filter=self._filter)
            if 'logfile' in dir(self):
                with open(self.logfile, "w") as f:
                    f.write(self._logclean(self.mylog.getvalue().decode(), True))

        else:
            print(connect)
            exit(1)

    @MethodHook
    def run(self, commands, vars = None,*, folder = '', prompt = r'>$|#$|\$$|>.$|#.$|\$.$', stdout = False, timeout = 10):
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
        connect = self._connect(timeout = timeout)
        now = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
        if connect == True:
            # Attempt to set the terminal size
            try:
                self.child.setwinsize(65535, 65535)
            except Exception:
                try:
                    self.child.setwinsize(10000, 10000)
                except Exception:
                    pass
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
                    c = c.format(**vars)
                result = self.child.expect(expects, timeout = timeout)
                self.child.sendline(c)
                if result == 2:
                    break
            if not result == 2:
                result = self.child.expect(expects, timeout = timeout)
            self.child.close()
            output = self._logclean(self.mylog.getvalue().decode(), True)
            if stdout == True:
                print(output)
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
            if stdout == True:
                print(connect)
            if folder != '':
                with open(folder + "/" + self.unique + "_" + now + ".txt", "w") as f:
                    f.write(connect)
                    f.close()
            return connect

    @MethodHook
    def test(self, commands, expected, vars = None,*, prompt = r'>$|#$|\$$|>.$|#.$|\$.$', timeout = 10):
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
        connect = self._connect(timeout = timeout)
        if connect == True:
            # Attempt to set the terminal size
            try:
                self.child.setwinsize(65535, 65535)
            except Exception:
                try:
                    self.child.setwinsize(10000, 10000)
                except Exception:
                    pass
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
                    c = c.format(**vars)
                result = self.child.expect(expects, timeout = timeout)
                self.child.sendline(c)
                if result == 2:
                    break
            if not result == 2:
                result = self.child.expect(expects, timeout = timeout)
            self.child.close()
            output = self._logclean(self.mylog.getvalue().decode(), True)
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
                    newpattern = f".*({updatedprompt}).*{e}.*"
                    cleaned_output = output
                    cleaned_output = re.sub(newpattern, '', cleaned_output)
                    if e in cleaned_output:
                        self.result[e] = True
                    else:
                        self.result[e]= False
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
    def _generate_ssh_sftp_cmd(self):
        cmd = self.protocol
        if self.idletime > 0:
            cmd += " -o ServerAliveInterval=" + str(self.idletime)
        if self.port:
            if self.protocol == "ssh":
                cmd += " -p " + self.port
            elif self.protocol == "sftp":
                cmd += " -P " + self.port
        if self.options:
            cmd += " " + self.options
        if self.jumphost:
            cmd += " " + self.jumphost
        user_host = f"{self.user}@{self.host}" if self.user else self.host
        cmd += f" {user_host}"
        return cmd

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
    def _get_cmd(self):
        if self.protocol in ["ssh", "sftp"]:
            return self._generate_ssh_sftp_cmd()
        elif self.protocol == "telnet":
            return self._generate_telnet_cmd()
        elif self.protocol == "kubectl":
            return self._generate_kube_cmd()
        elif self.protocol == "docker":
            return self._generate_docker_cmd()
        else:
            raise ValueError(f"Invalid protocol: {self.protocol}")

    @MethodHook
    def _connect(self, debug=False, timeout=10, max_attempts=3):
        cmd = self._get_cmd()
        passwords = self._passtx(self.password) if self.password[0] else []
        if self.logs != '':
            self.logfile = self._logfile()
        default_prompt = r'>$|#$|\$$|>.$|#.$|\$.$'
        prompt = self.tags.get("prompt", default_prompt) if isinstance(self.tags, dict) else default_prompt
        password_prompt = '[p|P]assword:|[u|U]sername:' if self.protocol != 'telnet' else '[p|P]assword:'

        expects = {
            "ssh": ['yes/no', 'refused', 'supported', 'Invalid|[u|U]sage: ssh', 'ssh-keygen.*\"', 'timeout|timed.out', 'unavailable', 'closed', password_prompt, prompt, 'suspend', pexpect.EOF, pexpect.TIMEOUT, "No route to host", "resolve hostname", "no matching", "[b|B]ad (owner|permissions)"],
            "sftp": ['yes/no', 'refused', 'supported', 'Invalid|[u|U]sage: sftp', 'ssh-keygen.*\"', 'timeout|timed.out', 'unavailable', 'closed', password_prompt, prompt, 'suspend', pexpect.EOF, pexpect.TIMEOUT, "No route to host", "resolve hostname", "no matching", "[b|B]ad (owner|permissions)"],
            "telnet": ['[u|U]sername:', 'refused', 'supported', 'invalid|unrecognized option', 'ssh-keygen.*\"', 'timeout|timed.out', 'unavailable', 'closed', password_prompt, prompt, 'suspend', pexpect.EOF, pexpect.TIMEOUT, "No route to host", "resolve hostname", "no matching", "[b|B]ad (owner|permissions)"],
            "kubectl": ['[u|U]sername:', '[r|R]efused', '[E|e]rror', 'DEPRECATED', pexpect.TIMEOUT, password_prompt, prompt, pexpect.EOF, "expired|invalid"],
            "docker": ['[u|U]sername:', 'Cannot', '[E|e]rror', 'failed', 'not a docker command', 'unknown', 'unable to resolve', pexpect.TIMEOUT, password_prompt, prompt, pexpect.EOF]
        }

        error_indices = {
            "ssh": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16],
            "sftp": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16],
            "telnet": [1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16],
            "kubectl": [1, 2, 3, 4, 8],  # Define error indices for kube
            "docker": [1, 2, 3, 4, 5, 6, 7]  # Define error indices for docker
        }

        eof_indices = {
            "ssh": [8, 9, 10, 11],
            "sftp": [8, 9, 10, 11],
            "telnet": [8, 9, 10, 11],
            "kubectl": [5, 6, 7],  # Define eof indices for kube
            "docker": [8, 9, 10]  # Define eof indices for docker
        }

        initial_indices = {
            "ssh": [0],
            "sftp": [0],
            "telnet": [0],
            "kubectl": [0],  # Define special indices for kube
            "docker": [0]  # Define special indices for docker
        }

        attempts = 1
        while attempts <= max_attempts:
            child = pexpect.spawn(cmd)
            if debug:
                print(cmd)
                self.mylog = io.BytesIO()
                child.logfile_read = self.mylog

            endloop = False
            for i in range(len(passwords) if passwords else 1):
                while True:
                    results = child.expect(expects[self.protocol], timeout=timeout)
                    results_value = expects[self.protocol][results]
                    
                    if results in initial_indices[self.protocol]:
                        if self.protocol in ["ssh", "sftp"]:
                            child.sendline('yes')
                        elif self.protocol in ["telnet", "kubectl", "docker"]:
                            if self.user:
                                child.sendline(self.user)
                            else:
                                self.missingtext = True
                                break
                    
                    elif results in error_indices[self.protocol]:
                        child.terminate()
                        if results_value == pexpect.TIMEOUT and attempts != max_attempts:
                            attempts += 1
                            endloop = True
                            break
                        else:
                            after = "Connection timeout" if results_value == pexpect.TIMEOUT else child.after.decode()
                            return f"Connection failed code: {results}\n{child.before.decode().lstrip()}{after}{child.readline().decode()}".rstrip()
                    
                    elif results in eof_indices[self.protocol]:
                        if results_value == password_prompt:
                            if passwords:
                                child.sendline(passwords[i])
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

        child.readline(0)
        self.child = child
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
                           0 if method run or test ended succesfully.
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
    def run(self, commands, vars = None,*, folder = None, prompt = None, stdout = None, parallel = 10, timeout = None):
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
        if stdout != None:
            args["stdout"] = stdout
        if timeout != None:
            args["timeout"] = timeout
        output = {}
        status = {}
        tasks = []
        for n in self.nodelist:
            nodesargs[n.unique] = deepcopy(args)
            if vars != None:
                nodesargs[n.unique]["vars"] = {}
                if "__global__" in vars.keys():
                    nodesargs[n.unique]["vars"].update(vars["__global__"])
                if n.unique in vars.keys():
                    nodesargs[n.unique]["vars"].update(vars[n.unique])
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
    def test(self, commands, expected, vars = None,*, prompt = None, parallel = 10, timeout = None):
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

        ### Returns:  

            dict: Dictionary formed by nodes unique as keys, value is True if 
                  expected value is found after running the commands, False 
                  if prompt is found before.

        '''
        args = {}
        nodesargs = {}
        args["commands"] = commands
        args["expected"] = expected
        if prompt != None:
            args["prompt"] = prompt
        if timeout != None:
            args["timeout"] = timeout
        output = {}
        result = {}
        status = {}
        tasks = []
        for n in self.nodelist:
            nodesargs[n.unique] = deepcopy(args)
            if vars != None:
                nodesargs[n.unique]["vars"] = {}
                if "__global__" in vars.keys():
                    nodesargs[n.unique]["vars"].update(vars["__global__"])
                if n.unique in vars.keys():
                    nodesargs[n.unique]["vars"].update(vars[n.unique])
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
