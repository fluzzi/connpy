from flask import Flask, request, jsonify
from connpy import configfile, node, nodes
from connpy.ai import ai as myai
from waitress import serve
import os
import signal

app = Flask(__name__)
conf = configfile()

PID_FILE1 = "/run/connpy.pid"
PID_FILE2 = "/tmp/connpy.pid"


@app.route("/")
def root():
    return jsonify({
        'message': 'Welcome to Connpy api',
        'version': '1.0',
        'documentation': 'https://fluzzi.github.io/connpy/'
    })

@app.route("/list_nodes", methods=["POST"])
def list_nodes():
    conf = app.custom_config
    case = conf.config["case"]
    try:
        data = request.get_json()
        filter = data["filter"]
        if not case:
            if isinstance(filter, list):
                filter = [item.lower() for item in filter]
            else:
                filter = filter.lower()
        output = conf._getallnodes(filter)
    except:
        output = conf._getallnodes()
    return jsonify(output)

@app.route("/get_nodes", methods=["POST"])
def get_nodes():
    conf = app.custom_config
    case = conf.config["case"]
    try:
        data = request.get_json()
        filter = data["filter"]
        if not case:
            if isinstance(filter, list):
                filter = [item.lower() for item in filter]
            else:
                filter = filter.lower()
        output = conf._getallnodesfull(filter)
    except:
        output = conf._getallnodesfull()
    return jsonify(output)

@app.route("/ask_ai", methods=["POST"])
def ask_ai():
    conf = app.custom_config
    data = request.get_json()
    input = data["input"]
    if "dryrun" in data:
        dryrun = data["dryrun"] 
    else:
        dryrun = False
    ai = myai(conf)
    return ai.ask(input, dryrun)


@app.route("/run_commands", methods=["POST"])
def run_commands():
    conf = app.custom_config
    data = request.get_json()
    case = conf.config["case"]
    mynodes = {}
    args = {}
    try:
        action = data["action"]
        nodelist = data["nodes"]
        args["commands"] = data["commands"]
        if action == "test":
            args["expected"] = data["expected"]
    except KeyError as e:
        error = "'{}' is mandatory".format(e.args[0])
        return({"DataError": error})
    if isinstance(nodelist, list):
        mynodes = conf.getitems(nodelist)
    else:
        if not case:
            nodelist = nodelist.lower()
        if nodelist.startswith("@"):
            mynodes = conf.getitem(nodelist)
        else:
            mynodes[nodelist] = conf.getitem(nodelist)

    mynodes = nodes(mynodes, config=conf)
    try:
        args["vars"] = data["variables"]
    except:
        pass
    try:
        options = data["options"]
        thisoptions = {k: v for k, v in options.items() if k in ["prompt", "parallel", "timeout"]}
        args.update(thisoptions)
    except:
        options = None
    if action == "run":
        output = mynodes.run(**args)
    elif action == "test":
        output = mynodes.test(**args)
    else:
        error = "Wrong action '{}'".format(action)
        return({"DataError": error})
    return output

def stop_api():
    # Read the process ID (pid) from the file
    try:
        with open(PID_FILE1, "r") as f:
            pid = int(f.readline().strip())
            port = int(f.readline().strip())
        PID_FILE=PID_FILE1
    except:
        try:
            with open(PID_FILE2, "r") as f:
                pid = int(f.readline().strip())
                port = int(f.readline().strip())
            PID_FILE=PID_FILE2
        except:
            print("Connpy api server is not running.")
            return 
    # Send a SIGTERM signal to the process
    try:
        os.kill(pid, signal.SIGTERM)
    except:
        pass
    # Delete the PID file
    os.remove(PID_FILE)
    print(f"Server with process ID {pid} stopped.")
    return port

def debug_api(port=8048):
    app.custom_config = configfile()
    app.run(debug=True, port=port)

def start_server(port=8048):
    app.custom_config = configfile()
    serve(app, host='0.0.0.0', port=port)

def start_api(port=8048):
    if os.path.exists(PID_FILE1) or os.path.exists(PID_FILE2):
        print("Connpy server is already running.")
        return
    pid = os.fork()
    if pid == 0:
        start_server(port)
    else:
        try:
            with open(PID_FILE1, "w") as f:
                f.write(str(pid) + "\n" + str(port))
        except:
            try:
                with open(PID_FILE2, "w") as f:
                    f.write(str(pid) + "\n" + str(port))
            except:
                print("Cound't create PID file")
                return
        print(f'Server is running with process ID {pid} in port {port}')

