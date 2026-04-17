from .base import BaseService
from .exceptions import ProfileNotFoundError, ProfileAlreadyExistsError, InvalidConfigurationError

class ProfileService(BaseService):
    """Business logic for node profiles management."""

    def list_profiles(self, filter_str=None):
        """List all profile names, optionally filtered."""
        profiles = list(self.config.profiles.keys())
        case_sensitive = self.config.config.get("case", False)
        
        if filter_str:
            if not case_sensitive:
                f_str = filter_str.lower()
                return [p for p in profiles if f_str in p.lower()]
            else:
                return [p for p in profiles if filter_str in p]
        return profiles

    def get_profile(self, name, resolve=True):
        """Get the profile dictionary, optionally resolved."""
        profile = self.config.profiles.get(name)
        if not profile:
            raise ProfileNotFoundError(f"Profile '{name}' not found.")
        
        if resolve:
            return self.resolve_node_data(profile)
        return profile

    def add_profile(self, name, data):
        """Add a new profile."""
        if name in self.config.profiles:
            raise ProfileAlreadyExistsError(f"Profile '{name}' already exists.")
            
        # Filter data to match _profiles_add signature and ensure id is passed
        allowed_keys = {"host", "options", "logs", "password", "port", "protocol", "user", "tags", "jumphost"}
        filtered_data = {k: v for k, v in data.items() if k in allowed_keys}
        
        self.config._profiles_add(id=name, **filtered_data)
        self.config._saveconfig(self.config.file)

    def resolve_node_data(self, node_data):
        """Resolve profile references (@profile) in node data and handle inheritance."""
        resolved = node_data.copy()
        
        # 1. Identify all referenced profiles to support inheritance
        referenced_profiles = []
        for value in resolved.values():
            if isinstance(value, str) and value.startswith("@"):
                referenced_profiles.append(value[1:])
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.startswith("@"):
                        referenced_profiles.append(item[1:])
        
        # 2. Resolve explicit references
        for key, value in resolved.items():
            if isinstance(value, str) and value.startswith("@"):
                profile_name = value[1:]
                try:
                    profile = self.get_profile(profile_name, resolve=True)
                    resolved[key] = profile.get(key, "")
                except ProfileNotFoundError:
                    resolved[key] = ""
            elif isinstance(value, list):
                resolved_list = []
                for item in value:
                    if isinstance(item, str) and item.startswith("@"):
                        profile_name = item[1:]
                        try:
                            profile = self.get_profile(profile_name, resolve=True)
                            if "password" in profile:
                                resolved_list.append(profile["password"])
                        except ProfileNotFoundError:
                            pass
                    else:
                        resolved_list.append(item)
                resolved[key] = resolved_list
        
        # 3. Inheritance: Fill empty keys from the first referenced profile
        if referenced_profiles:
            base_profile_name = referenced_profiles[0]
            try:
                base_profile = self.get_profile(base_profile_name, resolve=True)
                for key, value in base_profile.items():
                    # Fill if key is missing or empty
                    if key not in resolved or resolved[key] == "" or resolved[key] == [] or resolved[key] is None:
                        resolved[key] = value
            except ProfileNotFoundError:
                pass

        # 4. Handle default protocol
        if resolved.get("protocol") == "" or resolved.get("protocol") is None:
            try:
                default_profile = self.get_profile("default", resolve=True)
                resolved["protocol"] = default_profile.get("protocol", "ssh")
            except ProfileNotFoundError:
                resolved["protocol"] = "ssh"
                
        return resolved

    def delete_profile(self, name):
        """Delete an existing profile, with safety checks."""
        if name not in self.config.profiles:
            raise ProfileNotFoundError(f"Profile '{name}' not found.")
            
        if name == "default":
            raise InvalidConfigurationError("Cannot delete the 'default' profile.")
            
        used_by = self.config._profileused(name)
        if used_by:
            # We return the list of nodes using it so the UI can inform the user
            raise InvalidConfigurationError(f"Profile '{name}' is used by nodes: {', '.join(used_by)}")
            
        self.config._profiles_del(id=name)
        self.config._saveconfig(self.config.file)

    def update_profile(self, name, data):
        """Update an existing profile."""
        if name not in self.config.profiles:
            raise ProfileNotFoundError(f"Profile '{name}' not found.")
            
        # Merge with existing data
        existing = self.get_profile(name, resolve=False)
        updated_data = existing.copy()
        updated_data.update(data)
        
        # Filter data to match _profiles_add signature
        allowed_keys = {"host", "options", "logs", "password", "port", "protocol", "user", "tags", "jumphost"}
        filtered_data = {k: v for k, v in updated_data.items() if k in allowed_keys}
        
        self.config._profiles_add(id=name, **filtered_data)
        self.config._saveconfig(self.config.file)

