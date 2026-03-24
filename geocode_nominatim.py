"""
Геокодинг парковок через Nominatim (OpenStreetMap)
Бесплатно, без API ключа
"""

import sqlite3
import requests
import time
from pathlib import Path
from urllib.parse import quote

DB_PATH = Path(__file__).parent / "parkings.db"

# Nominatim API (требует User-Agent)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {
    'User-Agent': 'FreeParkingBot/1.0 (moscow parking lookup)'
}

def get_address_from_nominatim(latitude, longitude):
    """Получить адрес по координатам через Nominatim"""
    
    params = {
        'lat': latitude,
        'lon': longitude,
        'zoom': 18,
        'addressdetails': 1,
        'format': 'json'
    }
    
    try:
        response = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Собираем адрес
        address = data.get('address', {})
        
        # Формируем читаемый адрес
        road = address.get('road', '')
        house_number = address.get('house_number', '')
        suburb = address.get('suburb', '')
        city = address.get('city', address.get('town', ''))
        
        parts = []
        if road:
            parts.append(road)
        if house_number:
            parts.append(house_number)
        
        if parts:
            return ', '.join(parts)
        elif suburb:
            return f"район {suburb}"
        elif city:
            return city
        
        return data.get('display_name', '')[:100]
        
    except Exception as e:
        print(f"  Ошибка геокодинга: {e}")
        return None

def update_addresses():
    """Обновить адреса у парковок без адресов"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Находим парковки без адреса (только бесплатные)
    cursor.execute("""
        SELECT id, latitude, longitude 
        FROM parkings 
        WHERE (address IS NULL OR address = '' OR address = 'Адрес не указан')
          AND is_free = 1
        LIMIT 100
    """)
    
    parkings = cursor.fetchall()
    print(f"🔍 Найдено {len(parkings)} парковок без адреса")
    
    updated = 0
    errors = 0
    
    for i, (parking_id, lat, lon) in enumerate(parkings, 1):
        print(f"[{i}/{len(parkings)}] Геокодинг парковки #{parking_id} ({lat:.4f}, {lon:.4f})")
        
        address = get_address_from_nominatim(lat, lon)
        
        if address:
            cursor.execute("""
                UPDATE parkings 
                SET address = ? 
                WHERE id = ?
            """, (address, parking_id))
            conn.commit()
            print(f"  ✅ {address}")
            updated += 1
        else:
            print(f"  ❌ Не найден адрес")
            errors += 1
        
        # Пауза чтобы не превысить лимит (1 запрос в секунду)
        time.sleep(1.1)
        
        # Большая пауза каждые 20 запросов
        if i % 20 == 0:
            print("⏳ Пауза 10 секунд...")
            time.sleep(10)
    
    conn.close()
    
    print(f"\n✅ Готово!")
    print(f"   Обновлено: {updated}")
    print(f"   Ошибок: {errors}")

if __name__ == "__main__":
    update_addresses()
