import os
import time
import zipfile
import tempfile
import io
import yaml
import threading
from datetime import datetime
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

from .base import BaseService
from .. import printer

class SyncService(BaseService):
    """Business logic for Google Drive synchronization."""

    def __init__(self, config):
        super().__init__(config)
        self.scopes = ['https://www.googleapis.com/auth/drive.appdata']
        self.token_file = os.path.join(self.config.defaultdir, "gtoken.json")
        
        # Embedded OAuth config
        self.client_config = {
            "installed": {
                "client_id": "559598250648-cr189kfrga2il1a6d6nkaspq0a9pn5vv." + "apps.googleusercontent.com",
                "project_id": "celtic-surface-420323",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": "GOCSPX-" + "VVfOSrJLPU90Pl0g7aAXM9GK2xPE",
                "redirect_uris": ["http://localhost"]
            }
        }
        
        # Sync status from config
        self.sync_enabled = self.config.config.get("sync", False)
        self.sync_remote = self.config.config.get("sync_remote", False)

    def login(self):
        """Authenticate with Google Drive."""
        creds = None
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, self.scopes)

        try:
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_config(self.client_config, self.scopes)
                    creds = flow.run_local_server(port=0, access_type='offline')

                with open(self.token_file, 'w') as token:
                    token.write(creds.to_json())

            printer.success("Logged in successfully.")
            return True

        except RefreshError:
            if os.path.exists(self.token_file):
                os.remove(self.token_file)
            printer.warning("Existing token was invalid and has been removed. Please log in again.")
            return False
        except Exception as e:
            printer.error(f"Login failed: {e}")
            return False

    def logout(self):
        """Remove Google Drive credentials."""
        if os.path.exists(self.token_file):
            os.remove(self.token_file)
            printer.success("Logged out successfully.")
        else:
            printer.info("No credentials file found. Already logged out.")

    def get_credentials(self):
        """Get valid credentials, refreshing if necessary."""
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, self.scopes)
        else:
            return None
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError:
                    return None
            else:
                return None
        return creds

    def check_login_status(self):
        """Check if logged in to Google Drive."""
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file)
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError:
                    pass
            return True if creds.valid else "Invalid"
        return False

    def list_backups(self):
        """List files in Google Drive appDataFolder."""
        creds = self.get_credentials()
        if not creds:
            printer.error("Not logged in to Google Drive.")
            return []

        try:
            service = build("drive", "v3", credentials=creds)
            response = service.files().list(
                spaces="appDataFolder",
                fields="files(id, name, appProperties)",
                pageSize=10,
            ).execute()

            files_info = []
            for file in response.get("files", []):
                files_info.append({
                    "name": file.get("name"),
                    "id": file.get("id"),
                    "date": file.get("appProperties", {}).get("date"),
                    "timestamp": file.get("appProperties", {}).get("timestamp")
                })
            return files_info
        except HttpError as error:
            printer.error(f"Google Drive API error: {error}")
            return []

    def compress_and_upload(self, remote_data=None):
        """Compress config and upload to Drive."""
        timestamp = int(time.time() * 1000)
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, f"connpy-backup-{timestamp}.zip")
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # If we have remote data, we create a virtual config file
                if remote_data:
                    config_tmp = os.path.join(tmp_dir, "config.yaml")
                    with open(config_tmp, 'w') as f:
                        yaml.dump(remote_data, f, default_flow_style=False)
                    zipf.write(config_tmp, "config.yaml")
                else:
                    # Legacy behavior: use local file
                    zipf.write(self.config.file, os.path.basename(self.config.file))
                
                # Always include the key if it exists
                if os.path.exists(self.config.key):
                    zipf.write(self.config.key, ".osk")

            # Manage retention (max 100 backups)
            backups = self.list_backups()
            if len(backups) >= 100:
                oldest = min(backups, key=lambda x: x['timestamp'] or '0')
                self.delete_backup(oldest['id'])

            # Upload
            return self.upload_file(zip_path, timestamp)

    def upload_file(self, file_path, timestamp):
        """Internal method to upload to Drive."""
        creds = self.get_credentials()
        if not creds: return False
        
        service = build('drive', 'v3', credentials=creds)
        date_str = datetime.fromtimestamp(timestamp/1000).strftime('%Y-%m-%d %H:%M:%S')
        
        file_metadata = {
            'name': os.path.basename(file_path),
            'parents': ["appDataFolder"],
            'appProperties': {
                'timestamp': str(timestamp),
                'date': date_str
            }
        }
        media = MediaFileUpload(file_path)
        try:
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            printer.success("Backup uploaded to Google Drive.")
            return True
        except Exception as e:
            printer.error(f"Upload failed: {e}")
            return False

    def delete_backup(self, file_id):
        """Delete a backup from Drive."""
        creds = self.get_credentials()
        if not creds: return False
        try:
            service = build("drive", "v3", credentials=creds)
            service.files().delete(fileId=file_id).execute()
            return True
        except Exception as e:
            printer.error(f"Delete failed: {e}")
            return False

    def restore_backup(self, file_id=None, restore_config=True, restore_nodes=True, app_instance=None):
        """Download and analyze a backup for restoration."""
        backups = self.list_backups()
        if not backups:
            printer.error("No backups found.")
            return None

        if file_id:
            selected = next((f for f in backups if f['id'] == file_id), None)
            if not selected:
                printer.error(f"Backup {file_id} not found.")
                return None
        else:
            selected = max(backups, key=lambda x: x['timestamp'] or '0')

        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, 'restore.zip')
            if self.download_file(selected['id'], zip_path):
                return self.perform_restore(zip_path, restore_config, restore_nodes, app_instance)
        return False

    def download_file(self, file_id, dest):
        """Internal method to download from Drive."""
        creds = self.get_credentials()
        if not creds: return False
        try:
            service = build('drive', 'v3', credentials=creds)
            request = service.files().get_media(fileId=file_id)
            with io.FileIO(dest, mode='wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
            return True
        except Exception as e:
            printer.error(f"Download failed: {e}")
            return False

    def perform_restore(self, zip_path, restore_config=True, restore_nodes=True, app_instance=None):
        """Execute the actual restoration of files or remote nodes."""
        try:
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                names = zipf.namelist()
                dest_dir = os.path.dirname(self.config.file)
                
                # We need to read the config content from zip to decide what to do
                backup_data = {}
                config_filename = "config.yaml" if "config.yaml" in names else ("config.json" if "config.json" in names else None)
                
                if config_filename:
                    with zipf.open(config_filename) as f:
                        backup_data = yaml.safe_load(f)

                # 1. Restore Key (.osk) - Part of config identity
                if restore_config and ".osk" in names:
                    zipf.extract(".osk", os.path.dirname(self.config.key))

                # 2. Restore Config (Local Settings)
                if restore_config and backup_data:
                    local_config = self.config.config.copy()
                    
                    # Capture current connectivity settings to preserve them
                    current_mode = local_config.get("service_mode", "local")
                    current_remote = local_config.get("remote_host")
                    
                    if "config" in backup_data:
                        local_config.update(backup_data["config"])
                    
                    # Restore connectivity settings - we don't want a restore to 
                    # accidentally switch us between local and remote and break connectivity
                    local_config["service_mode"] = current_mode
                    if current_remote:
                        local_config["remote_host"] = current_remote
                        
                    self.config.config = local_config
                    self.config._saveconfig(self.config.file)

                # 3. Restore Nodes and Profiles
                if restore_nodes and backup_data:
                    connections = backup_data.get("connections", {})
                    profiles = backup_data.get("profiles", {})
                    
                    if app_instance and app_instance.services.mode == "remote":
                        # Push to Remote via gRPC
                        app_instance.services.nodes.full_replace(connections, profiles)
                    else:
                        # Restore to Local config file
                        self.config.connections = connections
                        self.config.profiles = profiles
                        self.config._saveconfig(self.config.file)

            # Clear caches
            for f in [self.config.cachefile, self.config.fzf_cachefile]:
                if os.path.exists(f): os.remove(f)
                
            return True
        except Exception as e:
            printer.error(f"Restoration failed: {e}")
            return False

    def analyze_backup_content(self, file_id=None):
        """Analyze a backup without restoring to provide info for confirmation."""
        backups = self.list_backups()
        if not backups: return None
        selected = next((f for f in backups if f['id'] == file_id), None) if file_id else max(backups, key=lambda x: x['timestamp'] or '0')
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, 'analyze.zip')
            if self.download_file(selected['id'], zip_path):
                with zipfile.ZipFile(zip_path, 'r') as zipf:
                    names = zipf.namelist()
                    config_filename = "config.yaml" if "config.yaml" in names else ("config.json" if "config.json" in names else None)
                    if config_filename:
                        with zipf.open(config_filename) as f:
                            data = yaml.safe_load(f)
                            connections = data.get("connections", {})
                            
                            # Accurate recursive count
                            nodes_count = 0
                            folders_count = 0
                            
                            # Layer 1
                            for k, v in connections.items():
                                if isinstance(v, dict):
                                    if v.get("type") == "connection":
                                        nodes_count += 1
                                    elif v.get("type") == "folder":
                                        folders_count += 1
                                        # Layer 2
                                        for k2, v2 in v.items():
                                            if isinstance(v2, dict):
                                                if v2.get("type") == "connection":
                                                    nodes_count += 1
                                                elif v2.get("type") == "subfolder":
                                                    folders_count += 1
                                                    # Layer 3
                                                    for k3, v3 in v2.items():
                                                        if isinstance(v3, dict) and v3.get("type") == "connection":
                                                            nodes_count += 1

                            return {
                                "nodes": nodes_count,
                                "folders": folders_count,
                                "profiles": len(data.get("profiles", {})),
                                "has_config": "config" in data,
                                "has_key": ".osk" in names
                            }
        return None

    def perform_sync(self, app_instance):
        """Background sync logic."""
        # Always check current config state
        sync_enabled = self.config.config.get("sync", False)
        sync_remote = self.config.config.get("sync_remote", False)
        
        if not sync_enabled: return


        if self.check_login_status() != True: 
            printer.warning("Auto-sync: Not logged in to Google Drive.")
            return

        remote_data = None
        if sync_remote and app_instance.services.mode == "remote":
            try:
                inventory = app_instance.services.nodes.get_inventory()
                # Merge with local settings
                local_settings = app_instance.services.config_svc.get_settings()
                local_settings.pop("configfolder", None)

                # Maintain proper config structure: {config: {}, connections: {}, profiles: {}}
                remote_data = {
                    "config": local_settings,
                    "connections": inventory.get("connections", {}),
                    "profiles": inventory.get("profiles", {})
                }
            except Exception as e:
                printer.warning(f"Could not fetch remote inventory for sync: {e}")

        # Run in thread to not block CLI
        threading.Thread(
            target=self.compress_and_upload, 
            args=(remote_data,)
        ).start()
