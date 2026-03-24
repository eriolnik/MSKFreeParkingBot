"""
Telegram бот для поиска бесплатных парковок в Москве
Первая версия (MVP)
"""

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Optional
from datetime import datetime

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

# Загрузка переменных окружения из .env файла
load_dotenv()

# ==================== ЛОГИРОВАНИЕ ИЗМЕНЕНИЙ ====================

def log_bot_start():
    """Записать в ЗАМЕТКИ.md о запуске бота"""
    notes_path = Path(__file__).parent / "ЗАМЕТКИ.md"
    if notes_path.exists():
        timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
        try:
            with open(notes_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Проверяем, есть ли уже запись о сегодняшнем запуске
            today = datetime.now().strftime("%d %B %Y")
            if today not in content:
                new_entry = f"\n### {today}, {timestamp}\n- Бот запущен (очередная сессия разработки)\n"
                
                if "## 🔄 История изменений" in content:
                    parts = content.split("## 🔄 История изменений\n")
                    content = parts[0] + "## 🔄 История изменений\n" + new_entry + parts[1]
                
                with open(notes_path, 'w', encoding='utf-8') as f:
                    f.write(content)
        except Exception:
            pass  # Игнорируем ошибки логирования

# ==================== КОНФИГУРАЦИЯ ====================

# API-ключ Яндекс.Карт (из .env файла)
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")

# Токен бота от @BotFather (из .env файла)
BOT_TOKEN = os.getenv("BOT_TOKEN")

# URL карты (из .env, или локальный по умолчанию)
MAP_BASE_URL = os.getenv("MAP_BASE_URL", "http://localhost:8000")

# Путь к базе данных
DB_PATH = Path(__file__).parent / "parkings.db"

# Координаты центра Москвы (по умолчанию)
MOSCOW_CENTER = (55.751244, 37.618423)

# Проверка наличия токенов
if not YANDEX_API_KEY:
    raise ValueError("YANDEX_API_KEY не найден в .env файле")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле")

# ==================== БАЗА ДАННЫХ ====================


def init_db():
    """Инициализация базы данных"""
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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_use TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def add_parking(
    name: str,
    latitude: float,
    longitude: float,
    address: str,
    telegram_id: int,
    photo_id: Optional[str] = None,
) -> int:
    """Добавить парковку в базу"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        """
        INSERT INTO parkings (name, latitude, longitude, address, created_by, photo_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (name, latitude, longitude, address, str(telegram_id), photo_id),
    )
    
    parking_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return parking_id


def get_nearby_parkings(
    latitude: float, longitude: float, radius_km: float = 2.0
) -> list:
    """Получить парковки рядом с координатами"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Простой поиск по bounding box (для точности нужен Haversine)
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * abs(latitude / 90))
    
    cursor.execute(
        """
        SELECT id, name, latitude, longitude, address, is_free, created_at
        FROM parkings
        WHERE latitude BETWEEN ? AND ?
          AND longitude BETWEEN ? AND ?
        ORDER BY created_at DESC
        LIMIT 20
    """,
        (
            latitude - lat_delta,
            latitude + lat_delta,
            longitude - lon_delta,
            longitude + lon_delta,
        ),
    )
    
    results = cursor.fetchall()
    conn.close()
    
    return results


def register_user(telegram_id: int, username: Optional[str] = None):
    """Зарегистрировать пользователя"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        """
        INSERT OR IGNORE INTO users (telegram_id, username)
        VALUES (?, ?)
    """,
        (telegram_id, username),
    )
    
    conn.commit()
    conn.close()


# ==================== YANDEX API ====================


async def search_parkings_yandex(
    latitude: float, longitude: float, radius: int = 500
) -> list:
    """Поиск парковок через API Яндекс.Карт (GeoSearch)"""
    url = "https://geocode-maps.yandex.ru/1.x/"
    
    params = {
        "apikey": YANDEX_API_KEY,
        "format": "json",
        "geocode": f"{longitude},{latitude}",
        "scope": "near",
        "results": 10,
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                return parse_yandex_features(data)
            return []


def parse_yandex_features(data: dict) -> list:
    """Парсинг ответа Яндекс.GeoCode"""
    features = []
    
    try:
        response = data["response"]["GeoObjectCollection"]["featureMember"]
        
        for feature in response:
            obj = feature.get("GeoObject", {})
            name = obj.get("name", "Без названия")
            point = obj.get("Point", {})
            coords = point.get("pos", "").split()
            
            if len(coords) == 2:
                longitude, latitude = map(float, coords)
                address = obj.get("description", "")
                
                features.append(
                    {
                        "name": name,
                        "latitude": latitude,
                        "longitude": longitude,
                        "address": address,
                    }
                )
    except (KeyError, IndexError, ValueError):
        pass
    
    return features


async def get_map_image(
    latitude: float, longitude: float, zoom: int = 17, size: str = "600x400"
) -> Optional[bytes]:
    """Получить статичное изображение карты с меткой"""
    url = "https://static-maps.yandex.ru/1.x/"
    
    params = {
        'll': f'{longitude},{latitude}',
        'z': zoom,
        'l': 'map',  # тип карты: map, sat, trf
        'pt': f'{longitude},{latitude},pm2rdm',  # метка красного цвета
        'size': size,
        'apikey': YANDEX_API_KEY,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    return await response.read()
                return None
    except Exception:
        return None


def generate_street_view_link(latitude: float, longitude: float) -> str:
    """Генерация ссылки на вид улиц Яндекс.Карт"""
    return f"https://yandex.ru/maps/?ll={longitude},{latitude}&z=18&l=pano%2Cmap"


def generate_navigator_link(latitude: float, longitude: float) -> str:
    """Генерация ссылки на Яндекс.Карты с навигацией"""
    # Используем https ссылку вместо yandexnav://
    return f"https://yandex.ru/maps/?rtext=~{latitude},{longitude}&rtt=auto"


def generate_yandex_maps_link(latitude: float, longitude: float) -> str:
    """Генерация ссылки на Яндекс.Карты"""
    return f"https://yandex.ru/maps/?pt={longitude},{latitude}&z=18"


def generate_google_maps_link(latitude: float, longitude: float) -> str:
    """Генерация ссылки на Google Maps"""
    return f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"


# ==================== TELEGRAM WEB APP ====================


def create_map_webapp_url(latitude: float, longitude: float) -> str:
    """Создание URL для Web App с картой"""
    # Используем локальный сервер или ngrok
    return f"{MAP_BASE_URL}/map.html?lat={latitude}&lon={longitude}"


def create_parking_detail_keyboard(
    latitude: float, longitude: float, parking_id: int, name: str
) -> InlineKeyboardMarkup:
    """Клавиатура для конкретной парковки"""
    builder = InlineKeyboardBuilder()
    
    builder.button(
        text="🧭 Яндекс.Навигатор",
        url=generate_navigator_link(latitude, longitude),
    )
    builder.button(
        text="📍 Яндекс.Карты",
        url=generate_yandex_maps_link(latitude, longitude),
    )
    builder.button(
        text="🗺️ Google Maps",
        url=generate_google_maps_link(latitude, longitude),
    )
    builder.button(
        text="🏞️ Вид улиц",
        url=generate_street_view_link(latitude, longitude),
    )
    builder.button(text="📸 Панорама", callback_data=f"panorama_{latitude}_{longitude}")
    
    builder.adjust(2, 2, 1)
    return builder.as_markup()


# ==================== КЛАВИАТУРЫ ====================


def create_main_keyboard() -> types.ReplyKeyboardMarkup:
    """Основная клавиатура бота"""
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
    
    # Создаём обычную клавиатуру (не inline)
    keyboard = [
        [
            KeyboardButton(text="🅿️ Найти парковки рядом"),
        ],
        [
            KeyboardButton(text="➕ Добавить парковку"),
        ],
        [
            KeyboardButton(text="📍 Моё местоположение", request_location=True),
        ],
        [
            KeyboardButton(text="🗺️ Карта парковок"),
        ],
    ]
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def create_map_webapp_button(latitude: float = 55.751244, longitude: float = 37.618423) -> types.InlineKeyboardMarkup:
    """Создать inline кнопку с картой"""
    # Всегда отправляем ссылку на локальную карту
    return types.InlineKeyboardMarkup(
        inline_keyboard=[[
            types.InlineKeyboardButton(
                text="🗺️ Открыть карту (в браузере)",
                url=f"http://localhost:8000/map.html?lat={latitude}&lon={longitude}"
            )
        ]]
    )


def create_parking_keyboard(
    latitude: float, longitude: float, parking_id: Optional[int] = None
) -> InlineKeyboardMarkup:
    """Клавиатура для парковки"""
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🧭 Яндекс.Навигатор",
        url=generate_navigator_link(latitude, longitude),
    )
    builder.button(
        text="📍 Яндекс.Карты",
        url=generate_yandex_maps_link(latitude, longitude),
    )
    builder.button(
        text="🗺️ Google Maps",
        url=generate_google_maps_link(latitude, longitude),
    )
    builder.button(text="📸 Панорама", callback_data=f"panorama_{latitude}_{longitude}")

    if parking_id:
        builder.button(text="❌ Удалить", callback_data=f"delete_{parking_id}")

    builder.adjust(2, 2, 1)
    return builder.as_markup()


# ==================== БОТ ====================


async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    register_user(message.from_user.id, message.from_user.username)
    
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        "Я бот для поиска бесплатных парковок в Москве.\n\n"
        "Что я умею:\n"
        "• 🔍 Искать ближайшие бесплатные парковки\n"
        "• ➕ Добавлять новые парковки\n"
        "• 🗺️ Показывать карту с парковками\n"
        "• 📸 Показывать панорамы местности\n\n"
        "Отправь мне своё местоположение или выбери действие ниже!",
        reply_markup=create_main_keyboard(),
    )


async def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    await message.answer(
        "📖 **Как пользоваться ботом:**\n\n"
        "1. Отправь мне геолокацию (кнопка 📍 или через меню)\n"
        "2. Я найду ближайшие бесплатные парковки\n"
        "3. Выбери парковку и открой навигатор\n\n"
        "**Добавить парковку:**\n"
        "1. Нажми ➕ Добавить парковку\n"
        "2. Отправь геолокацию места\n"
        "3. Введи название и описание\n\n"
        "**Команды:**\n"
        "/start - Запустить бота\n"
        "/help - Помощь\n"
        "/map - Открыть карту парковок",
        parse_mode="Markdown"
    )


async def cmd_map(message: types.Message):
    """Обработчик команды /map - открывает Web App с картой"""
    await message.answer(
        "🗺️ **Карта парковок Москвы**\n\n"
        "Нажми на кнопку ниже чтобы открыть интерактивную карту:\n\n"
        "• Фильтр по типу парковок\n"
        "• Фильтр по радиусу\n"
        "• Навигация к парковке\n"
        "• Просмотр панорам",
        reply_markup=create_map_webapp_button(),
        parse_mode="Markdown"
    )


async def handle_location(message: types.Message):
    """Обработка полученной геолокации"""
    if not message.location:
        return

    latitude = message.location.latitude
    longitude = message.location.longitude

    # Ищем парковки в базе (радиус 3 км)
    parkings = get_nearby_parkings(latitude, longitude, radius_km=3.0)

    if parkings:
        # Показываем каждую парковку отдельным сообщением
        for i, parking in enumerate(parkings[:5], 1):  # Максимум 5
            parking_id, name, lat, lon, address, is_free, created_at = parking

            free_text = "🆓 Бесплатная" if is_free else "💰 Платная"
            actual_name = name if name and name != "Без названия" else f"Парковка #{parking_id}"

            # Расчёт расстояния (примерный)
            dist = abs(lat - latitude) * 111 + abs(lon - longitude) * 111 * 0.7

            text = f"🅿️ **{actual_name}** (~{dist:.1f} км)\n\n"
            text += f"{free_text}\n"
            text += f"📍 {address if address and address != 'Адрес не указан' else 'Адрес не указан'}\n\n"
            text += f"📍 Координаты: {lat:.6f}, {lon:.6f}"

            # Отправляем с клавиатурой
            await message.answer(
                text,
                reply_markup=create_parking_detail_keyboard(lat, lon, parking_id, actual_name),
                parse_mode="Markdown",
            )

        # Если больше 5 парковок, показываем сообщение
        if len(parkings) > 5:
            await message.answer(
                f"📊 **Ещё {len(parkings) - 5} парковок** в этом районе...\n\n"
                f"Всего найдено: **{len(parkings)}** (в радиусе 3 км)\n\n"
                f"Открой карту чтобы увидеть все: /map",
                parse_mode="Markdown"
            )
    else:
        await message.answer(
            f"📍 **Ваша позиция:** {latitude:.6f}, {longitude:.6f}\n\n"
            f"🔍 Ищу в радиусе 3 км...\n\n"
            "К сожалению, поблизости парковок пока нет.\n"
            "Попробуйте увеличить радиус или открыть карту: /map",
            reply_markup=create_main_keyboard(),
            parse_mode="Markdown",
        )


async def handle_menu_buttons(message: types.Message):
    """Обработчик кнопок меню"""
    text = message.text
    
    # Игнорируем сообщения с геолокацией
    if message.location:
        return

    if text == "🅿️ Найти парковки рядом":
        await message.answer(
            "📍 Отправьте мне вашу геолокацию!\n"
            "Нажмите на значок 📎 в поле ввода сообщения → Геолокация"
        )
    elif text == "➕ Добавить парковку":
        await message.answer(
            "➕ **Добавление парковки**\n\n"
            "1. Отправьте геолокацию места\n"
            "2. Введите название парковки\n"
            "3. Введите описание (необязательно)",
            parse_mode="Markdown"
        )
    elif text == "🗺️ Карта парковок":
        await message.answer(
            "🗺️ **Карта парковок Москвы**\n\n"
            "Открою интерактивную карту со всеми парковками.\n"
            "Можно фильтровать по типу и радиусу.",
            reply_markup=create_map_webapp_button(),
            parse_mode="Markdown"
        )


async def callback_handler(callback_query: types.CallbackQuery):
    """Обработчик callback-запросов"""
    data = callback_query.data

    if data == "find_nearby":
        await callback_query.answer(
            "Отправьте мне вашу геолокацию!\n"
            "Нажмите на значок 📎 в поле ввода сообщения",
            show_alert=True,
        )

    elif data == "add_parking":
        await callback_query.answer(
            "Отправьте геолокацию места парковки,\n"
            "затем введите название и описание",
            show_alert=True,
        )

    elif data == "share_location":
        await callback_query.answer(
            "Нажмите на значок 📎 → Геолокация",
            show_alert=True,
        )
    
    elif data.startswith("show_on_map_"):
        # Показать парковку на карте (Web App)
        parts = data.split("_")
        if len(parts) >= 5:
            parking_id = parts[3]
            lat = float(parts[4])
            lon = float(parts[5])
            
            webapp_url = create_map_webapp_url(lat, lon)
            
            await callback_query.answer(
                f"Открываю карту с парковкой #{parking_id}...",
                show_alert=False,
            )
            
            # Отправляем сообщение с Web App
            from aiogram.types import WebAppInfo
            await callback_query.message.answer(
                "🗺️ **Карта парковок**\n\n"
                f"Парковка #{parking_id} выделена на карте.",
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[[
                        types.InlineKeyboardButton(
                            text="🗺️ Открыть карту",
                            web_app=WebAppInfo(url=webapp_url)
                        )
                    ]]
                ),
                parse_mode="Markdown"
            )

    elif data.startswith("panorama_"):
        parts = data.split("_")
        if len(parts) == 3:
            lat = float(parts[1])
            lon = float(parts[2])

            await callback_query.answer("⏳ Загружаю панораму...")

            # Отправляем ссылку на Яндекс.Карты с панорамой
            street_view_url = generate_street_view_link(lat, lon)
            await callback_query.message.answer(
                f"📸 **Панорама для этой точки**\n\n"
                f"📍 {lat:.6f}, {lon:.6f}\n\n"
                f"Нажми на кнопку ниже чтобы открыть вид улиц:",
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[[
                        types.InlineKeyboardButton(
                            text="🗺️ Открыть вид улиц",
                            url=street_view_url
                        )
                    ]]
                ),
                parse_mode="Markdown"
            )
            
            await callback_query.answer()
            return

            await callback_query.answer("Панорама недоступна для этой точки", show_alert=True)

    elif data.startswith("delete_"):
        parking_id = int(data.split("_")[1])
        # Здесь должна быть логика удаления
        await callback_query.answer("Функция в разработке", show_alert=True)


# ==================== ЗАПУСК ====================


async def main():
    """Точка входа"""
    # Логирование запуска
    log_bot_start()
    
    # Инициализация БД
    init_db()

    # Настройка логирования
    import logging
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s',
        stream=sys.stdout
    )
    log = logging.getLogger(__name__)

    # Создание бота с прокси
    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.client.session.aiohttp import AiohttpSession
    
    log.info("Создаём бота...")
    
    # Получаем прокси из .env
    proxy_url = os.getenv("PROXY_URL")
    
    if proxy_url:
        log.info(f"🔗 Используем прокси: {proxy_url}")
        # Передаём прокси напрямую в сессию aiogram
        session = AiohttpSession(proxy=proxy_url)
    else:
        log.info("Прокси не найден, используем прямое подключение")
        session = AiohttpSession()
    
    bot = Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    log.info("Проверяем токен...")
    try:
        me = await bot.get_me()
        log.info(f"✓ Бот: @{me.username} - {me.first_name}")
    except Exception as e:
        log.error(f"✗ Ошибка проверки токена: {e}")
        log.error("Попробуй включить VPN или прокси")
        raise
    
    dp = Dispatcher()

    # Регистрация хендлеров (ВАЖНО: порядок имеет значение!)
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_map, Command("map"))
    
    # СНАЧАЛА геолокация (чтобы срабатывала сразу)
    dp.message.register(handle_location, lambda msg: msg.location is not None)
    
    # Потом callback
    dp.callback_query.register(callback_handler)
    
    # Потом кнопки меню (с фильтром чтобы не перехватывать location)
    async def menu_buttons_filter(message: types.Message):
        return message.text in ["🅿️ Найти парковки рядом", "➕ Добавить парковку", "🗺️ Карта парковок"] and message.location is None
    
    dp.message.register(handle_menu_buttons, menu_buttons_filter)

    # В конце echo для всего остального
    async def echo(message: types.Message):
        await message.answer("📍 Отправьте мне геолокацию (кнопка 📎 → Геолокация)")

    dp.message.register(echo)

    # Запуск
    log.info("🤖 Бот запускается...")
    
    # Отключаем вебхуки
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("✓ Webhook удалён")
    
    # Запускаем polling
    log.info("✓ Запускаем polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
