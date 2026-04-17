import os
import shutil
import base64
from typing import Any, Dict
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
from .base import BaseService
from .exceptions import ConnpyError, InvalidConfigurationError, NodeNotFoundError


class ConfigService(BaseService):
    """Business logic for general application settings and state configuration."""

    def get_settings(self) -> Dict[str, Any]:
        """Get the global configuration settings block."""
        settings = self.config.config.copy()
        settings["configfolder"] = self.config.defaultdir
        return settings

    def get_default_dir(self) -> str:
        """Get the default configuration directory."""
        return self.config.defaultdir

    def set_config_folder(self, folder_path: str):
        """Set the default location for config file by writing to ~/.config/conn/.folder"""
        if not os.path.isdir(folder_path):
            raise ConnpyError(f"readable_dir:{folder_path} is not a valid path")
            
        pathfile = os.path.join(self.config.anchor_path, ".folder")
        folder = os.path.abspath(folder_path).rstrip('/')
        
        try:
            with open(pathfile, "w") as f:
                f.write(str(folder))
        except Exception as e:
            raise ConnpyError(f"Failed to save config folder: {e}")

    def update_setting(self, key, value):
        """Update a setting in the configuration file."""
        self.config.config[key] = value
        self.config._saveconfig(self.config.file)

    def encrypt_password(self, password):
        """Encrypt a password using the application's configuration encryption key."""
        return self.config.encrypt(password)
        
    def apply_theme_from_file(self, theme_input):
        """Apply 'dark', 'light' theme or load a YAML theme file and save it to the configuration."""
        import yaml
        from ..printer import STYLES, LIGHT_THEME
        
        if theme_input == "dark":
            valid_styles = {}
            self.update_setting("theme", valid_styles)
            return valid_styles
        elif theme_input == "light":
            valid_styles = LIGHT_THEME.copy()
            self.update_setting("theme", valid_styles)
            return valid_styles
            
        if not os.path.exists(theme_input):
            raise InvalidConfigurationError(f"Theme file '{theme_input}' not found.")
            
        try:
            with open(theme_input, 'r') as f:
                user_styles = yaml.safe_load(f)
        except Exception as e:
            raise InvalidConfigurationError(f"Failed to parse theme file: {e}")
            
        if not isinstance(user_styles, dict):
            raise InvalidConfigurationError("Theme file must be a YAML dictionary.")
            
        # Filter for valid styles only (prevent junk in config)
        valid_styles = {k: v for k, v in user_styles.items() if k in STYLES}
        
        if not valid_styles:
            raise InvalidConfigurationError("No valid style keys found in theme file.")
            
        # Persist and return merged styles
        self.update_setting("theme", valid_styles)
        return valid_styles

