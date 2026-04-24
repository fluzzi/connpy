import sys
import os

# gRPC generated files use absolute imports that assume their directory is in sys.path.
# We add this directory to sys.path to allow imports like 'import connpy_pb2' to succeed.
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
