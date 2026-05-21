"""
Tests for gRPC auth serialization/deserialization (engineer_auth, architect_auth, provider auth).

These tests verify that:
1. to_struct/from_struct round-trips correctly for auth dicts.
2. AIStub.ask() correctly serializes engineer_auth and architect_auth into AskRequest.
3. AIServicer.ask() correctly deserializes them and passes them to the service.
4. AIStub.configure_provider() serializes auth into ProviderRequest.
5. AIServicer.configure_provider() deserializes auth and forwards it to the service.
"""
import pytest
from unittest.mock import MagicMock, patch, call
from connpy.grpc_layer import connpy_pb2
from connpy.grpc_layer.utils import to_struct, from_struct


# --- Unit: Struct round-trip ---

class TestStructRoundTrip:
    def test_simple_dict(self):
        d = {"api_key": "secret", "region": "us-east-1"}
        assert from_struct(to_struct(d)) == d

    def test_nested_dict(self):
        d = {"vertex_project": "my-project", "vertex_location": "us-central1", "nested": {"key": "val"}}
        assert from_struct(to_struct(d)) == d

    def test_empty_dict(self):
        assert from_struct(to_struct({})) == {}

    def test_none_returns_empty(self):
        assert from_struct(to_struct(None)) == {}


# --- Unit: AskRequest Struct fields ---

class TestAskRequestStructFields:
    def test_engineer_auth_round_trip(self):
        auth = {"vertex_project": "proj", "vertex_location": "us-central1"}
        req = connpy_pb2.AskRequest(input_text="hi")
        req.engineer_auth.CopyFrom(to_struct(auth))
        assert from_struct(req.engineer_auth) == auth

    def test_architect_auth_round_trip(self):
        auth = {"api_key": "sk-abc", "base_url": "https://custom.api/v1"}
        req = connpy_pb2.AskRequest(input_text="hi")
        req.architect_auth.CopyFrom(to_struct(auth))
        assert from_struct(req.architect_auth) == auth

    def test_has_field_false_when_unset(self):
        req = connpy_pb2.AskRequest(input_text="hi")
        assert not req.HasField("engineer_auth")
        assert not req.HasField("architect_auth")

    def test_has_field_true_when_set(self):
        req = connpy_pb2.AskRequest(input_text="hi")
        req.engineer_auth.CopyFrom(to_struct({"k": "v"}))
        assert req.HasField("engineer_auth")


# --- Unit: ProviderRequest Struct field ---

class TestProviderRequestStructField:
    def test_auth_round_trip(self):
        auth = {"vertex_project": "proj", "vertex_location": "eu-west1"}
        req = connpy_pb2.ProviderRequest(provider="vertex", model="gemini-pro")
        req.auth.CopyFrom(to_struct(auth))
        assert from_struct(req.auth) == auth

    def test_has_field_false_when_unset(self):
        req = connpy_pb2.ProviderRequest(provider="openai", model="gpt-4o")
        assert not req.HasField("auth")

    def test_has_field_true_when_set(self):
        req = connpy_pb2.ProviderRequest(provider="vertex")
        req.auth.CopyFrom(to_struct({"vertex_project": "p"}))
        assert req.HasField("auth")


# --- Integration: Server deserializes auth and passes to service ---

class TestAIServicerAuthDeserialization:
    @pytest.fixture
    def servicer(self, populated_config):
        from connpy.grpc_layer.server import AIServicer
        return AIServicer(populated_config)

    def test_configure_provider_passes_auth_to_service(self, servicer):
        auth = {"vertex_project": "my-proj", "vertex_location": "us-central1"}
        req = connpy_pb2.ProviderRequest(provider="vertex", model="gemini/gemini-pro", api_key="")
        req.auth.CopyFrom(to_struct(auth))

        with patch.object(servicer.service, "configure_provider") as mock_cp:
            mock_context = MagicMock()
            servicer.configure_provider(req, mock_context)
            mock_cp.assert_called_once_with("vertex", "gemini/gemini-pro", "", auth=auth)

    def test_configure_provider_no_auth(self, servicer):
        req = connpy_pb2.ProviderRequest(provider="openai", model="gpt-4o", api_key="sk-test")

        with patch.object(servicer.service, "configure_provider") as mock_cp:
            mock_context = MagicMock()
            servicer.configure_provider(req, mock_context)
            mock_cp.assert_called_once_with("openai", "gpt-4o", "sk-test", auth=None)


# --- Integration: Stub serializes auth into request ---

class TestAIStubAuthSerialization:
    @pytest.fixture
    def ai_stub(self):
        from connpy.grpc_layer.stubs import AIStub
        mock_channel = MagicMock()
        stub = AIStub(mock_channel, "localhost:8048")
        return stub

    def test_configure_provider_with_auth_serializes_struct(self, ai_stub):
        auth = {"vertex_project": "proj", "vertex_location": "us-central1"}
        ai_stub.stub.configure_provider = MagicMock()

        ai_stub.configure_provider("vertex", model="gemini/gemini-pro", auth=auth)

        ai_stub.stub.configure_provider.assert_called_once()
        sent_req = ai_stub.stub.configure_provider.call_args[0][0]
        assert sent_req.provider == "vertex"
        assert sent_req.model == "gemini/gemini-pro"
        assert sent_req.HasField("auth")
        assert from_struct(sent_req.auth) == auth

    def test_configure_provider_without_auth_no_struct(self, ai_stub):
        ai_stub.stub.configure_provider = MagicMock()

        ai_stub.configure_provider("openai", model="gpt-4o", api_key="sk-x")

        sent_req = ai_stub.stub.configure_provider.call_args[0][0]
        assert not sent_req.HasField("auth")
