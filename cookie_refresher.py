"""
Автоматическое обновление YouTube cookies через Selenium.

Логинится в YouTube резервным аккаунтом (email/пароль из переменных
окружения) и сохраняет cookies.txt в формате Netscape, который понимает
yt-dlp.

ВАЖНО:
- На резервном аккаунте должна быть ОТКЛЮЧЕНА двухфакторная аутентификация,
  иначе автологин не пройдёт дальше запроса кода.
- Google может показать капчу при входе с нового/облачного IP — тогда
  функция вернёт False, и нужно будет один раз войти вручную с этого же
  IP (например, через VNC/remote debugging), чтобы "приучить" аккаунт.
- Требует установленный Chromium в системе (см. Dockerfile).

Переменные окружения:
    YT_EMAIL    — email резервного аккаунта
    YT_PASSWORD — пароль резервного аккаунта
    YT_COOKIES_FILE — путь для сохранения (по умолчанию cookies.txt)
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

COOKIES_FILE = os.environ.get("YT_COOKIES_FILE", "cookies.txt")
YT_EMAIL = os.environ.get("YT_EMAIL")
YT_PASSWORD = os.environ.get("YT_PASSWORD")


def _cookies_to_netscape(cookies: list, filepath: str) -> None:
    """Конвертирует cookies из формата Selenium в формат Netscape для yt-dlp."""
    lines = ["# Netscape HTTP Cookie File", ""]
    for c in cookies:
        domain = c.get("domain", "")
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = str(int(c.get("expiry", time.time() + 30 * 86400)))
        name = c.get("name", "")
        value = c.get("value", "")
        lines.append(
            "\t".join([domain, include_subdomains, path, secure, expiry, name, value])
        )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def refresh_cookies() -> bool:
    """
    Логинится в YouTube и сохраняет свежие cookies.
    Возвращает True при успехе, False при неудаче (например, капча или 2FA).
    """
    if not YT_EMAIL or not YT_PASSWORD:
        logger.error("YT_EMAIL / YT_PASSWORD не заданы — обновление cookies невозможно")
        return False

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,800")
    # user-agent обычного браузера, чтобы меньше палиться как автоматизация
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 20)

    try:
        driver.get("https://accounts.google.com/ServiceLogin?service=youtube")

        # Шаг 1: email
        email_input = wait.until(EC.presence_of_element_located((By.ID, "identifierId")))
        email_input.send_keys(YT_EMAIL)
        email_input.send_keys(Keys.RETURN)

        # Шаг 2: пароль
        password_input = wait.until(
            EC.presence_of_element_located((By.NAME, "Passwd"))
        )
        time.sleep(1)  # даём полю анимацию отрисоваться
        password_input.send_keys(YT_PASSWORD)
        password_input.send_keys(Keys.RETURN)

        # Ждём загрузки YouTube после логина — признак успешного входа
        try:
            wait.until(EC.url_contains("myaccount.google.com"))
        except TimeoutException:
            pass  # иногда редиректит сразу, не всегда попадает на myaccount

        driver.get("https://www.youtube.com")
        time.sleep(3)

        # Проверяем, что реально залогинены (есть аватарка аккаунта)
        try:
            wait.until(EC.presence_of_element_located((By.ID, "avatar-btn")))
        except TimeoutException:
            logger.error(
                "Не удалось подтвердить успешный вход — возможно, Google запросил "
                "код 2FA или показал капчу. Автообновление cookies не удалось."
            )
            return False

        cookies = driver.get_cookies()
        _cookies_to_netscape(cookies, COOKIES_FILE)
        logger.info(f"Cookies успешно обновлены и сохранены в {COOKIES_FILE}")
        return True

    except Exception as e:
        logger.exception(f"Ошибка при обновлении cookies: {e}")
        return False

    finally:
        driver.quit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success = refresh_cookies()
    print("Успех" if success else "Неудача — смотри логи выше")
