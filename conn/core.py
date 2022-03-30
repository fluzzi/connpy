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

#functions and classes

class node:
    def __init__(self, unique, host, options='', logs='', password='', port='', protocol='', type='', user='', config=''):
        if config == '':
            self.idletime = 0
            self.key = None
        else:
            self.idletime = config.config["idletime"]
            self.key = config.key
        self.unique = unique
        self.id = self.unique.split("@")[0]
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
        if logfile == None:
            logfile = self.logs
        logfile = logfile.replace("${id}", self.id)
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
        if var == False:
            t = open(logfile, "r").read()
        else:
            t = logfile
        t = t.replace("\n","",1).replace("\a","")
        t = t.replace('\n\n', '\n')
        t = re.sub('.\[K', '', t)
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

    def run(self, commands,*, folder = '', prompt = '>$|#$|\$$|>.$|#.$|\$.$', stdout = False):
        connect = self._connect()
        if connect == True:
            expects = [prompt, pexpect.EOF]
            output = ''
            if isinstance(commands, list):
                for c in commands:
                    result = self.child.expect(expects)
                    self.child.sendline(c)
                    match result:
                        case 0:
                            output = output + self.child.before.decode() + self.child.after.decode()
                        case 1:
                            output = output + self.child.before.decode()
            else:
                result = self.child.expect(expects)
                self.child.sendline(commands)
                match result:
                    case 0:
                        output = output + self.child.before.decode() + self.child.after.decode()
                    case 1:
                        output = output + self.child.before.decode()
            result = self.child.expect(expects)
            match result:
                case 0:
                    output = output + self.child.before.decode() + self.child.after.decode()
                case 1:
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

    def test(self, commands, expected, *, prompt = '>$|#$|\$$|>.$|#.$|\$.$'):
        connect = self._connect()
        if connect == True:
            expects = [prompt, pexpect.EOF]
            output = ''
            if isinstance(commands, list):
                for c in commands:
                    result = self.child.expect(expects)
                    self.child.sendline(c)
                    match result:
                        case 0:
                            output = output + self.child.before.decode() + self.child.after.decode()
                        case 1:
                            output = output + self.child.before.decode()
            else:
                self.child.expect(expects)
                self.child.sendline(commands)
                output = output + self.child.before.decode() + self.child.after.decode()
            expects = [expected, prompt, pexpect.EOF]
            results = self.child.expect(expects)
            match results:
                case 0:
                    self.child.close()
                    self.test = True
                    output = output + self.child.before.decode() + self.child.after.decode()
                    output = output.lstrip()
                    self.output = output
                    return True
                case 1 | 2:
                    self.child.close()
                    self.test = False
                    if results == 1:
                        output = output + self.child.before.decode() + self.child.after.decode()
                    elif results == 2:
                        output = output + self.child.before.decode()
                    output = output.lstrip()
                    self.output = output
                    return False
        else:
            self.test = None
            self.output = connect
            return connect

    def _connect(self, debug = False):
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
            expects = ['yes/no', 'refused', 'supported', 'cipher', 'sage', 'timeout', 'unavailable', 'closed', '[p|P]assword:|[u|U]sername:', '>$|#$|\$$|>.$|#.$|\$.$', 'suspend', pexpect.EOF, "No route to host", "resolve hostname"]
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
            expects = ['[u|U]sername:', 'refused', 'supported', 'cipher', 'sage', 'timeout', 'unavailable', 'closed', '[p|P]assword:', '>$|#$|\$$|>.$|#.$|\$.$', 'suspend', pexpect.EOF, "No route to host", "resolve hostname"]
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
                match results:
                    case 0:
                        if self.protocol == "ssh":
                            child.sendline('yes')
                        elif self.protocol == "telnet":
                            if self.user != '':
                                child.sendline(self.user)
                            else:
                                self.missingtext = True
                                break
                    case 1 | 2 | 3 | 4 | 5 | 6 | 7 | 12 | 13:
                        child.close()
                        return "Connection failed code:" + str(results)
                    case 8:
                        if len(passwords) > 0:
                            child.sendline(passwords[i])
                        else:
                            self.missingtext = True
                        break
                    case 9 | 11:
                        endloop = True
                        child.sendline()
                        break
                    case 10:
                        child.sendline("\r")
                        sleep(2)
            if endloop:
                break
        child.readline(0)
        self.child = child
        return True


# script
