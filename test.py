#!/usr/bin/python3
import conn

conf = conn.configfile()
conn1=conn.node("pruebas@conn", **conf.connections["home"]["xr"])
conn1.connect("interact")

