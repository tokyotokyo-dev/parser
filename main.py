import json
import logging
import os
import sys
import time
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Настройка логирования
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("parser.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

BANNER = r"""
   ______ ____   __  __ __  __ ____ 
  /_  __// __ \ / / / // / / // __ \
   / /  / / / // //_/ / /_/ // / / /
  / /  / /_/ // , <  \__, // /_/ / 
 /_/   \____//_/|_| /____/ \____/  
"""


def load_config(path: str = "settings.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_games(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def load_checkpoint(path: str = "checkpoint.json") -> list:
    """Загрузить ранее собранные ID (если парсер прерывался)."""
    full_path = os.path.join(BASE_DIR, path)
    if os.path.exists(full_path):
        with open(full_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_checkpoint(data, path: str = "checkpoint.json"):
    import datetime

    def _default(obj):
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")

    full_path = os.path.join(BASE_DIR, path)
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(data, f, default=_default, ensure_ascii=False)


def load_processed_ids() -> set:
    """Загрузка ID каналов из прошлого"""
    full_path = os.path.join(BASE_DIR, "processed_ids.json")
    if os.path.exists(full_path):
        with open(full_path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_processed_ids(ids: set):
    """Сохранение ID обработанных каналов."""
    full_path = os.path.join(BASE_DIR, "processed_ids.json")
    existing = load_processed_ids()
    merged = existing | ids
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(list(merged), f, ensure_ascii=False)


def main():
    print(BANNER)

    # ------------------------------------------------------------------
    # Загрузка конфига
    # ------------------------------------------------------------------
    try:
        cfg = load_config("settings.json")
    except FileNotFoundError:
        logger.error("Файл settings.json не найден рядом с main.py!")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Импорт зависимостей
    # ------------------------------------------------------------------
    try:
        from googleapiclient.discovery import build
    except ImportError:
        logger.error("Не установлен google-api-python-client: pip install google-api-python-client")
        sys.exit(1)

    from modules.channel_collector import ChannelCollector
    from modules.channel_filter import ChannelFilter
    from modules.email_extractor import EmailExtractor
    from modules.excel_exporter import export_xlsx

    
    api_keys = cfg.get("youtube_api_keys", [cfg.get("youtube_api_key")])
    api_keys = [k for k in api_keys if k and k != "YOUR_YOUTUBE_API_KEY_HERE"]
    if not api_keys:
        logger.error("Укажите youtube_api_keys в settings.json!")
        sys.exit(1)

    youtube = build("youtube", "v3", developerKey=api_keys[0])

    
    games = load_games(cfg["games_file"])
    logger.info(f"Загружено {len(games)} игр из {cfg['games_file']}")

    checkpoint_ids = load_checkpoint("checkpoint_ids.json")

    if checkpoint_ids:
        logger.info(f"[CHECKPOINT] Найдено {len(checkpoint_ids)} сохранённых ID, пропускаем сбор.")
        all_channel_ids = checkpoint_ids
    else:
        collector = ChannelCollector(
            api_keys=api_keys,
            max_results_per_query=cfg.get("max_results_per_query", 50),
            regions=cfg.get("regions_to_search", ["US"]),
            delay=cfg.get("request_delay_seconds", 1.5),
        )
        logger.info("=" * 60)
        logger.info("ЭТАП 1: Сбор каналов через YouTube Search API")
        logger.info("=" * 60)
        all_channel_ids = collector.collect_all(
            games,
            checkpoint_path=os.path.join(BASE_DIR, "checkpoint_ids.json")
        )
        save_checkpoint(all_channel_ids, "checkpoint_ids.json")
        logger.info(f"Собрано уникальных ID: {len(all_channel_ids)}")

    # Очистка обработанных каналов
    processed_ids = load_processed_ids()
    all_channel_ids = [cid for cid in all_channel_ids if cid not in processed_ids]
    logger.info(f"После исключения обработанных: {len(all_channel_ids)} новых каналов")

    
    checkpoint_filtered = load_checkpoint("checkpoint_filtered.json")

    if checkpoint_filtered:
        logger.info(f"[CHECKPOINT] Найдено {len(checkpoint_filtered)} отфильтрованных каналов.")
        filtered_channels = checkpoint_filtered
    else:
        logger.info("=" * 60)
        logger.info("ЭТАП 2: Фильтрация по гео и статистике")
        logger.info("=" * 60)
        channel_filter = ChannelFilter(
            youtube_client=youtube,
            whitelist_countries=cfg.get("whitelist_countries", []),
            blacklist_countries=cfg.get("blacklist_countries", []),
            min_subscribers=cfg.get("min_subscribers", 1000),
            min_videos=cfg.get("min_videos", 5),
            max_days_since_upload=cfg.get("max_days_since_upload", 90),
            delay=cfg.get("request_delay_seconds", 1.5),
        )
        filtered_channels = channel_filter.filter(all_channel_ids)
        save_checkpoint(filtered_channels, "checkpoint_filtered.json")

    
    logger.info("=" * 60)
    logger.info("ЭТАП 3: Парсинг email со страниц /about")
    logger.info("=" * 60)

    extractor = EmailExtractor(
        anticaptcha_key=cfg.get("anticaptcha_api_key", ""),
        headless=cfg.get("headless_browser", True),
        delay=cfg.get("request_delay_seconds", 2.0),
    )
    channels_with_email = extractor.extract_emails_batch(filtered_channels)

    
    logger.info("=" * 60)
    logger.info("ЭТАП 5: Сохранение результатов")
    logger.info("=" * 60)

    output_file = cfg.get("output_file", "results.xlsx")
    export_xlsx(channels_with_email, output_file)

    # Сохранение обработанных id
    all_processed = set(ch["channel_id"] for ch in filtered_channels)
    save_processed_ids(all_processed)
    logger.info(f"[PROCESSED] Сохранено {len(all_processed)} ID в processed_ids.json")

    # Очистка чекпоинтов после завершения
    for cp in ("checkpoint_ids.json", "checkpoint_filtered.json"):
        full_cp = os.path.join(BASE_DIR, cp)
        if os.path.exists(full_cp):
            os.remove(full_cp)

    # ------------------------------------------------------------------
    # Итог
    # ------------------------------------------------------------------
    total = len(channels_with_email)
    with_email = sum(1 for ch in channels_with_email if ch.get("email"))
    logger.info("=" * 60)
    logger.info(f"Готово! Всего каналов: {total} | С email: {with_email}")
    logger.info(f"Результаты: {output_file}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
