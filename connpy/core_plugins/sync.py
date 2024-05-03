#!/usr/bin/python3
import argparse
import os
import time
import zipfile
import tempfile
import io
import yaml
import threading
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.http import MediaFileUpload,MediaIoBaseDownload
from googleapiclient.errors import HttpError
from datetime import datetime

class sync:

    def __init__(self, connapp):
        self.scopes = ['https://www.googleapis.com/auth/drive.appdata']
        self.token_file = f"{connapp.config.defaultdir}/gtoken.json"
        self.file = connapp.config.file
        self.key = connapp.config.key
        self.google_client = f"{os.path.dirname(os.path.abspath(__file__))}/sync_client"
        self.connapp = connapp
        try:
            self.sync = self.connapp.config.config["sync"]
        except:
            self.sync = False

    def login(self):
        creds = None
        # The file token.json stores the user's access and refresh tokens.
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, self.scopes)

        try:
            # If there are no valid credentials available, let the user log in.
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.google_client, self.scopes)
                    creds = flow.run_local_server(port=0, access_type='offline')

                # Save the credentials for the next run
                with open(self.token_file, 'w') as token:
                    token.write(creds.to_json())

            print("Logged in successfully.")

        except RefreshError as e:
            # If refresh fails, delete the invalid token file and start a new login flow
            if os.path.exists(self.token_file):
                os.remove(self.token_file)
            print("Existing token was invalid and has been removed. Please log in again.")
            flow = InstalledAppFlow.from_client_secrets_file(
                self.google_client, self.scopes)
            creds = flow.run_local_server(port=0, access_type='offline')
            with open(self.token_file, 'w') as token:
                token.write(creds.to_json())
            print("Logged in successfully after re-authentication.")

    def logout(self):
        if os.path.exists(self.token_file):
            os.remove(self.token_file)
            print("Logged out successfully.")
        else:
            print("No credentials file found. Already logged out.")

    def get_credentials(self):
        # Load credentials from token.json
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, self.scopes)
        else:
            print("Credentials file not found.")
            return 0
        
        # If there are no valid credentials available, ask the user to log in again
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError:
                    print("Could not refresh access token. Please log in again.")
                    return 0
            else:
                print("Credentials are missing or invalid. Please log in.")
                return 0
        return creds

    def check_login_status(self):
        # Check if the credentials file exists
        if os.path.exists(self.token_file):
            # Load credentials from token.json
            creds = Credentials.from_authorized_user_file(self.token_file)

            # If credentials are expired, refresh them
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError:
                    pass

            # Check if the credentials are valid after refresh
            if creds.valid:
                return True
            else:
                return "Invalid"
        else:
            return False

    def status(self):
        print(f"Login: {self.check_login_status()}")
        print(f"Sync: {self.sync}")


    def get_appdata_files(self):

        creds = self.get_credentials()
        if not creds:
            return 0

        try:
            # Create the Google Drive service
            service = build("drive", "v3", credentials=creds)

            # List files in the appDataFolder
            response = (
                service.files()
                .list(
                    spaces="appDataFolder",
                    fields="files(id, name, appProperties)",
                    pageSize=10,
                )
                .execute()
            )

            files_info = []
            for file in response.get("files", []):
                # Extract file information
                file_id = file.get("id")
                file_name = file.get("name")
                timestamp = file.get("appProperties", {}).get("timestamp")
                human_readable_date = file.get("appProperties", {}).get("date")
                files_info.append({"name": file_name, "id": file_id, "date": human_readable_date, "timestamp": timestamp})

            return files_info

        except HttpError as error:
            print(f"An error occurred: {error}")
            return 0


    def dump_appdata_files_yaml(self):
        files_info = self.get_appdata_files()
        if not files_info:
            print("Failed to retrieve files or no files found.")
            return
        # Pretty print as YAML
        yaml_output = yaml.dump(files_info, sort_keys=False, default_flow_style=False)
        print(yaml_output)


    def backup_file_to_drive(self, file_path, timestamp):
        
        creds = self.get_credentials()
        if not creds:
            return 1
        
        # Create the Google Drive service
        service = build('drive', 'v3', credentials=creds)
        
        # Convert timestamp to a human-readable date
        human_readable_date = datetime.fromtimestamp(timestamp/1000).strftime('%Y-%m-%d %H:%M:%S')
        
        # Upload the file to Google Drive with timestamp metadata
        file_metadata = {
            'name': os.path.basename(file_path),
            'parents': ["appDataFolder"],
            'appProperties': {
                'timestamp': str(timestamp),
                'date': human_readable_date  # Add human-readable date attribute
            }
        }
        media = MediaFileUpload(file_path)
        
        try:
            file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            return 0
        except Exception as e:
            return f"An error occurred: {e}"

    def delete_file_by_id(self, file_id):
        creds = self.get_credentials()
        if not creds:
            return 1

        try:
            # Create the Google Drive service
            service = build("drive", "v3", credentials=creds)

            # Delete the file
            service.files().delete(fileId=file_id).execute()
            return 0
        except Exception as e:
            return f"An error occurred: {e}"

    def compress_specific_files(self, zip_path):
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(self.file, "config.json")
            zipf.write(self.key, ".osk")

    def compress_and_upload(self):
        # Read the file content to get the folder path
        timestamp = int(time.time() * 1000)
        # Create a temporary directory for storing the zip file
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Compress specific files from the folder path to a zip file in the temporary directory
            zip_path = os.path.join(tmp_dir, f"connpy-backup-{timestamp}.zip")
            self.compress_specific_files(zip_path)

            # Get the files in the app data folder
            app_data_files = self.get_appdata_files()
            if app_data_files == 0:
                return 1

            # If there are 10 or more files, remove the oldest one based on timestamp
            if len(app_data_files) >= 10:
                oldest_file = min(app_data_files, key=lambda x: x['timestamp'])
                delete_old = self.delete_file_by_id(oldest_file['id'])
                if delete_old:
                    print(delete_old)
                    return 1

            # Upload the new file
            upload_new = self.backup_file_to_drive(zip_path, timestamp)
            if upload_new:
                print(upload_new)
                return 1
            
            print("Backup to google uploaded successfully.")
            return 0

    def decompress_zip(self, zip_path):
        try:
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                # Extract the specific file to the specified destination
                zipf.extract("config.json", os.path.dirname(self.file))
                zipf.extract(".osk", os.path.dirname(self.key))
            return 0
        except Exception as e:
            print(f"An error occurred: {e}")
            return 1

    def download_file_by_id(self, file_id, destination_path):

        creds = self.get_credentials()
        if not creds:
            return 1

        try:
            # Create the Google Drive service
            service = build('drive', 'v3', credentials=creds)

            # Download the file
            request = service.files().get_media(fileId=file_id)
            fh = io.FileIO(destination_path, mode='wb')
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()

            return 0
        except Exception as e:
            return f"An error occurred: {e}"

    def restore_last_config(self, file_id=None):
        # Get the files in the app data folder
        app_data_files = self.get_appdata_files()
        if not app_data_files:
            print("No files found in app data folder.")
            return 1

        # Check if a specific file_id was provided and if it exists in the list
        if file_id:
            selected_file = next((f for f in app_data_files if f['id'] == file_id), None)
            if not selected_file:
                print(f"No file found with ID: {file_id}")
                return 1
        else:
            # Find the latest file based on timestamp
            selected_file = max(app_data_files, key=lambda x: x['timestamp'])

        # Download the selected file to a temporary location
        temp_download_path = os.path.join(tempfile.gettempdir(), 'connpy-backup.zip')
        if self.download_file_by_id(selected_file['id'], temp_download_path):
            return 1

        # Unzip the downloaded file to the destination folder
        if self.decompress_zip(temp_download_path):
            print("Failed to decompress the file.")
            return 1

        print(f"Backup from Google Drive restored successfully: {selected_file['name']}")
        return 0

    def config_listener_post(self, args, kwargs):
        if self.sync:
            if self.check_login_status() == True:
                if not kwargs["result"]:
                    self.compress_and_upload()
            else:
                print("Sync cannot be performed. Please check your login status.")
        return kwargs["result"]

    def config_listener_pre(self, *args, **kwargs):
        try:
            self.sync = self.connapp.config.config["sync"]
        except:
            self.sync = False
        return args, kwargs

    def start_post_thread(self, *args, **kwargs):
        post_thread = threading.Thread(target=self.config_listener_post, args=(args,kwargs))
        post_thread.start()

class Preload:
    def __init__(self, connapp):
        syncapp = sync(connapp)
        connapp.config._saveconfig.register_post_hook(syncapp.start_post_thread)
        connapp.config._saveconfig.register_pre_hook(syncapp.config_listener_pre)

class Parser:
    def __init__(self):
        self.parser = argparse.ArgumentParser(description="Sync config with Google")
        self.description = "Sync config with Google"
        subparsers = self.parser.add_subparsers(title="Commands", dest='command',metavar="")
        login_parser = subparsers.add_parser("login", help="Login to Google to enable synchronization")
        logout_parser = subparsers.add_parser("logout", help="Logout from Google")
        start_parser = subparsers.add_parser("start", help="Start synchronizing with Google")
        stop_parser = subparsers.add_parser("stop", help="Stop any ongoing synchronization")
        restore_parser = subparsers.add_parser("restore", help="Restore data from Google")
        backup_parser = subparsers.add_parser("once", help="Backup current configuration to Google once")
        restore_parser.add_argument("--id", type=str, help="Optional file ID to restore a specific backup", required=False)
        status_parser = subparsers.add_parser("status", help="Check the current status of synchronization")
        list_parser = subparsers.add_parser("list", help="List all backups stored on Google")

class Entrypoint:
    def __init__(self, args, parser, connapp):
        syncapp = sync(connapp)
        if args.command == 'login':
            syncapp.login()
        elif args.command == "status":
            syncapp.status()
        elif args.command == "start":
            connapp._change_settings("sync", True)
        elif args.command == "stop":
            connapp._change_settings("sync", False)
        elif args.command == "list":
            syncapp.dump_appdata_files_yaml()
        elif args.command == "once":
            syncapp.compress_and_upload()
        elif args.command == "restore":
            syncapp.restore_last_config(args.id)
        elif args.command == "logout":
            syncapp.logout()

def _connpy_completion(wordsnumber, words, info = None):
    if wordsnumber == 3:
        result = ["--help", "login", "status", "start", "stop", "list", "once", "restore", "logout"]
    #NETMASK_completion
    if wordsnumber == 4 and words[1] == "restore":
            result = ["--help", "--id"]
    return result
