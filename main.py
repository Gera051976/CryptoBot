# main.py
from aiogram import Bot, Dispatcher, types
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import feedparser
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.filters.command import CommandStart
from dotenv import load_dotenv
import os
import asyncio

# Загрузка переменных окружения из .env (для локальной разработки)
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

# Получение последних новостей из RSS-ленты
def get_latest_news():
    try:
        rss_feed = feedparser.parse(RSS_URL)
        if not rss_feed.entries:
            logger.info("Новых записей в RSS-ленте не найдено")
            return []
        
        # Проверяем последние 3 записи
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
    await bot.set_webhook(f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}")
    logger.info("Вебхук установлен: %s", f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}")
    try:
        me = await bot.get_me()
        logger.info(f"Бот подключен: {me.username}")
    except Exception as e:
        logger.error(f"Ошибка подключения к Telegram: {e}")

# Действия при остановке бота
async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()
    scheduler.shutdown(wait=False)  # Немедленное завершение задач
    logger.info("Бот остановлен")

# Обработчик команды /start
@dp.message(CommandStart())
async def start_command(message: types.Message):
    await message.reply("Бот запущен и работает по расписанию!")

# Настройка планировщика
def setup_scheduler():
    scheduler.add_job(
        check_for_new_news,
        'cron',
        day_of_week='mon-fri',  # Отправка с понедельника по пятницу
        hour='10-20',           # Каждый час с 10:00 до 20:00
        minute='0',             # На нулевой минуте каждого часа
        timezone='Europe/Moscow'
    )

# Основная функция запуска
async def main():
    # Настройка планировщика
    setup_scheduler()
    scheduler.start()

    # Создание веб-приложения
    app = web.Application()
    app.on_shutdown.append(on_shutdown)

    # Настройка вебхука
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    # Запуск бота
    await on_startup(bot)

    # Запуск веб-сервера
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_SERVER_HOST, WEB_SERVER_PORT)
    await site.start()

    logger.info(f"Сервер запущен на {WEB_SERVER_HOST}:{WEB_SERVER_PORT}")

    # Держим приложение работающим
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        logger.info("Приложение завершено")
    finally:
        await runner.cleanup()

if __name__ == '__main__':
    # Используем существующий событийный цикл
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Программа завершена пользователем")
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()