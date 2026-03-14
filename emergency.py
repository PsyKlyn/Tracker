# emergency_unlock.py
import sqlite3
import os

# Delete lock files
for file in ['gps_tracker.db-wal', 'gps_tracker.db-shm', 'gps_tracker.db-journal']:
    if os.path.exists(file):
        os.remove(file)
        print(f"🗑️  Removed {file}")

print("🔓 Database unlocked - now run fix.py")
