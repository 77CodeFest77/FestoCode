                async with session.get(url) as resp:
                    text = await resp.text()
                    # Простой парсинг строк вида IP:PORT
                    lines = text.strip().splitlines()                    for line in lines:
                        line = line.strip()
                        if ":" in line:
                            ip_port = line.split(":")
                            if len(ip_port) == 2:
                                ip, port = ip_port
                                all_proxies.add((ip, int(port)))
            except Exception:
                continue
    return list(all_proxies)

async def check_proxy_speed(proxy_ip, proxy_port):
    start_time = time.time()
    try:
        connector = aiohttp.TCPConnector(limit=1)
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            proxy_url = f"http://{proxy_ip}:{proxy_port}"
            async with session.get('http://httpbin.org/ip', proxy=proxy_url) as resp:
                if resp.status == 200:
                    end_time = time.time()
                    speed = round(end_time - start_time, 2)
                    return True, speed
    except Exception:
        pass
    return False, 0

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 Привет! Я бот, который находит и проверяет прокси.\n\n"
        "MTProto позволяет обходить блокировки и использовать Telegram быстро и безопасно.\n\n"
        "Выбери действие:"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu_keyboard())

@dp.callback_query(lambda c: c.data == "find_proxy")
async def process_find_proxy(callback_query: types.CallbackQuery):
    await callback_query.message.answer("🔍 Поиск и проверка прокси...")
    
    proxies = await fetch_proxies_from_sources()
    working_proxies = []

    for ip, port in proxies[:10]:  # Проверим первые 10
        is_working, speed = await check_proxy_speed(ip, port)
        if is_working:
            working_proxies.append({"ip": ip, "port": port, "speed": speed})

    if working_proxies:
        best_proxy = min(working_proxies, key=lambda x: x["speed"])        response = (
            f"✅ Найден рабочий прокси:\n"
            f"🌐 IP: {best_proxy['ip']}\n"
            f"🔌 Порт: {best_proxy['port']}\n"
            f"⚡ Скорость: {best_proxy['speed']} сек\n\n"
            f"ℹ️ Этот прокси SOCKS5. Для использования в Telegram, установите его вручную или используйте с VPN-приложением."
        )
        await callback_query.message.answer(response)
    else:
        await callback_query.message.answer("❌ Не удалось найти рабочие прокси.")

    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "about")
async def process_about(callback_query: types.CallbackQuery):
    about_text = (
        "ℹ️ **О боте:**\n\n"
        "Этот бот предоставляет SOCKS5-прокси, которые помогают обходить "
        "блокировки и использовать Telegram без ограничений.\n\n"
        "MTProto — это протокол, разработанный Telegram, который защищает "
        "трафик и позволяет подключаться даже при жёсткой цензуре."
    )
    await callback_query.message.answer(about_text, parse_mode="Markdown")
    await callback_query.answer()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
