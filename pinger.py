import asyncio, os, telegram, csv, configparser, logging, sys
from datetime import datetime
from telegram.error import NetworkError, TimedOut, RetryAfter

# Налаштування логування
logging.basicConfig(level=logging.INFO, filename="pinger.log", filemode="w", format="%(asctime)s %(levelname)s [%(funcName)s]: %(message)s")

buildings_status = {}
ip_states = {}

def read_ip_file():
    ip_list = []
    try:
        with open("ip.csv", "r", encoding="utf-8") as ip_csv:
            reader = csv.reader(ip_csv)
            for row in reader:
                if len(row) == 2:
                    ip, building = row[0], row[1]
                    ip_list.append([ip, building])
                    if building not in buildings_status:
                        buildings_status[building] = {"total": 0, "down": 0, "alert_sent": False}
                    buildings_status[building]["total"] += 1
                    ip_states[ip] = "up"
        return ip_list
    except Exception as e:
        logging.error(f"Помилка CSV: {e}")
        return None

async def ping(host):
    timeout_sec = 1
    command = ['ping', '-n' if os.name == 'nt' else '-c', '1', '-w' if os.name == 'nt' else '-W', str(int(timeout_sec * 1000) if os.name == 'nt' else timeout_sec), host]
    try:
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        return await process.wait()
    except: return None

async def sendmess(bot, CHAT_ID, message, delay_error):
    """Наполеглива відправка повідомлень з обробкою помилок мережі"""
    while True:
        try:
            await bot.send_message(chat_id=CHAT_ID, text=message)
            return
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except (NetworkError, TimedOut):
            logging.warning(f"Мережа недоступна. Повтор через {delay_error} сек...")
            await asyncio.sleep(delay_error)
        except Exception as e:
            logging.error(f"Помилка відправки: {e}")
            break

async def pinger_worker(ip, building, delay):
    while True:
        response = await ping(ip)
        current_st = "up" if response == 0 else "down"
        if current_st != ip_states[ip]:
            if current_st == "down":
                buildings_status[building]["down"] += 1
            else:
                buildings_status[building]["down"] -= 1
            ip_states[ip] = current_st
        await asyncio.sleep(delay)

async def info_message(threshold):
    time = datetime.now().strftime('%H:%M:%S')
    message = f"📊 **МОНІТОР СВІТЛА**\nОновлено о: `{time}`\n"
    message += "—" * 15 + "\n"
    
    # Сортуємо будинки: 
    sorted_buildings = sorted(
        buildings_status.items(),
        key=lambda item: (item[1]["down"] / item[1]["total"] < threshold, item[0])
    )
    
    for building, status in sorted_buildings:
        available = status["total"] - status["down"]
        fail_ratio = status["down"] / status["total"]
        perc = (available / status["total"]) * 100
        
        icon = "💡" if fail_ratio < threshold else "⚠️"
        status_text = "зі світлом" if fail_ratio < threshold else "без світла"
        
        message += f"{icon} **{building}**: {status_text}\n"
        message += f"└ Доступність: {perc:.1f}% ({available} з {status['total']})\n\n"
    
    return message

async def central_monitor(bot, CHAT_ID, threshold, delay, delay_error):
    await asyncio.sleep(60)
    
    for building, status in buildings_status.items():
        status["alert_sent"] = (status["down"] / status["total"] >= threshold)
    
    # Створення закріпленого повідомлення з очікуванням мережі
    main_msg = None
    while main_msg is None:
        try:
            report_text = await info_message(threshold)
            main_msg = await bot.send_message(chat_id=CHAT_ID, text=report_text, parse_mode="Markdown")
            await bot.pin_chat_message(chat_id=CHAT_ID, message_id=main_msg.message_id)
        except (NetworkError, TimedOut):
            logging.warning(f"Немає інету для закріпу. Чекаю {delay_error} сек...")
            await asyncio.sleep(delay_error)

    while True:
        await asyncio.sleep(delay)
        time_now = datetime.now().strftime('%H:%M:%S')
        
        for building, status in buildings_status.items():
            fail_ratio = status["down"] / status["total"]
            available = status["total"] - status["down"]
            perc = (available / status["total"]) * 100

            if fail_ratio >= threshold and not status["alert_sent"]:
                status["alert_sent"] = True
                await sendmess(bot, CHAT_ID, f"⚠️ Світло зникло: {building}\n🔴 Доступність: {perc:.1f}% ({available} з {status['total']})\n🕑 {time_now}", delay_error)
            
            elif fail_ratio < threshold and status["alert_sent"]:
                status["alert_sent"] = False
                await sendmess(bot, CHAT_ID, f"💡 Світло з'явилося: {building}\n🟢 Доступність: {perc:.1f}% ({available} з {status['total']})\n🕑 {time_now}", delay_error)
        
        # Оновлення закріпленого повідомлення
        try:
            new_report = await info_message(threshold)
            await bot.edit_message_text(chat_id=CHAT_ID, message_id=main_msg.message_id, text=new_report, parse_mode="Markdown")
        except (NetworkError, TimedOut):
            pass # Просто чекаємо наступного циклу
        except telegram.error.BadRequest as e:
            if "Message is not modified" not in str(e):
                logging.error(f"Помилка оновлення: {e}")

async def main():
    config = configparser.RawConfigParser()
    config.read("config.ini")
    
    delay = int(config["Settings"]["DELAY"])
    delay_error = int(config["Settings"]["DELAY_ERROR"])
    threshold = float(config["Settings"].get("POWER_FAILURE_THRESHOLD", 0.5))
    
    ip_list = read_ip_file()
    if not ip_list: return

    bot = telegram.Bot(config["General"]["TGTOKEN"])
    CHAT_ID = config["General"]["CHAT_ID"]

    tasks = [asyncio.create_task(pinger_worker(i[0], i[1], delay)) for i in ip_list]
    tasks.append(asyncio.create_task(central_monitor(bot, CHAT_ID, threshold, delay, delay_error)))
    
    print(f"Моніторинг запущено! (затримка: {delay} сек, при помилці: {delay_error} сек)")
    await sendmess(bot, CHAT_ID, "🚀 Моніторинг світла запущено!", delay_error)
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())