#!/usr/bin/python3
#Imports
import yaml
import os
import re
from . import tools
from Crypto.PublicKey import RSA


#Constants

#Variables

#functions and clsses

class configfile:
    
    def __init__(self, conf = None):
        home = os.path.expanduser("~")
        self.defaultdir = home + '/.config/conn'
        self.defaultfile = self.defaultdir + '/config.yaml'
        self.defaultkey = self.defaultdir + '/.osk'
        if conf == None:
            self.dir = self.defaultdir
            self.file = self.defaultfile
        else:
            self.dir = os.path.dirname(conf)
            self.file = conf
        if os.path.exists(self.file):
            config = self.loadconfig(self.file)
        else:
            config = self.createconfig(self.file)
        self.config = config["config"]
        self.connections = config["connections"]
        self.profiles = config["profiles"]


    def loadconfig(self, conf):
        ymlconf = open(conf)
        return yaml.load(ymlconf.read(), Loader=yaml.FullLoader)

    def createconfig(self, conf):
        defaultconfig = {'config': {'case': False, 'frun': False, 'idletime': 30}, 'connections': {}, 'profiles': {}}
        if not os.path.exists(conf):
            with open(conf, "w") as f:
                yaml.dump(defaultconfig, f, explicit_start=True)
                f.close()
        ymlconf = open(conf)
        return yaml.load(ymlconf.read(), Loader=yaml.FullLoader)

    def createkey(self, keyfile):
        key = RSA.generate(2048)
        with open('mykey.pem','wb') as f:
            f.write(key.export_key('PEM'))
            f.close()
