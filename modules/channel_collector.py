import json
import time
import logging
from typing import List

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


class ChannelCollector:
    def __init__(self, api_keys: list, max_results_per_query: int = 50,
                 regions: List[str] = None, delay: float = 1.0):
        self.api_keys = api_keys
        self.key_index = 0
        self.max_results = min(max_results_per_query, 50)
        self.regions = regions or ["US"]
        self.delay = delay
        self.youtube = build("youtube", "v3", developerKey=self.api_keys[0])

    def _switch_key(self) -> bool:
        #Переключение на следующий ключ
        self.key_index += 1
        if self.key_index >= len(self.api_keys):
            logger.error("[API] Все ключи исчерпали квоту!")
            return False
        new_key = self.api_keys[self.key_index]
        self.youtube = build("youtube", "v3", developerKey=new_key)
        logger.info(f"[API] Переключились на ключ #{self.key_index + 1}")
        return True

    def search_channels_for_game(self, game: str) -> List[str]:
        #Поиск канала по определенным словам
        channel_ids = set()
        queries = [
            f"{game} highlights",
            f"{game} gameplay",
            f"{game} update",
        ]

        for query in queries:
            for region in self.regions:
                ids = self._search_page(query, region)
                channel_ids.update(ids)
                time.sleep(self.delay)

        return list(channel_ids)

    def _search_page(self, query: str, region: str) -> List[str]:
        #Запрос к search list
        channel_ids = []
        next_page_token = None
        collected = 0

        while collected < self.max_results:
            try:
                request = self.youtube.search().list(
                    part="snippet",
                    q=query,
                    type="channel",
                    regionCode=region,
                    maxResults=min(50, self.max_results - collected),
                    pageToken=next_page_token,
                    relevanceLanguage="en",
                )
                response = request.execute()

                for item in response.get("items", []):
                    cid = item["snippet"].get("channelId") or item["id"].get("channelId")
                    if cid:
                        channel_ids.append(cid)
                        collected += 1

                next_page_token = response.get("nextPageToken")
                if not next_page_token or collected >= self.max_results:
                    break

                time.sleep(self.delay)

            except HttpError as e:
                if "quotaExceeded" in str(e):
                    logger.warning(f"[API] Квота исчерпана на ключе #{self.key_index + 1}")
                    if not self._switch_key():
                        return channel_ids
                    continue
                else:
                    logger.error(f"API ошибка при поиске '{query}' [{region}]: {e}")
                    break

        logger.info(f"  Запрос '{query}' [{region}] → {len(channel_ids)} каналов")
        return channel_ids

    def collect_all(self, games: List[str], checkpoint_path: str = None) -> List[str]:
        #Обход игр и скип id каналов
        all_ids = set()

        for i, game in enumerate(games, 1):
            logger.info(f"[{i}/{len(games)}] Ищем каналы для игры: {game}")
            try:
                ids = self.search_channels_for_game(game)
            except KeyboardInterrupt:
                logger.info("Остановлено пользователем, сохраняем прогресс...")
                break

            before = len(all_ids)
            all_ids.update(ids)
            logger.info(f"  Добавлено новых: {len(all_ids) - before} | Всего: {len(all_ids)}")

            if checkpoint_path:
                with open(checkpoint_path, "w", encoding="utf-8") as f:
                    json.dump(list(all_ids), f)

        return list(all_ids)
