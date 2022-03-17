#!/usr/bin/python3
#Imports
import yaml
import os
import re
import pexpect
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import ast
from time import sleep
import datetime
import sys
from conn import tools
from .configfile import configfile

#Constants

#Variables

#functions and clsses

class node:
    def __init__(self, unique, host, options='', logs='', password='', port='', protocol='', type='', user=''):
        try:
            config = configfile()
            self.idletime = config.config["idletime"]
        except:
            config = {}
            self.idletime = 0
        self.unique = unique
        self.id = self.unique.split("@")[0]
        attr = {"host": host, "logs": logs, "options":options, "port": port, "protocol": protocol, "user": user}
        for key in attr:
            profile = re.search("^@(.*)", attr[key])
            if profile:
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
                if profile:
                    self.password.append(config.profiles[profile.group(1)]["password"])
        else:
            self.password = [str(password)]

    def __passtx(self, passwords, *, keyfile=None):
        if keyfile is None:
            home = os.path.expanduser("~")
            keyfile = home + '/.config/conn/.osk'
        key = RSA.import_key(open(keyfile).read())
        publickey = key.publickey()
        encryptor = PKCS1_OAEP.new(publickey)
        decryptor = PKCS1_OAEP.new(key)
        dpass = []
        for passwd in passwords:
            decrypted = decryptor.decrypt(ast.literal_eval(str(passwd))).decode("utf-8")
            dpass.append(decrypted)
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

    def _logclean(self, logfile):
        t = open(logfile, "r").read().replace("\n","",1).replace("\a","")
        t = t.replace('\n\n', '\n')
        t = re.sub('.\[K', '', t)
        while True:
            tb = re.sub('.\b', '', t, count=1)
            if len(t) == len(tb):
                break
            t = tb
        d = open(logfile, "w")
        d.write(t)
        d.close()
        return

    def connect(self, mode = None):
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
                logfile = self._logfile()
            if self.password[0] != '':
                passwords = self.__passtx(self.password)
            else:
                passwords = []
            expects = ['yes/no', 'refused', 'supported', 'cipher', 'sage', 'timeout', 'unavailable', 'closed', '[p|P]assword:|[u|U]sername:', '>$|#$|\$', 'suspend']
        elif self.protocol == "telnet":
            cmd = "telnet " + self.host
            if self.port != '':
                cmd = cmd + " " + self.port
            if self.options != '':
                cmd = cmd + " " + self.options
            if self.logs != '':
                logfile = self._logfile()
            if self.password[0] != '':
                passwords = self.__passtx(self.password)
            else:
                passwords = []
            expects = ['[u|U]sername:', 'refused', 'supported', 'cipher', 'sage', 'timeout', 'unavailable', 'closed', '[p|P]assword:', '>$|#$|\$', 'suspend']
        else:
            print("Invalid protocol: " + self.protocol)
            return
        child = pexpect.spawn(cmd)
        if 'logfile' in locals():
            child.logfile_read = open(logfile, "wb")
            # child.logfile_read = sys.stdout.buffer
        print("Connecting to " + self.unique + " at " + self.host + (":" if self.port != '' else '') + self.port + " via: " + self.protocol)
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
                                print(child.after.decode(), end='')
                                break
                    case 1 | 2 | 3 | 4 | 5 | 6 |7:
                        print("Connection failed code:" + str(results))
                        child.close()
                        return
                    case 8:
                        if len(passwords) > 0:
                            child.sendline(passwords[i])
                        else:
                            print(child.after.decode(), end='')
                        break
                    case 9:
                        endloop = True
                        child.sendline()
                        break
                    case 10:
                        child.sendline("\r")
                        sleep(2)
            if endloop:
                break
        child.readline(0)
        if mode == "interact":
            child.interact()
            if "logfile" in locals():
                self._logclean(logfile)

# script
