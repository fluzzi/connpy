from .base import BaseService
from .exceptions import InvalidConfigurationError

class AIService(BaseService):
    """Business logic for interacting with AI agents and LLM configurations."""

    def ask(self, input_text, dryrun=False, chat_history=None, status=None, debug=False, session_id=None, console=None, chunk_callback=None, confirm_handler=None, trust=False, **overrides):
        """Send a prompt to the AI agent."""
        from connpy.ai import ai
        agent = ai(self.config, console=console, confirm_handler=confirm_handler, trust=trust, **overrides)
        return agent.ask(input_text, dryrun, chat_history, status=status, debug=debug, session_id=session_id, chunk_callback=chunk_callback)


    def confirm(self, input_text, console=None):
        """Ask for a safe confirmation of an action."""
        from connpy.ai import ai
        agent = ai(self.config, console=console)
        return agent.confirm(input_text)


    def list_sessions(self):
        """Return a list of all saved AI sessions."""
        from connpy.ai import ai
        agent = ai(self.config)
        return agent._get_sessions()

    def delete_session(self, session_id):
        """Delete an AI session by ID."""
        import os
        sessions_dir = os.path.join(self.config.defaultdir, "ai_sessions")
        path = os.path.join(sessions_dir, f"{session_id}.json")
        if os.path.exists(path):
            os.remove(path)
        else:
            raise InvalidConfigurationError(f"Session '{session_id}' not found.")

    def configure_provider(self, provider, model=None, api_key=None):
        """Update AI provider settings in the configuration."""
        settings = self.config.config.get("ai", {})
        if model:
            settings[f"{provider}_model"] = model
        if api_key:
            settings[f"{provider}_api_key"] = api_key
            
        self.config.config["ai"] = settings
        self.config._saveconfig(self.config.file)

    def load_session_data(self, session_id):
        """Load a session's raw data by ID."""
        from connpy.ai import ai
        agent = ai(self.config)
        return agent.load_session_data(session_id)

