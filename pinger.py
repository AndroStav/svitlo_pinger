import asyncio, os, telegram, csv, configparser, logging, sys, json
from datetime import datetime
from telegram.error import NetworkError, TimedOut, RetryAfter

# Налаштування логування
logging.basicConfig(level=logging.INFO, filename="pinger.log", filemode="w", format="%(asctime)s %(levelname)s [%(funcName)s]: %(message)s")

STATUS_FILE = "status.json"
buildings_status = {}
ip_states = {}

def save_status():
    # Зберігає стан (наявність світла) та час останньої зміни у файл
    data = {
        b: {
            "last_change": status["last_change"], 
            "alert_sent": status["alert_sent"]
        } for b, status in buildings_status.items()
    }
    with open(STATUS_FILE, "w") as f:
        json.dump(data, f)

def load_status():
    # Завантажує час останньої зміни з файлу
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def read_ip_file():
    ip_list = []
    saved_status = load_status()
    current_now = datetime.now().isoformat()
    
    try:
        with open("ip.csv", "r", encoding="utf-8") as ip_csv:
            reader = csv.reader(ip_csv)
            for row in reader:
                if len(row) == 2:
                    ip, building = row[0], row[1]
                    ip_list.append([ip, building])
                    if building not in buildings_status:
                        b_saved = saved_status.get(building)
                        
                        # Читаємо новий формат (словник) або старий (рядок)
                        if isinstance(b_saved, dict):
                            last_change = b_saved.get("last_change", current_now)
                            alert_sent = b_saved.get("alert_sent", False)
                        elif isinstance(b_saved, str):
                            last_change = b_saved
                            alert_sent = False
                        else:
                            last_change = current_now
                            alert_sent = False
                            
                        buildings_status[building] = {
                            "total": 0, 
                            "down": 0, 
                            "alert_sent": alert_sent, # Відновлюємо стан з файлу
                            "last_change": last_change
                        }
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
    # Наполеглива відправка повідомлень з обробкою помилок мережі
    while True:
        try:
            await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="Markdown")
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

def pluralize(n, forms):
    # Підбирає правильну форму слова залежно від числа n
    n = abs(n) % 100
    n1 = n % 10
    if 10 < n < 20: return forms[2]
    if n1 > 1 and n1 < 5: return forms[1]
    if n1 == 1: return forms[0]
    return forms[2]

def get_duration_str(last_change_iso):
    # Рахує різницю між 'зараз' та вказаним часом і повертає гарний текст
    time_now = datetime.now()
    last_change = datetime.fromisoformat(last_change_iso)
    diff = time_now - last_change
    
    days = diff.days
    hours, remainder = divmod(diff.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    
    d_text = pluralize(days, ["день", "дні", "днів"])
    h_text = pluralize(hours, ["годину", "години", "годин"])
    m_text = pluralize(minutes, ["хвилину", "хвилини", "хвилин"])
    
    if days > 0:
        final_text = f"{days} {d_text} {hours} {h_text} {minutes} {m_text}"
    elif hours > 0:
        final_text = f"{hours} {h_text} {minutes} {m_text}"
    else:
        final_text = f"{minutes} {m_text}"
    
    return final_text

async def info_message(threshold):
    time_now = datetime.now().strftime('%H:%M:%S')
    message = f"📊 **МОНІТОР СВІТЛА**\nОновлено о: `{time_now}`\n"
    message += "—" * 15 + "\n"
    
    # Сортування
    def sorting_key(item):
        status = item[1]
        is_up = (status["down"] / status["total"]) < threshold
        ts = datetime.fromisoformat(status["last_change"]).timestamp()
        
        # Використовуємо -ts, щоб найбільше число (зараз) стало найменшим і пішло вгору
        return (1 if is_up else 0, -ts)
    
    sorted_buildings = sorted(buildings_status.items(), key=sorting_key)
    
    for building, status in sorted_buildings:
        available = status["total"] - status["down"]
        fail_ratio = status["down"] / status["total"]
        perc = (available / status["total"]) * 100
        
        # Використовуємо нашу функцію
        duration_str = get_duration_str(status["last_change"])
        
        if fail_ratio >= threshold:
            icon, status_text = "⚠️", "без світла"
            time_label = "Немає вже"
        else:
            icon, status_text = "💡", "зі світлом"
            time_label = "Вже є"
        
        message += f"{icon} **{building}**: {status_text}\n"
        message += f"├ {time_label}: `{duration_str}`\n"
        message += f"└ Доступність: {perc:.1f}% ({available} з {status['total']})\n\n"
    
    return message

async def central_monitor(bot, CHAT_ID, threshold, delay, delay_error):
    await asyncio.sleep(60)
    
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
        time_now_str = datetime.now().strftime('%H:%M:%S')
        current_iso = datetime.now().isoformat()
        
        changes_made = False
        for building, status in buildings_status.items():
            fail_ratio = status["down"] / status["total"]

            # Світло ЗНИКЛО
            if fail_ratio >= threshold and not status["alert_sent"]:
                # Рахуємо, скільки часу БУЛО світло
                duration = get_duration_str(status["last_change"])
                
                status["alert_sent"] = True
                status["last_change"] = current_iso
                changes_made = True
                
                msg = (f"⚠️ **Світло зникло**: {building}\n"
                       f"⏳ Було зі світлом: `{duration}`\n"
                       f"🕑 {time_now_str}")
                await sendmess(bot, CHAT_ID, msg, delay_error)
            
            # Світло З'ЯВИЛОСЯ
            elif fail_ratio < threshold and status["alert_sent"]:
                # Рахуємо, скільки часу НЕ БУЛО світла
                duration = get_duration_str(status["last_change"])
                
                status["alert_sent"] = False
                status["last_change"] = current_iso
                changes_made = True
                
                msg = (f"💡 **Світло з'явилося**: {building}\n"
                       f"⏳ Було без світла: `{duration}`\n"
                       f"🕑 {time_now_str}\n")
                await sendmess(bot, CHAT_ID, msg, delay_error)
        
        if changes_made:
            save_status() # Зберігаємо у файл JSON

        # Оновлення закріпленого звіту
        try:
            new_report = await info_message(threshold)
            await bot.edit_message_text(chat_id=CHAT_ID, message_id=main_msg.message_id, text=new_report, parse_mode="Markdown")
        except Exception:
            pass

async def main():
    config = configparser.RawConfigParser()
    config.read("config.ini")
    
    delay = int(config["Settings"]["DELAY"])
    delay_error = int(config["Settings"]["DELAY_ERROR"])
    threshold = float(config["Settings"].get("POWER_FAILURE_THRESHOLD", 0.5))
    
    ip_list = read_ip_file()
    if not ip_list: return
    save_status()

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