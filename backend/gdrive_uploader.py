"""
Google Drive Cloud Uploader
Uploads backup archives directly to Google Drive using a Google Service Account.
Works seamlessly in GitHub Actions (Cloud-to-Cloud) without requiring any local computer.
"""

import os
import io
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ['https://www.googleapis.com/auth/drive']
FOLDER_NAMES = ["Ahmed Abdelfattah Backup", "4Brother_ERP_Backups"]

def get_drive_service():
    """
    Initializes Google Drive API service.
    Priority 1: User OAuth2 Credentials (using refresh_token, client_id, client_secret).
                This uses the user's personal Google Drive quota and bypasses the Service Account limit.
    Priority 2: Service Account JSON.
    """
    # 1. Check OAuth2 User Credentials
    refresh_token = os.environ.get("GDRIVE_REFRESH_TOKEN")
    client_id = os.environ.get("GDRIVE_CLIENT_ID")
    client_secret = os.environ.get("GDRIVE_CLIENT_SECRET")
    
    if refresh_token and client_id and client_secret:
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                client_id=client_id,
                client_secret=client_secret,
                token_uri="https://oauth2.googleapis.com/token",
                scopes=SCOPES
            )
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            print(f"Error initializing OAuth2 credentials: {e}")

    # 2. Check Service Account Credentials
    creds_json_str = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    default_sa_path = os.path.join(base_dir, "service_account.json")
    creds_file_path = os.environ.get("GDRIVE_SERVICE_ACCOUNT_FILE", default_sa_path)
    
    if creds_json_str and creds_json_str.strip():
        try:
            creds_info = json.loads(creds_json_str)
            creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            print(f"Error parsing GDRIVE_SERVICE_ACCOUNT_JSON: {e}")
            
    if os.path.exists(creds_file_path):
        creds = service_account.Credentials.from_service_account_file(creds_file_path, scopes=SCOPES)
        return build('drive', 'v3', credentials=creds)
        
    return None

def get_or_create_backup_folder(service, folder_name=None):
    """Find existing folder or create it."""
    # Check if FOLDER_ID is given directly in env
    raw_folder_id = os.environ.get("GDRIVE_FOLDER_ID", "")
    folder_id = raw_folder_id.strip().strip("\ufeff").replace("\r", "").replace("\n", "")
    if folder_id:
        return folder_id

    # Search for folder across known names
    targets = [folder_name] if folder_name else FOLDER_NAMES
    for name in targets:
        query = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        files = results.get('files', [])
        if files:
            return files[0]['id']

    # Fallback: search any shared folder
    results = service.files().list(q="mimeType = 'application/vnd.google-apps.folder' and trashed = false", fields='files(id, name)').execute()
    files = results.get('files', [])
    if files:
        return files[0]['id']

    # Create folder if none exists
    target_name = folder_name or FOLDER_NAMES[0]
    folder_metadata = {
        'name': target_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    folder = service.files().create(body=folder_metadata, fields='id').execute()
    return folder.get('id')

def upload_to_gdrive(file_path, folder_name=None):
    """Uploads a local file to Google Drive and cleans up old files."""
    service = get_drive_service()
    if not service:
        print("[GDRIVE] No Google Service Account credentials found. Skipping cloud upload.")
        return False, None
        
    if not os.path.exists(file_path):
        print(f"[GDRIVE] File not found: {file_path}")
        return False, None
        
    file_name = os.path.basename(file_path)
    folder_id = get_or_create_backup_folder(service, folder_name)
    
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    media = MediaFileUpload(file_path, mimetype='application/zip', resumable=True)
    
    print(f"[GDRIVE] Uploading {file_name} to Google Drive folder ({folder_id})...")
    uploaded_file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, name, size'
    ).execute()
    
    file_id = uploaded_file.get('id')
    print(f"[GDRIVE] Upload complete! File ID: {file_id}")
    
    # Retention cleanup: Keep only the last 30 backups in Drive
    clean_old_gdrive_files(service, folder_id, keep_count=30)
    
    return True, file_id

def clean_old_gdrive_files(service, folder_id, keep_count=30):
    """Keep only the latest N backups in the Google Drive folder."""
    try:
        query = f"'{folder_id}' in parents and trashed = false"
        results = service.files().list(
            q=query,
            orderBy='createdTime desc',
            fields='files(id, name, createdTime)'
        ).execute()
        files = results.get('files', [])
        
        if len(files) > keep_count:
            files_to_delete = files[keep_count:]
            for f in files_to_delete:
                print(f"[GDRIVE RETENTION] Deleting old backup: {f['name']}")
                service.files().delete(fileId=f['id']).execute()
    except Exception as e:
        print(f"[GDRIVE RETENTION] Cleanup error: {e}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        upload_to_gdrive(sys.argv[1])
    else:
        print("Usage: python gdrive_uploader.py <path_to_zip>")

