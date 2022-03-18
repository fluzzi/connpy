#!/usr/bin/python3
import conn
import yaml

conf = conn.configfile("test.yaml")
# ***
# conf._connections_del(id = "zab3mu", folder="teco")
# conf._connections_add(id = "zzztest", folder="teco" ,host = "10.21.96.45", user="sarabada")
# conf._folder_add(folder="zzz")
# conf._folder_add(folder="zzz", subfolder="achus")
# conf._connections_add(id = "zzztec", subfolder="achus", folder="zzz" ,host = "10.21.96.45")
# conf._connections_add(id = "zzztec", subfolder="achus", folder="zzz" ,host = "10.21.96.45", options=" saracatanga")
# conf._folder_del(folder = "zzz", subfolder = "achus")
# conf._profiles_add(id = "test", user = 'tesuser')
# conf._profiles_add(id = "test", user = 'tesuser', protocol = 'telnet')
# conf._profiles_del(id = "test")
# print(yaml.dump(conf.profiles))
# conf.saveconfig("test.yaml")
# ***
# xr=conn.node("xr@home", **conf.connections["home"]["xr"], config=conf)
# ios=conn.node("ios@home", **conf.connections["home"]["ios"], config=conf)
# norman = conn.node("norman@home", **conf.connections["home"]["norman"], config=conf)
# eve = conn.node("eve@home", **conf.connections["home"]["eve"], config=conf)
router228 = conn.node("router228@bbva", **conf.connections["bbva"]["router228"], config=conf)
# router228.interact()
router228.run(["term len 0","show ip int br"])
# xr.run(["term len 0","show ip bgp", "show ip bgp summ"], folder="test")
# ios.run(["term len 0","show ip bgp", "show ip bgp summ"])
# norman.run(["ls -la", "pwd"], folder = "test")
# test = eve.run(["ls -la", "pwd"])
print(router228.output)
# xr.interact()

