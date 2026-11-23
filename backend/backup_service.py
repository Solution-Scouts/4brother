"""
Backup Service Module for 4Brother ERP
Handles comprehensive backups:
1. Database to SQLite (.db) with all 27 tables and full data
2. Incremental Product Images Sync from Supabase Storage (.webp)
3. Direct auto-sync to Google Drive (G:\\My Drive\\4Brother_ERP_Backups)
4. Retention policy to clean backups older than N days
"""

import os
import sys
import io
import json
import shutil
import zipfile
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine, MetaData, text
from dotenv import dotenv_values

GOOGLE_DRIVE_BACKUP_DIR = r"G:\My Drive\4Brother_ERP_Backups"

def get_base_dir():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def get_pg_engine():
    base_dir = get_base_dir()
    env_vars = dotenv_values(os.path.join(base_dir, ".env"))
    pg_url = env_vars.get("DATABASE_URL")
    if not pg_url:
        pg_url = os.environ.get("DATABASE_URL")
    if pg_url and pg_url.startswith("postgres://"):
        pg_url = pg_url.replace("postgres://", "postgresql://", 1)
    return create_engine(pg_url)

def generate_sqlite_snapshot(sqlite_path, pg_engine=None):
    """Mirror all PG tables into a standalone portable SQLite database file."""
    if pg_engine is None:
        pg_engine = get_pg_engine()
        
    sqlite_engine = create_engine(f'sqlite:///{sqlite_path}')
    
    base_dir = get_base_dir()
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
        
    from backend.core import db
    from backend import models  # Ensure all models are registered
    
    # Create all tables in SQLite
    db.metadata.create_all(bind=sqlite_engine)

    pg_meta = MetaData()
    pg_meta.reflect(bind=pg_engine)

    sqlite_meta = MetaData()
    sqlite_meta.reflect(bind=sqlite_engine)

    stats = {}
    with sqlite_engine.begin() as sqlite_conn:
        sqlite_conn.execute(text("PRAGMA foreign_keys = OFF;"))
        for table in pg_meta.sorted_tables:
            if table.name not in sqlite_meta.tables:
                continue
            sqlite_table = sqlite_meta.tables[table.name]
            sqlite_conn.execute(sqlite_table.delete())
            
            with pg_engine.connect() as pg_conn:
                rows = pg_conn.execute(table.select()).fetchall()
                if not rows:
                    stats[table.name] = 0
                    continue
                
                pg_cols = set(c.name for c in table.columns)
                sqlite_cols = set(c.name for c in sqlite_table.columns)
                common_cols = pg_cols.intersection(sqlite_cols)
                
                insert_data = []
                for row in rows:
                    row_dict = {col: row._mapping[col] for col in common_cols}
                    insert_data.append(row_dict)
                
                for i in range(0, len(insert_data), 1000):
                    sqlite_conn.execute(sqlite_table.insert(), insert_data[i:i+1000])
                stats[table.name] = len(insert_data)

    sqlite_engine.dispose()
    pg_engine.dispose()
    return stats

def sync_product_images(cache_dir, max_workers=10):
    """
    Incrementally sync active product images from Supabase Storage to cache_dir.
    Skips images already cached to save time and bandwidth.
    """
    base_dir = get_base_dir()
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
        
    from backend.models import ProductModel
    from backend.core import app
    
    os.makedirs(cache_dir, exist_ok=True)
    urls = []
    with app.app_context():
        models = ProductModel.query.all()
        for m in models:
            if m.image and m.image.startswith("http"):
                urls.append((m.id, m.image))
                
    results = {"total": len(urls), "downloaded": 0, "already_cached": 0, "failed": 0}
    
    def fetch_img(item):
        m_id, url = item
        try:
            filename = url.split("/")[-1].split("?")[0]
            if not filename.endswith((".webp", ".png", ".jpg", ".jpeg")):
                filename = f"model_{m_id}.webp"
            target_path = os.path.join(cache_dir, filename)
            
            # Skip if already cached and valid size
            if os.path.exists(target_path) and os.path.getsize(target_path) > 100:
                return "cached"
                
            resp = requests.get(url, timeout=12)
            if resp.status_code == 200 and len(resp.content) > 0:
                with open(target_path, "wb") as f:
                    f.write(resp.content)
                return "downloaded"
            return "failed"
        except Exception:
            return "failed"

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for status in executor.map(fetch_img, urls):
            if status == "cached":
                results["already_cached"] += 1
            elif status == "downloaded":
                results["downloaded"] += 1
            else:
                results["failed"] += 1
                
    return results

def create_backup_archive(include_images=True, target_dir=None):
    """
    Build a comprehensive .zip archive containing DB + Images + Manifest.
    Copies the resulting archive to Google Drive if accessible.
    """
    import tempfile
    base_dir = get_base_dir()
    local_backups_dir = os.path.join(base_dir, "backups")
    try:
        os.makedirs(local_backups_dir, exist_ok=True)
        test_file = os.path.join(local_backups_dir, ".perm_test")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
    except Exception:
        local_backups_dir = os.path.join(tempfile.gettempdir(), "4brother_backups")
        os.makedirs(local_backups_dir, exist_ok=True)
        
    images_cache_dir = os.path.join(local_backups_dir, "images_cache")
    os.makedirs(images_cache_dir, exist_ok=True)
    
    cairo_now = datetime.now(timezone(timedelta(hours=2)))
    timestamp = cairo_now.strftime("%Y-%m-%d_%H-%M-%S")
    zip_name = f"4brother_backup_{timestamp}_{'full' if include_images else 'db'}.zip"
    local_zip_path = os.path.join(local_backups_dir, zip_name)
    
    temp_dir = os.path.join(local_backups_dir, f"tmp_{timestamp}")
    os.makedirs(temp_dir, exist_ok=True)
    sqlite_temp = os.path.join(temp_dir, "erp_crm.db")
    
    try:
        # 1. Export SQLite Snapshot
        db_stats = generate_sqlite_snapshot(sqlite_temp)
        
        # 2. Sync Images if requested
        img_stats = {}
        if include_images:
            img_stats = sync_product_images(images_cache_dir)
            
        # 3. Create Manifest JSON
        manifest = {
            "created_at": datetime.now().isoformat(),
            "timestamp": timestamp,
            "system": "4Brother ERP v2",
            "type": "full" if include_images else "db_only",
            "total_tables": len(db_stats),
            "database_stats": db_stats,
            "image_stats": img_stats
        }
        manifest_path = os.path.join(temp_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            
        # 4. Build ZIP file
        with zipfile.ZipFile(local_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            if os.path.exists(sqlite_temp):
                zipf.write(sqlite_temp, arcname="erp_crm.db")
            if os.path.exists(manifest_path):
                zipf.write(manifest_path, arcname="manifest.json")
            if include_images and os.path.exists(images_cache_dir):
                for fname in os.listdir(images_cache_dir):
                    fpath = os.path.join(images_cache_dir, fname)
                    if os.path.isfile(fpath):
                        zipf.write(fpath, arcname=f"images/{fname}")

        size_mb = round(os.path.getsize(local_zip_path) / (1024 * 1024), 2)
        
        # 5. Copy to Google Drive if target_dir or G:\My Drive is accessible
        gdrive_synced = False
        destination_path = local_zip_path
        
        dest_dirs = []
        if target_dir:
            dest_dirs.append(target_dir)
        elif os.path.exists(GOOGLE_DRIVE_BACKUP_DIR):
            dest_dirs.append(GOOGLE_DRIVE_BACKUP_DIR)
            
        for d in dest_dirs:
            try:
                os.makedirs(d, exist_ok=True)
                dest_file = os.path.join(d, zip_name)
                shutil.copy2(local_zip_path, dest_file)
                destination_path = dest_file
                gdrive_synced = True
            except Exception as e:
                print(f"Warning: Could not copy to {d}: {e}")

        # 6. Clean old backups
        clean_old_backups(local_backups_dir, keep_days=14)
        if os.path.exists(GOOGLE_DRIVE_BACKUP_DIR):
            clean_old_backups(GOOGLE_DRIVE_BACKUP_DIR, keep_days=30)
            
        return {
            "success": True,
            "zip_path": destination_path,
            "local_path": local_zip_path,
            "zip_name": zip_name,
            "size_mb": size_mb,
            "gdrive_synced": gdrive_synced,
            "manifest": manifest
        }
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

def clean_old_backups(directory, keep_days=30):
    """Remove backup zips older than keep_days."""
    if not directory or not os.path.exists(directory):
        return 0
    now = datetime.now()
    deleted = 0
    for fname in os.listdir(directory):
        if fname.startswith("4brother_backup_") and fname.endswith(".zip"):
            fpath = os.path.join(directory, fname)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                if (now - mtime).days > keep_days:
                    os.remove(fpath)
                    deleted += 1
            except Exception:
                pass
    return deleted

if __name__ == "__main__":
    print("Running Full Backup (DB + Images to Google Drive)...")
    res = create_backup_archive(include_images=True)
    print("Completed successfully!")
    print(f"File: {res['zip_name']}")
    print(f"Size: {res['size_mb']} MB")
    print(f"Google Drive Synced: {res['gdrive_synced']}")
    print(f"Images: {res['manifest']['image_stats']}")
