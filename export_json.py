"""Экспорт парковок в JSON для карты"""
import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "parkings.db"
JSON_PATH = Path(__file__).parent / "parkings.json"

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

c.execute("""
    SELECT id, name, latitude, longitude, address, is_free
    FROM parkings
    ORDER BY created_at DESC
""")

parkings = []
for row in c.fetchall():
    parkings.append({
        "id": row[0],
        "name": row[1] if row[1] and row[1] != "Без названия" else f"Парковка #{row[0]}",
        "latitude": row[2],
        "longitude": row[3],
        "address": row[4] or "Адрес не указан",
        "is_free": bool(row[5])
    })

conn.close()

# Записываем с UTF-8 кодировкой
with open(JSON_PATH, 'w', encoding='utf-8') as f:
    json.dump(parkings, f, ensure_ascii=False, indent=2)

print(f"✅ Экспортировано {len(parkings)} парковок в {JSON_PATH}")
print(f"📊 Бесплатных: {sum(1 for p in parkings if p['is_free'])}")
print(f"📊 Платных: {sum(1 for p in parkings if not p['is_free'])}")
