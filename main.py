from aiogram import Bot, Dispatcher, types
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import feedparser
import logging
import os
import socket
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram.filters.command import CommandStart
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('Bot')

# Переменные окружения
API_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
RSS_URL = os.getenv('RSS_URL', 'https://ru.tradingview.com/feed/')
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv('PORT', 10000))
WEBHOOK_PATH = "/webhook"
BASE_WEBHOOK_URL = os.getenv('WEBHOOK_URL')

# Проверка обязательных переменных окружения
if not all([API_TOKEN, CHANNEL_ID, BASE_WEBHOOK_URL]):
    logger.error("Требуемые переменные окружения не установлены!")
    exit(1)

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
sent_news_ids = set()

# Проверка доступности порта
def check_port_availability(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        logger.info(f"Порт {port} свободен и доступен")
        return True
    except OSError as e:
        logger.error(f"Порт {port} занят: {e}")
        return False
    finally:
        sock.close()

# Получение последних новостей из RSS-ленты
def get_latest_news():
    try:
        rss_feed = feedparser.parse(RSS_URL)
        if not rss_feed.entries:
            logger.info("Новых записей в RSS-ленте не найдено")
            return []
        
        latest_entries = rss_feed.entries[:3]
        news_items = []
        for entry in latest_entries:
            news_id = entry.get('guid') or entry.link
            if news_id not in sent_news_ids:
                news_items.append({
                    'title': entry.title,
                    'summary': entry.summary,
                    'image_url': entry.media_content[0]['url'] if 'media_content' in entry else None,
                    'id': news_id
                })
        return news_items
    except Exception as e:
        logger.error(f"Ошибка при парсинге RSS-ленты: {e}")
        return []

# Отправка новости в канал
async def send_to_channel(news_item):
    if news_item['id'] in sent_news_ids:
        logger.info(f"Новость уже отправлена: {news_item['title']}")
        return
        
    sent_news_ids.add(news_item['id'])
    caption = f"{news_item['title']}\n\n{news_item['summary']}"
    
    try:
        if news_item['image_url']:
            await bot.send_photo(CHANNEL_ID, photo=news_item['image_url'], caption=caption)
        else:
            await bot.send_message(CHANNEL_ID, caption)
        logger.info(f"Новость отправлена: {news_item['title']}")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")

# Проверка новых новостей по расписанию
async def check_for_new_news():
    logger.info("Проверка новых новостей")
    news_items = get_latest_news()
    for news_item in news_items:
        await send_to_channel(news_item)

# Действия при запуске бота
async def on_startup(bot: Bot):
    webhook_url = f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}"
    try:
        await bot.set_webhook(webhook_url)
        logger.info(f"Вебхук установлен: {webhook_url}")
        me = await bot.get_me()
        logger.info(f"Бот подключен: {me.username}")
    except Exception as e:
        logger.error(f"Ошибка настройки вебхука или подключения к Telegram: {e}")
        raise

# Действия при остановке бота
async def on_shutdown(app: web.Application):
    try:
        await bot.delete_webhook()
        logger.info("Вебхук удален")
    except Exception as e:
        logger.error(f"Ошибка удаления вебхука: {e}")
    
    if hasattr(bot, 'session'):
        await bot.session.close()
        logger.info("Сессия бота закрыта")
    
    scheduler.shutdown(wait=False)
    logger.info("Планировщик остановлен")

# Обработчик команды /start
@dp.message(CommandStart())
async def start_command(message: types.Message):
    await message.reply("Бот запущен и работает по расписанию!")

# Основная функция запуска
async def main():
    # Проверка доступности порта перед запуском
    if not check_port_availability(WEB_SERVER_HOST, WEB_SERVER_PORT):
        logger.error(f"Не удалось запустить бота: порт {WEB_SERVER_PORT} недоступен")
        return

    # Настройка планировщика
    scheduler.add_job(
        check_for_new_news,
        CronTrigger(hour="9-20", minute="*/5", day_of_week="mon-fri", timezone="Europe/Moscow")
    )
    scheduler.start()
    logger.info("Планировщик запущен")

    # Создание веб-приложения
    app = web.Application()
    app.on_shutdown.append(on_shutdown)

    # Настройка вебхука
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    # Создание сокета с SO_REUSEADDR
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((WEB_SERVER_HOST, WEB_SERVER_PORT))
        sock.listen(1)
        sock.setblocking(False)
        logger.info(f"Сокет привязан к {WEB_SERVER_HOST}:{WEB_SERVER_PORT}")
    except Exception as e:
        logger.error(f"Ошибка привязки сокета: {e}")
        sock.close()
        raise

    # Запуск веб-сервера
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, sock=sock)
    try:
        await site.start()
        logger.info(f"Сервер запущен на {WEB_SERVER_HOST}:{WEB_SERVER_PORT}")
    except Exception as e:
        logger.error(f"Ошибка запуска сервера: {e}")
        await runner.cleanup()
        raise

    # Выполнение задач при запуске
    await on_startup(bot)

    # Удержание приложения в работе
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        logger.info("Приложение завершает работу")
    finally:
        await site.stop()
        await runner.cleanup()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Программа завершена пользователем")
    except Exception as e:
        logger.error(f"Непредвиденная ошибка: {e}")
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()