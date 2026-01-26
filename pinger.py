import asyncio, os, telegram, csv, configparser, logging, sys
from datetime import datetime

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

async def sendmess(bot, CHAT_ID, message):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message)
    except Exception as e:
        logging.error(f"TG Error: {e}")

# Використовуємо delay для пауз між пінгами
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

# Використовуємо delay для частоти перевірки стану будинків
async def central_monitor(bot, CHAT_ID, threshold, delay):
    # Даємо час на перший скан (3 цикли затримки, щоб дані були точними)
    await asyncio.sleep(delay * 3)
    # Стартове повідомлення зі статусами
    start_message = "Статус світла на старті:"
    
    for building, status in buildings_status.items():
        fail_ratio = status["down"] / status["total"]
        if fail_ratio >= threshold:
                status["alert_sent"] = True
                start_message += f"\n⚠️ {building} без світла\n        (доступно {status['total'] - status['down']} з {status['total']})"
            
        elif fail_ratio < threshold:
            status["alert_sent"] = False
            start_message += f"\n💡 {building} зі світлом\n        (доступно {status['total'] - status['down']} з {status['total']})"
    
    await sendmess(bot, CHAT_ID, start_message)
    
    while True:
        time = datetime.now().strftime('%H:%M:%S')
        for building, status in buildings_status.items():
            fail_ratio = status["down"] / status["total"]

            if fail_ratio >= threshold and not status["alert_sent"]:
                status["alert_sent"] = True
                await sendmess(bot, CHAT_ID, f"⚠️ Зникло світло: {building}\n🔴 Доступно {status['total'] - status['down']} з {status['total']} пристроїв.\n🕑 {time}")
            
            elif fail_ratio < threshold and status["alert_sent"]:
                status["alert_sent"] = False
                await sendmess(bot, CHAT_ID, f"💡 Світло з'явилося: {building}\n✅ Доступно {status['total'] - status['down']} з {status['total']} пристроїв.\n🕑 {time}")
        
        await asyncio.sleep(delay)

async def main():
    config = configparser.RawConfigParser() # Використовуємо для читання налаштувань
    config.read("config.ini")
    
    # Зчитуємо DELAY з файлу налаштувань
    delay = int(config["Settings"]["DELAY"]) 
    threshold = float(config["Settings"].get("POWER_FAILURE_THRESHOLD", 0.5))
    
    ip_list = read_ip_file()
    if not ip_list: return

    bot = telegram.Bot(config["General"]["TGTOKEN"])
    CHAT_ID = config["General"]["CHAT_ID"]

    tasks = [asyncio.create_task(pinger_worker(i[0], i[1], delay)) for i in ip_list]
    # Передаємо той самий delay в монітор
    tasks.append(asyncio.create_task(central_monitor(bot, CHAT_ID, threshold, delay)))
    
    print(f"Моніторинг запущено (затримка: {delay} сек)!")
    await sendmess(bot, CHAT_ID, "🚀 Моніторинг світла запущено!")
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())