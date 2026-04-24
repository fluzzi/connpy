import json
from google.protobuf import json_format
from google.protobuf.struct_pb2 import Struct, Value

def to_value(obj):
    if obj is None:
        v = Value()
        v.null_value = 0
        return v
    json_str = json.dumps(obj)
    v = Value()
    json_format.Parse(json_str, v)
    return v

def from_value(val):
    if not val.HasField("kind"):
        return None
    return json.loads(json_format.MessageToJson(val))

def to_struct(obj):
    if not obj:
        return Struct()
    s = Struct()
    json_format.ParseDict(obj, s)
    return s

def from_struct(struct):
    if not struct:
        return {}
    return json_format.MessageToDict(struct, preserving_proto_field_name=True)
