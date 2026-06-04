import sys
import yaml
import inquirer
from .. import printer

class SSOHandler:
    def __init__(self, app):
        self.app = app

    def dispatch(self, args):
        if self.app.services.mode == "remote":
            printer.error("SSO management commands are only available in local/server-side mode.")
            sys.exit(1)

        # Parse actions from argparse mutually exclusive options
        if getattr(args, "add", None):
            args.action = "add"
            args.provider = args.add[0]
        elif getattr(args, "delete", None):
            args.action = "del"
            args.provider = args.delete[0]
        elif getattr(args, "list", False):
            args.action = "list"
        elif getattr(args, "show", None):
            args.action = "show"
            args.provider = args.show[0]

        action = getattr(args, "action", None)
        
        if action == "add":
            return self.add_provider(args)
        elif action == "del":
            return self.delete_provider(args)
        elif action == "list":
            return self.list_providers(args)
        elif action == "show":
            return self.show_provider(args)
        else:
            printer.error(f"Unknown action: {action}")
            sys.exit(1)

    def add_provider(self, args):
        provider = args.provider
        sso = self.app.config.config.get("sso", {})
        providers = sso.setdefault("providers", {})
        
        existing = providers.get(provider, {})
        if existing:
            printer.warning(f"SSO Provider '{provider}' already exists. Overwriting/Editing it.")
        
        # Interactive questionnaire
        questions = [
            inquirer.Text("jwks_url", message="JWKS URL (optional, press Enter to skip)", default=existing.get("jwks_url", "")),
            inquirer.Text("secret", message="Client Secret / Shared Secret (optional, press Enter to skip)", default=existing.get("secret", "")),
            inquirer.Text("username_claim", message="Username Claim", default=existing.get("username_claim", "sub")),
            inquirer.Text("algorithms", message="Algorithms (comma separated)", default=",".join(existing.get("algorithms", ["RS256"]))),
            inquirer.Text("allowed_domains", message="Allowed/Trusted Email Domains (comma separated, optional)", default=",".join(existing.get("allowed_domains", [])))
        ]
        
        answers = inquirer.prompt(questions)
        if not answers:
            printer.warning("Operation cancelled.")
            sys.exit(130)
            
        jwks_url = answers["jwks_url"].strip()
        secret = answers["secret"].strip()
        username_claim = answers["username_claim"].strip()
        algorithms_str = answers["algorithms"].strip()
        allowed_domains_str = answers.get("allowed_domains", "").strip()
        
        if not jwks_url and not secret:
            printer.error("You must configure either a JWKS URL or a Secret.")
            sys.exit(1)
            
        if not username_claim:
            printer.error("Username claim cannot be empty.")
            sys.exit(1)
            
        algorithms = [alg.strip() for alg in algorithms_str.split(",") if alg.strip()]
        if not algorithms:
            algorithms = ["RS256"]
            
        allowed_domains = [domain.strip() for domain in allowed_domains_str.split(",") if domain.strip()]
            
        provider_data = {
            "username_claim": username_claim,
            "algorithms": algorithms
        }
        if jwks_url:
            provider_data["jwks_url"] = jwks_url
        if secret:
            provider_data["secret"] = secret
        if allowed_domains:
            provider_data["allowed_domains"] = allowed_domains
            
        providers[provider] = provider_data
        
        # Save config
        try:
            self.app.services.config_svc.update_setting("sso", sso)
            printer.success(f"SSO Provider '{provider}' saved successfully.")
        except Exception as e:
            printer.error(f"Failed to save SSO configuration: {e}")
            sys.exit(1)

    def delete_provider(self, args):
        provider = args.provider
        sso = self.app.config.config.get("sso", {})
        providers = sso.get("providers", {})
        
        if provider not in providers:
            printer.error(f"SSO Provider '{provider}' not found.")
            sys.exit(1)
            
        # Confirm delete
        questions = [inquirer.Confirm("confirm", message=f"Are you sure you want to delete SSO Provider '{provider}'?", default=False)]
        answers = inquirer.prompt(questions)
        if not answers or not answers["confirm"]:
            printer.info("Delete cancelled.")
            return
            
        del providers[provider]
        
        # Save config
        try:
            self.app.services.config_svc.update_setting("sso", sso)
            printer.success(f"SSO Provider '{provider}' deleted successfully.")
        except Exception as e:
            printer.error(f"Failed to save SSO configuration: {e}")
            sys.exit(1)

    def list_providers(self, args):
        sso = self.app.config.config.get("sso", {})
        providers = sso.get("providers", {})
        if not providers:
            printer.warning("No SSO providers configured.")
            return
            
        # Print list in YAML format
        providers_list = list(providers.keys())
        yaml_str = yaml.dump(providers_list, sort_keys=False, default_flow_style=False)
        printer.data("Configured SSO Providers", yaml_str)

    def show_provider(self, args):
        provider = args.provider
        sso = self.app.config.config.get("sso", {})
        providers = sso.get("providers", {})
        
        if provider not in providers:
            printer.error(f"SSO Provider '{provider}' not found.")
            sys.exit(1)
            
        data = providers[provider]
        
        # Mask client secret for display if it's sensitive and not an env var starting with $
        display_data = data.copy()
        secret = display_data.get("secret")
        if secret and not secret.startswith("$"):
            display_data["secret"] = "********"
            
        yaml_str = yaml.dump(display_data, sort_keys=False, default_flow_style=False)
        printer.data(f"SSO Provider: {provider}", yaml_str)
