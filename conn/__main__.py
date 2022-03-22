#!/usr/bin/env python3
import sys
import conn

def main():
    conf = conn.configfile()
    conn.connapp(conf, conn.node)

if __name__ == '__main__':
    sys.exit(main())
