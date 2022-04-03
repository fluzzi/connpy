#!/usr/bin/env python3
#Imports
import os
import re
import pexpect
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import ast
from time import sleep
import datetime
import sys
import threading

#functions and classes

class node:
    ''' This class generates a node object. Containts all the information and methods to connect and interact with a device using ssh or telnet.

    ### Attributes:  

        - output (str): Output of the commands you ran with run or test 
                        method.  

        - result(bool): True if expected value is found after running 
                        the commands using test method.
        '''
    
    def __init__(self, unique, host, options='', logs='', password='', port='', protocol='', user='', config=''):
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

            - protocol (str): Select ssh or telnet. Default is ssh.  

            - user     (str): Username to of the node.  

            - config   (obj): Pass the object created with class configfile with 
                              key for decryption and extra configuration if you 
                              are using connection manager.  
        '''
        if config == '':
            self.idletime = 0
            self.key = None
        else:
            self.idletime = config.config["idletime"]
            self.key = config.key
        self.unique = unique
        attr = {"host": host, "logs": logs, "options":options, "port": port, "protocol": protocol, "user": user}
        for key in attr:
            profile = re.search("^@(.*)", attr[key])
            if profile and config != '':
                setattr(self,key,config.profiles[profile.group(1)][key])
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

    def __passtx(self, passwords, *, keyfile=None):
        # decrypts passwords, used by other methdos.
        dpass = []
        if keyfile is None:
            keyfile = self.key
        if keyfile is not None:
            key = RSA.import_key(open(keyfile).read())
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

    def _logclean(self, logfile, var = False):
        #Remove special ascii characters and other stuff from logfile.
        if var == False:
            t = open(logfile, "r").read()
        else:
            t = logfile
        t = t.replace("\n","",1).replace("\a","")
        t = t.replace('\n\n', '\n')
        t = re.sub(r'.\[K', '', t)
        while True:
            tb = re.sub('.\b', '', t, count=1)
            if len(t) == len(tb):
                break
            t = tb
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/ ]*[@-~])')
        t = ansi_escape.sub('', t)
        if var == False:
            d = open(logfile, "w")
            d.write(t)
            d.close()
            return
        else:
            return t

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
                self.child.logfile_read = open(self.logfile, "wb")
            elif debug:
                self.child.logfile_read = None
            if 'missingtext' in dir(self):
                print(self.child.after.decode(), end='')
            self.child.interact()
            if "logfile" in dir(self) and not debug:
                self._logclean(self.logfile)
        else:
            print(connect)
            exit(1)

    def run(self, commands,*, folder = '', prompt = r'>$|#$|\$$|>.$|#.$|\$.$', stdout = False):
        '''
        Run a command or list of commands on the node and return the output.

        ### Parameters:  

            - commands (str/list): Commands to run on the node. Should be 
                                   str or a list of str.

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

        ### Returns:  

            str: Output of the commands you ran on the node.

        '''
        connect = self._connect()
        if connect == True:
            expects = [prompt, pexpect.EOF]
            output = ''
            if isinstance(commands, list):
                for c in commands:
                    result = self.child.expect(expects)
                    self.child.sendline(c)
                    if result == 0:
                        output = output + self.child.before.decode() + self.child.after.decode()
                    if result == 1:
                        output = output + self.child.before.decode()
            else:
                result = self.child.expect(expects)
                self.child.sendline(commands)
                if result == 0:
                    output = output + self.child.before.decode() + self.child.after.decode()
                if result == 1:
                    output = output + self.child.before.decode()
            result = self.child.expect(expects)
            if result == 0:
                output = output + self.child.before.decode() + self.child.after.decode()
            if result == 1:
                output = output + self.child.before.decode()
            self.child.close()
            output = output.lstrip()
            if stdout == True:
                print(output)
            if folder != '':
                with open(folder + "/" + self.unique, "w") as f:
                    f.write(output)
                    f.close()
                    self._logclean(folder + "/" + self.unique)
            self.output = output
            return output
        else:
            self.output = connect
            return connect

    def test(self, commands, expected, *, prompt = r'>$|#$|\$$|>.$|#.$|\$.$'):
        '''
        Run a command or list of commands on the node, then check if expected value appears on the output after the last command.

        ### Parameters:  

            - commands (str/list): Commands to run on the node. Should be
                                   str or list of str.  

            - expected (str)     : Expected text to appear after running 
                                   all the commands on the node.

        ### Optional Named Parameters: 

            - prompt (str): Prompt to be expected after a command is finished
                            running. Usually linux uses  ">" or EOF while 
                            routers use ">" or "#". The default value should 
                            work for most nodes. Change it if your connection 
                            need some special symbol.

        ### Returns: 
            bool: true if expected value is found after running the commands 
                  false if prompt is found before.

        '''
        connect = self._connect()
        if connect == True:
            expects = [prompt, pexpect.EOF]
            output = ''
            if isinstance(commands, list):
                for c in commands:
                    result = self.child.expect(expects)
                    self.child.sendline(c)
                    if result == 0:
                        output = output + self.child.before.decode() + self.child.after.decode()
                    if result == 1:
                        output = output + self.child.before.decode()
            else:
                self.child.expect(expects)
                self.child.sendline(commands)
                output = output + self.child.before.decode() + self.child.after.decode()
            expects = [expected, prompt, pexpect.EOF]
            results = self.child.expect(expects)
            if results == 0:
                self.child.close()
                self.result = True
                output = output + self.child.before.decode() + self.child.after.decode()
                output = output.lstrip()
                self.output = output
                return True
            if results in [1, 2]:
                self.child.close()
                self.result = False
                if results == 1:
                    output = output + self.child.before.decode() + self.child.after.decode()
                elif results == 2:
                    output = output + self.child.before.decode()
                output = output.lstrip()
                self.output = output
                return False
        else:
            self.result = None
            self.output = connect
            return connect

    def _connect(self, debug = False):
        # Method to connect to the node, it parse all the information, create the ssh/telnet command and login to the node.
        if self.protocol == "ssh":
            cmd = "ssh"
            if self.idletime > 0:
                cmd = cmd + " -o ServerAliveInterval=" + str(self.idletime)
            if self.user == '':
                cmd = cmd + " -t {}".format(self.host)
            else:
                cmd = cmd + " -t {}".format("@".join([self.user,self.host]))
            if self.port != '':
                cmd = cmd + " -p " + self.port
            if self.options != '':
                cmd = cmd + " " + self.options
            if self.logs != '':
                self.logfile = self._logfile()
            if self.password[0] != '':
                passwords = self.__passtx(self.password)
            else:
                passwords = []
            expects = ['yes/no', 'refused', 'supported', 'cipher', 'sage', 'timeout', 'unavailable', 'closed', '[p|P]assword:|[u|U]sername:', r'>$|#$|\$$|>.$|#.$|\$.$', 'suspend', pexpect.EOF, "No route to host", "resolve hostname", "no matching host key"]
        elif self.protocol == "telnet":
            cmd = "telnet " + self.host
            if self.port != '':
                cmd = cmd + " " + self.port
            if self.options != '':
                cmd = cmd + " " + self.options
            if self.logs != '':
                self.logfile = self._logfile()
            if self.password[0] != '':
                passwords = self.__passtx(self.password)
            else:
                passwords = []
            expects = ['[u|U]sername:', 'refused', 'supported', 'cipher', 'sage', 'timeout', 'unavailable', 'closed', '[p|P]assword:', r'>$|#$|\$$|>.$|#.$|\$.$', 'suspend', pexpect.EOF, "No route to host", "resolve hostname", "no matching host key"]
        else:
            raise ValueError("Invalid protocol: " + self.protocol)
        child = pexpect.spawn(cmd)
        if debug:
            child.logfile_read = sys.stdout.buffer
        if len(passwords) > 0:
            loops = len(passwords)
        else:
            loops = 1
        endloop = False
        for i in range(0, loops):
            while True:
                results = child.expect(expects)
                if results == 0:
                    if self.protocol == "ssh":
                        child.sendline('yes')
                    elif self.protocol == "telnet":
                        if self.user != '':
                            child.sendline(self.user)
                        else:
                            self.missingtext = True
                            break
                if results in  [1, 2, 3, 4, 5, 6, 7, 12, 13, 14]:
                    child.close()
                    return "Connection failed code:" + str(results)
                if results == 8:
                    if len(passwords) > 0:
                        child.sendline(passwords[i])
                    else:
                        self.missingtext = True
                    break
                if results in [9, 11]:
                    endloop = True
                    child.sendline()
                    break
                if results == 10:
                    child.sendline("\r")
                    sleep(2)
            if endloop:
                break
        child.readline(0)
        self.child = child
        return True

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

    
    def _splitlist(self, lst, n):
        #split a list in lists of n members.
        for i in range(0, len(lst), n):
            yield lst[i:i + n]


    def run(self, commands,*, folder = None, prompt = None, stdout = None, parallel = 10):
        '''
        Run a command or list of commands on all the nodes in nodelist.

        ### Parameters:  

            commands (str/list): Commands to run on the node. Should be str or 
                                 list of str.

        ### Optional Named Parameters:  

            folder   (str): Path where output log should be stored, leave empty 
                            to disable logging.  

            prompt   (str): Prompt to be expected after a command is finished 
                            running. Usually linux uses  ">" or EOF while routers 
                            use ">" or "#". The default value should work for 
                            most nodes. Change it if your connection need some 
                            special symbol.  

            stdout  (bool): Set True to send the command output to stdout. 
                            Default False.  

            parallel (int): Number of nodes to run the commands simultaneously. 
                            Default is 10, if there are more nodes that this 
                            value, nodes are groups in groups with max this 
                            number of members.

        ###Returns:  

            dict: Dictionary formed by nodes unique as keys, Output of the 
                  commands you ran on the node as value.

        '''
        args = {}
        args["commands"] = commands
        if folder != None:
            args["folder"] = folder
        if prompt != None:
            args["prompt"] = prompt
        if stdout != None:
            args["stdout"] = stdout
        output = {}
        tasks = []
        for n in self.nodelist:
            tasks.append(threading.Thread(target=n.run, kwargs=args))
        taskslist = list(self._splitlist(tasks, parallel))
        for t in taskslist:
            for i in t:
                i.start()
            for i in t:
                i.join()
        for i in self.nodelist:
            output[i.unique] = i.output
        self.output = output
        return output

    def test(self, commands, expected, *, prompt = None, parallel = 10):
        '''
        Run a command or list of commands on all the nodes in nodelist, then check if expected value appears on the output after the last command.

        ### Parameters:  

            commands (str/list): Commands to run on the node. Should be str or 
                                 list of str.  

            expected (str)     : Expected text to appear after running all the 
                                 commands on the node.

        ### Optional Named Parameters:  

            prompt (str): Prompt to be expected after a command is finished 
                          running. Usually linux uses  ">" or EOF while 
                          routers use ">" or "#". The default value should 
                          work for most nodes. Change it if your connection 
                          need some special symbol.

        ### Returns:  

            dict: Dictionary formed by nodes unique as keys, value is True if 
                  expected value is found after running the commands, False 
                  if prompt is found before.

        '''
        args = {}
        args["commands"] = commands
        args["expected"] = expected
        if prompt != None:
            args["prompt"] = prompt
        output = {}
        result = {}
        tasks = []
        for n in self.nodelist:
            tasks.append(threading.Thread(target=n.test, kwargs=args))
        taskslist = list(self._splitlist(tasks, parallel))
        for t in taskslist:
            for i in t:
                i.start()
            for i in t:
                i.join()
        for i in self.nodelist:
            result[i.unique] = i.result
            output[i.unique] = i.output
        self.output = output
        self.result = result
        return result

# script
