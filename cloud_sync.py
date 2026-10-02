import sqlite3
import psycopg2
import os

SUPABASE_URI = "postgresql://postgres.ukgzcdrrhwzbnbocgfnm:2EBPsBnWv5NiZNVV@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"

def download_db(target_path="xwar.db"):
    """Downloads the latest DB backup from Supabase."""
    print("[CloudSync] Checking for cloud database backup...")
    try:
        conn = psycopg2.connect(SUPABASE_URI)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sqlite_backup (
                id INTEGER PRIMARY KEY,
                db_data BYTEA,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        cur.execute("SELECT db_data, updated_at FROM sqlite_backup WHERE id = 1")
        row = cur.fetchone()
        
        if row and row[0]:
            print(f"[CloudSync] Cloud backup found (Last updated: {row[1]}). Downloading...")
            with open(target_path, 'wb') as f:
                f.write(row[0])
            print("[CloudSync] DB Download complete.")
        else:
            print("[CloudSync] No cloud backup found. Starting fresh.")
            
        conn.close()
    except Exception as e:
        print(f"[CloudSync] Error downloading DB: {e}")

def upload_db(source_db_conn):
    """Safely backups the current SQLite connection and uploads to Supabase."""
    print("[CloudSync] Uploading database backup to cloud...")
    try:
        # Create a safe backup file to avoid locking/transaction issues
        temp_db = sqlite3.connect("temp_backup.db")
        source_db_conn.backup(temp_db)
        temp_db.close()
        
        # Read the binary data
        with open("temp_backup.db", 'rb') as f:
            db_data = f.read()
            
        # Upload to Postgres
        conn = psycopg2.connect(SUPABASE_URI)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sqlite_backup (
                id INTEGER PRIMARY KEY,
                db_data BYTEA,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            INSERT INTO sqlite_backup (id, db_data, updated_at) 
            VALUES (1, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE SET db_data = EXCLUDED.db_data, updated_at = CURRENT_TIMESTAMP
        """, (db_data,))
        conn.commit()
        conn.close()
        
        # Cleanup
        os.remove("temp_backup.db")
        print("[CloudSync] DB Upload successful!")
    except Exception as e:
        print(f"[CloudSync] Error uploading DB: {e}")
