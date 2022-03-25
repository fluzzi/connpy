#!/usr/bin/python3
import conn

conf = conn.configfile()
# ***
# conf._connections_del(id = "zab3mu", folder="teco")
# conf._connections_add(id = "zzztest", folder="teco" ,host = "10.21.96.45", user="sarabada")
# conf._connections_add(id = "layer1",host = "10.21.96.45", user="sarabada")
# conf._folder_add(folder="zzz")
# conf._folder_add(folder="home", subfolder="achus")
# conf._connections_add(id = "layer3", folder="home", subfolder="achus",host = "10.21.96.45", user="sarabada")
# conf._connections_add(id = "zzztec", subfolder="achus", folder="zzz" ,host = "10.21.96.45")
# conf._connections_add(id = "zzztec", subfolder="achus", folder="zzz" ,host = "10.21.96.45", options=" saracatanga")
# conf._folder_del(folder = "zzz", subfolder = "achus")
# conf._profiles_add(id = "test", user = 'tesuser')
# conf._profiles_add(id = "test", user = 'tesuser', protocol = 'telnet')
# conf._profiles_del(id = "test")
# print(yaml.dump(conf.profiles))
# conf.saveconfig("test.yaml")
# ***
# test = conn.node("test", "10.21.96.45")
# xr=conn.node("xr@home", **conf.getitem("xr@home"), config=conf)
# ios=conn.node("ios@home", **conf.connections["home"]["ios"], config=conf)
# norman = conn.node("norman@home", **conf.connections["home"]["norman"], config=conf)
# eve = conn.node("eve@home", **conf.connections["home"]["eve"], config=conf)
# router228 = conn.node("router228@bbva", **conf.connections["bbva"]["router228"], config=conf)
# router228.interact()
# router228.run(["term len 0","show ip int br"])
# xroutput = xr.run("show run")
# ios.run("show run", folder=".",stdout=True)
# norman.run(["ls -la", "pwd"])
# test = eve.run(["ls -la", "pwd"])
# print(norman.output)
# print(xroutput)
# xr.interact()
# test.interact()
# ***
conn.connapp(conf, conn.node)
# ***
# list = ["xr@home","ios@home","router228@bbva","router142@bbva"]
# for i in list:
    # data = conf.getitem(i)
    # routeri = conn.node(i,**data,config=conf)
    # routeri.run(["term len 0","show run"], folder="test")

