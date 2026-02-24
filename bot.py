import logging
import os
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from dotenv import load_dotenv
import gitlab
from collections import defaultdict


# ==== ФУНКЦИЯ ДЛЯ УДАЛЕНИЯ BOM (невидимых символов) ====
def remove_bom_from_env():
    """Удаляет BOM из файла .env если он есть"""
    env_path = '.env'
    try:
        with open(env_path, 'rb') as f:
            content = f.read()

        # Проверяем наличие BOM (первые 3 байта: EF BB BF)
        if content.startswith(b'\xef\xbb\xbf'):
            with open(env_path, 'wb') as f:
                f.write(content[3:])
            logging.info("✅ BOM символ удален из .env")
            return True
    except Exception as e:
        logging.error(f"Ошибка при проверке BOM: {e}")
    return False


# Удаляем BOM если есть
remove_bom_from_env()

# Загружаем переменные окружения
load_dotenv(encoding='utf-8')

# ==== Проверка переменных ====
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
GITLAB_URL = os.getenv('GITLAB_URL')
GITLAB_TOKEN = os.getenv('GITLAB_TOKEN')
PROJECT_ID = os.getenv('PROJECT_ID')

if not all([BOT_TOKEN, CHAT_ID, GITLAB_URL, GITLAB_TOKEN, PROJECT_ID]):
    missing = []
    if not BOT_TOKEN: missing.append("BOT_TOKEN")
    if not CHAT_ID: missing.append("CHAT_ID")
    if not GITLAB_URL: missing.append("GITLAB_URL")
    if not GITLAB_TOKEN: missing.append("GITLAB_TOKEN")
    if not PROJECT_ID: missing.append("PROJECT_ID")
    raise ValueError(f"❌ Не все переменные окружения заданы! Отсутствуют: {', '.join(missing)}")

# ==== Настройка логирования ====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# ==== Инициализация ====
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==== Подключение к GitLab ====
try:
    gl = gitlab.Gitlab(GITLAB_URL, private_token=GITLAB_TOKEN)
    gl.auth()
    logging.info(f"✅ Успешное подключение к GitLab: {GITLAB_URL}")

    project = gl.projects.get(PROJECT_ID)
    logging.info(f"✅ Проект найден: {project.name}")
except Exception as e:
    logging.error(f"❌ Ошибка при подключении к GitLab: {e}")
    raise

# ==== Настройки ====
INACTIVITY_DAYS = 2
CHECK_INACTIVITY_INTERVAL = 3600

# ==== Хранилища ====
last_checked = {
    'commits': {},
    'merge_requests': {},
    'issues': {},
    'inactive_members': {}
}

members_activity = defaultdict(lambda: {'last_activity': None, 'notified': False})


# ==== Команды бота ====
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        f"👋 <b>Бот для отслеживания GitLab</b>\n\n"
        f"📁 Проект: {project.name}\n"
        f"🌐 GitLab: {GITLAB_URL}\n\n"
        f"✅ Бот успешно запущен!\n"
        f"⏰ Проверка каждые 30 секунд\n"
        f"⚠️ Неактивность: более {INACTIVITY_DAYS} дней\n\n"
        f"Команды:\n"
        f"/status - статус бота\n"
        f"/members - активность участников\n"
        f"/inactive - неактивные участники",
        parse_mode="HTML"
    )


@dp.message(Command("status"))
async def cmd_status(message: Message):
    await message.answer(
        f"✅ <b>Статус бота</b>\n\n"
        f"📁 Проект: {project.name}\n"
        f"📊 Проверка: каждые 30 сек\n"
        f"⏰ Неактивность: > {INACTIVITY_DAYS} дней\n"
        f"📝 Событий в кэше: {sum(len(v) for v in last_checked.values() if isinstance(v, dict))}",
        parse_mode="HTML"
    )


@dp.message(Command("members"))
async def cmd_members(message: Message):
    try:
        if not members_activity:
            await message.answer("❌ Данные об активности еще собираются...")
            return

        response = "👥 <b>Активность участников:</b>\n\n"
        now = datetime.now()

        sorted_members = sorted(
            members_activity.items(),
            key=lambda x: x[1]['last_activity'] if x[1]['last_activity'] else datetime.min,
            reverse=True
        )

        for username, data in sorted_members[:10]:
            if data['last_activity']:
                days_ago = (now - data['last_activity']).days
                if days_ago > INACTIVITY_DAYS:
                    status = f"⚠️ Неактивен {days_ago} дн"
                else:
                    status = f"✅ Активен {days_ago} дн назад"
            else:
                status = "❌ Нет данных"

            response += f"👤 <b>{username}</b>\n{status}\n\n"

        await message.answer(response, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("inactive"))
async def cmd_inactive(message: Message):
    try:
        if not members_activity:
            await message.answer("❌ Данные об активности еще собираются...")
            return

        now = datetime.now()
        inactive_members = []

        for username, data in members_activity.items():
            if data['last_activity']:
                days_ago = (now - data['last_activity']).days
                if days_ago > INACTIVITY_DAYS:
                    inactive_members.append((username, days_ago))

        if inactive_members:
            response = f"⚠️ <b>Неактивные участники (> {INACTIVITY_DAYS} дн):</b>\n\n"
            for username, days in sorted(inactive_members, key=lambda x: x[1], reverse=True)[:10]:
                response += f"👤 {username} — {days} дн\n"
        else:
            response = "✅ Все участники активны!"

        await message.answer(response, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# ==== Функции проверки GitLab ====
async def check_new_commits():
    try:
        since_time = (datetime.now() - timedelta(minutes=10)).isoformat()
        commits = project.commits.list(since=since_time, all=True)

        for commit in commits:
            commit_id = commit.id
            if commit_id not in last_checked['commits']:
                last_checked['commits'][commit_id] = True

                if commit.author_name:
                    members_activity[commit.author_name]['last_activity'] = datetime.now()
                    members_activity[commit.author_name]['notified'] = False

                message = (
                    f"📦 <b>Новый коммит</b>\n"
                    f"📝 Проект: {project.name}\n"
                    f"👤 Автор: {commit.author_name}\n"
                    f"💬 {commit.title}\n"
                    f"🆔 {commit_id[:8]}\n"
                    f"🔗 {commit.web_url}"
                )
                await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML")
                await asyncio.sleep(1)
    except Exception as e:
        logging.error(f"Ошибка при проверке коммитов: {e}")


async def check_new_merge_requests():
    try:
        mrs = project.mergerequests.list(state='opened', order_by='created_at', sort='desc')

        for mr in mrs:
            mr_id = mr.iid
            if mr_id not in last_checked['merge_requests']:
                last_checked['merge_requests'][mr_id] = True

                if mr.author and mr.author.get('name'):
                    members_activity[mr.author['name']]['last_activity'] = datetime.now()
                    members_activity[mr.author['name']]['notified'] = False

                author_name = mr.author.get('name', 'Unknown') if mr.author else 'Unknown'

                message = (
                    f"🔄 <b>Новый Merge Request</b>\n"
                    f"📝 Проект: {project.name}\n"
                    f"🔀 !{mr.iid}: {mr.title}\n"
                    f"👤 {author_name}\n"
                    f"🌿 {mr.source_branch} → {mr.target_branch}\n"
                    f"🔗 {mr.web_url}"
                )
                await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML")
                await asyncio.sleep(1)
    except Exception as e:
        logging.error(f"Ошибка при проверке MR: {e}")


async def check_new_issues():
    try:
        issues = project.issues.list(state='opened', order_by='created_at', sort='desc')

        for issue in issues:
            issue_id = issue.iid
            if issue_id not in last_checked['issues']:
                last_checked['issues'][issue_id] = True

                if issue.author and issue.author.get('name'):
                    members_activity[issue.author['name']]['last_activity'] = datetime.now()
                    members_activity[issue.author['name']]['notified'] = False

                author_name = issue.author.get('name', 'Unknown') if issue.author else 'Unknown'

                message = (
                    f"🎯 <b>Новый Issue</b>\n"
                    f"📝 Проект: {project.name}\n"
                    f"#{issue.iid}: {issue.title}\n"
                    f"👤 {author_name}\n"
                    f"🔗 {issue.web_url}"
                )
                await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML")
                await asyncio.sleep(1)
    except Exception as e:
        logging.error(f"Ошибка при проверке issues: {e}")


async def check_pipelines():
    try:
        pipelines = project.pipelines.list(order_by='updated_at', sort='desc', per_page=5)

        for pipeline in pipelines:
            status = pipeline.status
            emoji = {
                'success': '✅', 'failed': '❌', 'canceled': '🚫',
                'running': '🔄', 'pending': '⏳', 'skipped': '⏭️',
                'created': '🆕', 'manual': '👤'
            }.get(status, '🔄')

            cache_key = f"pipeline_{pipeline.id}_{status}"
            if cache_key not in last_checked:
                last_checked[cache_key] = True

                message = (
                    f"{emoji} <b>Pipeline {status}</b>\n"
                    f"📝 Проект: {project.name}\n"
                    f"🌿 {pipeline.ref}\n"
                    f"🆔 #{pipeline.id}\n"
                    f"🔗 {pipeline.web_url}"
                )
                await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML")
                await asyncio.sleep(1)
    except Exception as e:
        logging.error(f"Ошибка при проверке pipeline: {e}")


async def check_inactive_members():
    try:
        logging.info("🔍 Проверка неактивных участников...")
        now = datetime.now()
        inactive_members = []

        for username, data in members_activity.items():
            if data['last_activity']:
                days_inactive = (now - data['last_activity']).days

                if days_inactive >= INACTIVITY_DAYS and not data['notified']:
                    inactive_members.append((username, days_inactive))
                    data['notified'] = True
                elif days_inactive < INACTIVITY_DAYS and data['notified']:
                    data['notified'] = False

        if inactive_members:
            response = f"⚠️ <b>Неактивные участники (> {INACTIVITY_DAYS} дн):</b>\n\n"
            for username, days in inactive_members:
                response += f"👤 {username} — {days} дн\n"

            await bot.send_message(chat_id=CHAT_ID, text=response, parse_mode="HTML")
            logging.info(f"Отправлено уведомление о {len(inactive_members)} неактивных участниках")
    except Exception as e:
        logging.error(f"Ошибка при проверке неактивных: {e}")


async def gitlab_poller():
    logging.info("🚀 Запуск Polling для GitLab...")
    last_inactivity_check = datetime.now()

    while True:
        try:
            await check_new_commits()
            await check_new_merge_requests()
            await check_new_issues()
            await check_pipelines()

            if (datetime.now() - last_inactivity_check).seconds >= CHECK_INACTIVITY_INTERVAL:
                await check_inactive_members()
                last_inactivity_check = datetime.now()

            # Очистка старых записей
            for key in list(last_checked.keys()):
                if isinstance(last_checked[key], dict) and len(last_checked[key]) > 200:
                    keys = list(last_checked[key].keys())
                    for old_key in keys[:-200]:
                        del last_checked[key][old_key]

            await asyncio.sleep(30)
        except Exception as e:
            logging.error(f"❌ Ошибка в poller: {e}")
            await asyncio.sleep(60)


async def main():
    # Запускаем поллер в фоне
    asyncio.create_task(gitlab_poller())

    logging.info(f"📢 Бот запущен! Чат: {CHAT_ID}")
    logging.info(f"⏰ Проверка неактивности: > {INACTIVITY_DAYS} дней")

    # Запускаем бота
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("👋 Бот остановлен")
    except Exception as e:
        logging.error(f"❌ Критическая ошибка: {e}")