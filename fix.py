import sqlite3

def fix_db_schema():
    conn = sqlite3.connect('gps_tracker.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Fix coordinates table - add missing columns
    cursor.execute("PRAGMA table_info(coordinates)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'session_id' not in columns:
        cursor.execute("ALTER TABLE coordinates ADD COLUMN session_id TEXT")
        print("✅ Added session_id column")
    
    if 'nearby_landmarks' not in columns:
        cursor.execute("ALTER TABLE coordinates ADD COLUMN nearby_landmarks TEXT")
        print("✅ Added nearby_landmarks column")
    
    if 'city' not in columns:
        cursor.execute("ALTER TABLE coordinates ADD COLUMN city TEXT")
        print("✅ Added city column")
    
    if 'state' not in columns:
        cursor.execute("ALTER TABLE coordinates ADD COLUMN state TEXT")
        print("✅ Added state column")
    
    if 'country' not in columns:
        cursor.execute("ALTER TABLE coordinates ADD COLUMN country TEXT")
        print("✅ Added country column")
    
    if 'postal_code' not in columns:
        cursor.execute("ALTER TABLE coordinates ADD COLUMN postal_code TEXT")
        print("✅ Added postal_code column")
    
    if 'street' not in columns:
        cursor.execute("ALTER TABLE coordinates ADD COLUMN street TEXT")
        print("✅ Added street column")
    
    # Fix users table
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'nearby_landmarks' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN nearby_landmarks TEXT")
        print("✅ Added nearby_landmarks to users")
    
    conn.commit()
    conn.close()
    print("✅ Database schema fixed for 2026")

# Run this first
fix_db_schema()
