import re
import time
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)




class EmailExtractor:
    def __init__(self, anticaptcha_key: str = "", headless: bool = True,
                 delay: float = 2.0):
        self.anticaptcha_key = anticaptcha_key
        self.headless = headless
        self.delay = delay

    def _extract_from_text(self, text: str) -> List[str]:
        """Regex-поиск email в тексте."""
        found = EMAIL_RE.findall(text)
        # Убираем служебные адреса YouTube
        filtered = [
            e for e in found
            if not any(d in e for d in [
                "youtube.com", "google.com", "youtu.be",
                "example.com", "sentry.io"
            ])
        ]
        return list(set(filtered))

    def _try_click_email_button(self, page) -> Optional[str]:
        """
        Поиск email

        """
        try:
            # Поиск кнопки
            selectors = [
                "button:has-text('View email address')",
                "button:has-text('Email')",
                "#email-reveal-button",
                "[aria-label*='email']",
            ]
            btn = None
            for sel in selectors:
                btn = page.query_selector(sel)
                if btn:
                    break

            if not btn:
                return None

            btn.click()
            time.sleep(1.5)

            # Проверяем — появилась ли капча?
            captcha_frame = page.query_selector("iframe[src*='recaptcha']")
            if captcha_frame:
                logger.info("    [CAPTCHA] Обнаружена reCAPTCHA — запускаем решатель...")

                # Получаем sitekey из атрибутов
                sitekey = page.evaluate(
                    "document.querySelector('[data-sitekey]')?.getAttribute('data-sitekey') || ''"
                )
                token = solve_recaptcha_stub(
                    self.anticaptcha_key, sitekey, page.url
                )
                if token:
                    # Вставляем токен и сабмитим
                    page.evaluate(
                        f"document.getElementById('g-recaptcha-response').value = '{token}'"
                    )
                    submit_btn = page.query_selector("button[type='submit'], #submit")
                    if submit_btn:
                        submit_btn.click()
                        time.sleep(2)
                else:
                    logger.warning("    [CAPTCHA] Токен не получен, пропускаем.")
                    return None

            # Ищем email в появившемся контенте / диалоге
            page.wait_for_timeout(1500)
            dialog_text = ""
            dialog = page.query_selector("[role='dialog'], .ytd-about-channel-renderer")
            if dialog:
                dialog_text = dialog.inner_text()
            else:
                dialog_text = page.inner_text("body")

            found = self._extract_from_text(dialog_text)
            return found[0] if found else None

        except Exception as e:
            logger.debug(f"    Ошибка при клике на кнопку: {e}")
            return None

    def extract_email(self, channel_url: str) -> Optional[str]:
        """
        Поиск email по странице канала

        """
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        about_url = channel_url.rstrip("/") + "/about"

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=self.headless,
                    args=[
                        "--no-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                    ],
                )
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 800},
                    locale="en-US",
                )

                # Маскировка под человека
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    window.chrome = { runtime: {} };
                """)

                page = context.new_page()

                try:
                    page.goto(about_url, timeout=20000, wait_until="domcontentloaded")
                    time.sleep(self.delay)
                except PWTimeout:
                    logger.warning(f"    Таймаут загрузки: {about_url}")
                    browser.close()
                    return None

                # Сначала ищем email в тексте описания без кликов
                body_text = page.inner_text("body")
                emails_in_text = self._extract_from_text(body_text)
                if emails_in_text:
                    logger.info(f"    Email найден в тексте: {emails_in_text[0]}")
                    browser.close()
                    return emails_in_text[0]

                # Поиск кнопки view all emails
                email_via_btn = self._try_click_email_button(page)
                browser.close()

                if email_via_btn:
                    logger.info(f"    Email через кнопку: {email_via_btn}")
                return email_via_btn

        except Exception as e:
            logger.error(f"    Критическая ошибка при парсинге {about_url}: {e}")
            return None

    def extract_emails_batch(self, channels: List[Dict]) -> List[Dict]:
        """Обойти список каналов и добавить email."""
        total = len(channels)
        for i, ch in enumerate(channels, 1):
            logger.info(
                f"[EMAIL] [{i}/{total}] {ch['title']} — {ch['url']}"
            )
            email = self.extract_email(ch["url"])
            ch["email"] = email or ""
            time.sleep(self.delay)
        return channels
