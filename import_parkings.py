"""
Скрипт для загрузки парковок Москвы из OpenStreetMap
"""

import sqlite3
import requests
from pathlib import Path

DB_PATH = Path(__file__).parent / "parkings.db"

# Координаты Москвы (центр + радиус ~15км)
MOSCOW_BOUNDS = {
    "north": 55.95,
    "south": 55.55,
    "west": 37.35,
    "east": 37.95
}

# Overpass API запрос для поиска парковок
OVERPASS_QUERY = """
[out:json][timeout:60];
(
  way["amenity"="parking"]({south},{west},{north},{east});
  relation["amenity"="parking"]({south},{west},{north},{east});
  node["amenity"="parking"]({south},{west},{north},{east});
);
out center;
""".format(**MOSCOW_BOUNDS)


def fetch_parkings_from_osm():
    """Получить парковки из OpenStreetMap через Overpass API"""
    print("🔍 Запрос парковок из OpenStreetMap...")
    
    url = "https://overpass-api.de/api/interpreter"
    
    try:
        response = requests.post(url, data={'data': OVERPASS_QUERY}, timeout=120)
        response.raise_for_status()
        data = response.json()
        return data.get('elements', [])
    except Exception as e:
        print(f"❌ Ошибка запроса: {e}")
        return []


def parse_parking(element):
    """Распарсить элемент парковки"""
    tags = element.get('tags', {})
    
    # Получаем название
    name = tags.get('name', tags.get('addr:street', 'Без названия'))
    
    # Координаты
    if 'center' in element:  # Для way/relation
        lat = element['center'].get('lat')
        lon = element['center'].get('lon')
    elif 'lat' in element and 'lon' in element:  # Для node
        lat = element['lat']
        lon = element['lon']
    else:
        return None
    
    if not lat or not lon:
        return None
    
    # Адрес (собираем из компонентов)
    address_parts = []
    if tags.get('addr:street'):
        address_parts.append(tags.get('addr:street'))
    if tags.get('addr:housenumber'):
        address_parts.append(tags.get('addr:housenumber'))
    if tags.get('addr:district'):
        address_parts.append(tags.get('addr:district'))
    
    address = ', '.join(address_parts) if address_parts else tags.get('addr:full', '')
    
    # Тип парковки
    parking_type = tags.get('parking', 'surface')
    fee = tags.get('fee', 'no')  # 'yes', 'no', 'interval'
    access = tags.get('access', 'yes')  # 'yes', 'customers', 'private'
    
    # Проверяем шлагбаум
    barrier = tags.get('barrier', '')
    has_barrier = barrier in ['gate', 'barrier', 'lift_gate', 'sliding_gate']
    
    # Определяем бесплатная ли и без шлагбаума
    is_free = fee.lower() in ['no', 'interval'] and access.lower() == 'yes' and not has_barrier
    
    # Вместимость
    capacity = tags.get('capacity', '')
    
    # Сохраняем все теги для последующего геокодинга
    return {
        'name': name,
        'latitude': lat,
        'longitude': lon,
        'address': address,
        'is_free': 1 if is_free else 0,
        'parking_type': parking_type,
        'capacity': capacity,
        'barrier': barrier,
        'fee': fee,
        'access': access,
        'tags': tags
    }


def init_db():
    """Инициализация БД (если нужно)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parkings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            address TEXT,
            is_free INTEGER DEFAULT 1,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            photo_id TEXT,
            parking_type TEXT,
            capacity TEXT
        )
    """)
    
    conn.commit()
    conn.close()


def add_parking_to_db(parking):
    """Добавить парковку в базу"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Проверяем дубликаты (по координатам с точностью до 0.0001)
    cursor.execute("""
        SELECT id FROM parkings 
        WHERE ABS(latitude - ?) < 0.0001 AND ABS(longitude - ?) < 0.0001
    """, (parking['latitude'], parking['longitude']))
    
    if cursor.fetchone():
        conn.close()
        return False  # Уже есть
    
    cursor.execute("""
        INSERT INTO parkings (name, latitude, longitude, address, is_free, created_by, parking_type, capacity)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        parking['name'],
        parking['latitude'],
        parking['longitude'],
        parking['address'],
        parking['is_free'],
        'osm_import',  # Помечаем что импортировано
        parking.get('parking_type', ''),
        parking.get('capacity', '')
    ))
    
    conn.commit()
    conn.close()
    return True


def main():
    print("🚀 Импорт парковок Москвы из OpenStreetMap\n")
    print("📋 Фильтры:")
    print("   ✅ Только бесплатные")
    print("   ✅ Без шлагбаумов")
    print("   ✅ Свободный доступ\n")
    
    # Инициализация БД
    init_db()
    
    # Получаем парковки
    elements = fetch_parkings_from_osm()
    
    if not elements:
        print("❌ Парковки не найдены")
        return
    
    print(f"📦 Найдено элементов: {len(elements)}")
    
    # Парсим и добавляем
    added = 0
    skipped = 0
    free_count = 0
    barrier_count = 0
    
    for element in elements:
        parking = parse_parking(element)
        
        if not parking:
            skipped += 1
            continue
        
        # Пропускаем с шлагбаумом
        if parking.get('barrier'):
            barrier_count += 1
            continue
        
        if add_parking_to_db(parking):
            added += 1
            if parking['is_free']:
                free_count += 1
            
            # Выводим прогресс
            if added % 50 == 0:
                print(f"  Добавлено: {added} (бесплатных без шлагбаума: {free_count})")
        else:
            skipped += 1
    
    print(f"\n✅ Готово!")
    print(f"   Добавлено: {added}")
    print(f"   Пропущено (дубликаты): {skipped}")
    print(f"   Отфильтровано (шлагбаумы): {barrier_count}")
    print(f"   Бесплатных без шлагбаума: {free_count}")
    print(f"\n📍 База данных: {DB_PATH.absolute()}")


if __name__ == "__main__":
    main()
