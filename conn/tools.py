#!/usr/bin/python3
#Imports
import os
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import ast

#Constants

#Variables

#functions and clsses

def encrypt(password, keyfile=None):
    if keyfile is None:
        home = os.path.expanduser("~")
        keyfile = home + '/.config/conn/.osk'
    key = RSA.import_key(open(keyfile).read())
    publickey = key.publickey()
    encryptor = PKCS1_OAEP.new(publickey)
    password = encryptor.encrypt(password.encode("utf-8"))
    return password



