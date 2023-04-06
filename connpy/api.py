from flask import Flask, request, jsonify
from connpy import configfile, node, nodes
from waitress import serve
import os
import signal

app = Flask(__name__)
conf = configfile()

PID_FILE = ".connpy_server.pid"


@app.route("/")
def hello():
    return "Welcome to the Connpy API!"

@app.route("/add_node", methods=["POST"])
def add_node():
    node_data = request.get_json()
    unique = node_data["unique"]
    host = node_data["host"]
    user = node_data["user"]
    password = node_data["password"]

    conf.add(unique, host=host, user=user, password=password)

    return jsonify({"message": f"Node {unique} added successfully"})

@app.route("/run_commands", methods=["POST"])
def run_commands():
    conf = app.custom_config
    data = request.get_json()
    unique = data["unique"]
    commands = data["commands"]
    node_data = conf.getitem(unique)
    conn_node = node(unique,**node_data, config=conf)

    output = conn_node.run(commands)

    return output

@app.route("/run_commands_on_nodes", methods=["POST"])
def run_commands_on_nodes():
    data = request.get_json()
    unique_list = data["unique_list"]
    commands = data["commands"]

    nodes_data = {unique: conf.getitem(unique) for unique in unique_list}
    conn_nodes = nodes(nodes_data)

    output = conn_nodes.run(commands)

    return jsonify({"output": output})

def stop_api():
    # Read the process ID (pid) from the file
    with open(PID_FILE, "r") as f:
        pid = int(f.read().strip())

    # Send a SIGTERM signal to the process
    os.kill(pid, signal.SIGTERM)

    # Delete the PID file
    os.remove(PID_FILE)

    print(f"Server with process ID {pid} stopped.")

def start_server(folder):
    folder = folder.rstrip('/')
    file = folder + '/config.json'
    key = folder + '/.osk'
    app.custom_config = configfile(file, key)
    serve(app, host='0.0.0.0', port=8048)

def start_api(folder):
    pid = os.fork()
    if pid == 0:
        start_server(folder)
    else:
        with open(PID_FILE, "w") as f:
            f.write(str(pid))
        print(f'Server is running')

