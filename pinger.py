import asyncio, os, subprocess, telegram, csv, configparser, logging
from datetime import datetime
import sys

# Налаштування логування залишається без змін
logging.basicConfig(level=logging.INFO, filename="pinger.log", filemode="w", format="%(asctime)s %(levelname)s [%(funcName)s]: %(message)s")

# Глобальний словник для відстеження стану будинків
buildings_status = {}
# Спільний стан для всіх IP
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
                    
                    # Ініціалізуємо статистику будинку
                    if building not in buildings_status:
                        buildings_status[building] = {"total": 0, "down": 0, "alert_sent": False}
                    buildings_status[building]["total"] += 1
                    ip_states[ip] = "up"
        logging.info("IP-адреси та дані будинків завантажені")
        return ip_list
    except Exception as e:
        logging.error(f"Помилка читання CSV: {e}")
        return None

async def ping(host):
    timeout_sec = 1
    # Визначаємо прапорці залежно від ОС
    if os.name == 'nt':
        command = ['ping', '-n', '1', '-w', str(int(timeout_sec * 1000)), host]
    else:
        command = ['ping', '-c', '1', '-W', str(timeout_sec), host]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        return await process.wait()
    except Exception as e:
        logging.error(f"Помилка виконання ping для {host}: {e}")
        return None

async def sendmess(bot, CHAT_ID, message):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message)
        logging.info(f"Відправлено сповіщення: {message}")
    except Exception as e:
        logging.error(f"Помилка ТГ: {e}")

async def pinger(ip, building, bot, CHAT_ID, threshold):
    while True:
        response = await ping(ip)
        current_st = "up" if response == 0 else "down"
        time = datetime.now().strftime('%H:%M:%S')

        # Якщо стан змінився
        if current_st != ip_states[ip]:
            old_st = ip_states[ip]
            ip_states[ip] = current_st
            
            # Оновлюємо лічильник будинку
            if current_st == "down":
                buildings_status[building]["down"] += 1
            else:
                buildings_status[building]["down"] -= 1

            # Перевірка на "зникнення світла"
            down_count = buildings_status[building]["down"]
            total_count = buildings_status[building]["total"]
            fail_ratio = down_count / total_count

            if fail_ratio >= threshold and not buildings_status[building]["alert_sent"]:
                await sendmess(bot, CHAT_ID, f"⚠️ Зникло світло: {building}\n🔴 Впало {down_count} з {total_count} пристроїв.\n🕑 {time}")
                buildings_status[building]["alert_sent"] = True
            
            elif fail_ratio < threshold and buildings_status[building]["alert_sent"]:
                await sendmess(bot, CHAT_ID, f"💡 Світло з'явилося: {building}\n✅ Доступно {total_count - down_count} з {total_count} пристроїв.\n🕑 {time}")
                buildings_status[building]["alert_sent"] = False

        await asyncio.sleep(DELAY)

async def main():
    config = configparser.ConfigParser()
    config.read("config.ini")
    
    global DELAY
    DELAY = int(config["Settings"]["DELAY"])
    threshold = float(config["Settings"].get("POWER_FAILURE_THRESHOLD", 0.5))
    
    ip_list = read_ip_file()
    if not ip_list: return

    bot = telegram.Bot(config["General"]["TGTOKEN"])
    CHAT_ID = config["General"]["CHAT_ID"]

    tasks = [asyncio.create_task(pinger(i[0], i[1], bot, CHAT_ID, threshold)) for i in ip_list]
    print("Моніторинг світла запущено!")
    await sendmess(bot, CHAT_ID, "Моніторинг світла запущено!")
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())