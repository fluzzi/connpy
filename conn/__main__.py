#!/usr/bin/env python3
import sys
from connapp import connapp
from configfile import configfile
from core import node
# from conn import *

def main():
    conf = configfile()
    connapp(conf, node)

if __name__ == '__main__':
    sys.exit(main())
