"""
Локальный сервер для карты парковок
Запускает HTTP сервер для раздачи map.html и parkings.json
"""

import http.server
import socketserver
import os
from pathlib import Path

PORT = 8000
DIRECTORY = Path(__file__).parent

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)
    
    def end_headers(self):
        # Добавляем CORS заголовки
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET')
        super().end_headers()

if __name__ == "__main__":
    os.chdir(DIRECTORY)
    
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"🗺️ Сервер карты запущен")
        print(f"📍 Локально: http://localhost:{PORT}/map.html")
        print(f"📁 Раздаёт файлы из: {DIRECTORY}")
        print(f"\n⚠️ Для доступа извне запусти ngrok:")
        print(f"   ngrok http {PORT}")
        print(f"\n🔄 Остановить: Ctrl+C")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Сервер остановлен")
