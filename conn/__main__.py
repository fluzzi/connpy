#!/usr/bin/env python3
import sys
from conn import *

def main():
    conf = configfile()
    connapp(conf)

if __name__ == '__main__':
    sys.exit(main())
