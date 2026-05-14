import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

CHUNK_SIZE = 50  # API channels.list принимает до 50 ID за раз!!!


class ChannelFilter:
    def __init__(self, youtube_client,
                 whitelist_countries: List[str],
                 blacklist_countries: List[str],
                 min_subscribers: int,
                 min_videos: int,
                 max_days_since_upload: int,
                 delay: float = 1.0):
        self.yt = youtube_client
        self.whitelist = [c.upper() for c in whitelist_countries]
        self.blacklist = [c.upper() for c in blacklist_countries]
        self.min_subs = min_subscribers
        self.min_videos = min_videos
        self.max_days = max_days_since_upload
        self.delay = delay

    def _chunks(self, lst: List, n: int):
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    def _get_channel_batch(self, ids: List[str]) -> List[Dict]:
        #Запрос метаданных для каналов
        try:
            resp = self.yt.channels().list(
                part="snippet,statistics,contentDetails,brandingSettings",
                id=",".join(ids),
                maxResults=50,
            ).execute()
            return resp.get("items", [])
        except HttpError as e:
            logger.error(f"API ошибка channels.list: {e}")
            return []

    def _get_last_upload_date(self, uploads_playlist_id: str) -> Optional[datetime]:
        #Получение даты последнего видео
        if not uploads_playlist_id:
            return None
        try:
            resp = self.yt.playlistItems().list(
                part="snippet",
                playlistId=uploads_playlist_id,
                maxResults=1,
            ).execute()
            items = resp.get("items", [])
            if items:
                pub = items[0]["snippet"].get("publishedAt", "")
                if pub:
                    return datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except HttpError:
            pass
        return None

    def _passes_filter(self, item: Dict) -> Optional[Dict]:
        #Проверка канала, возрврат с email/none

        channel_id = item["id"]
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})

        title = snippet.get("title", "")
        country = snippet.get("country", "").upper()
        description = snippet.get("description", "")

        # Фильтр по ГЕО
        if self.blacklist and country in self.blacklist:
            logger.debug(f"  SKIP (blacklist {country}): {title}")
            return None

        if self.whitelist and country and country not in self.whitelist:
            logger.debug(f"  SKIP (not whitelist {country}): {title}")
            return None

        # Статистика канала
        subs = int(stats.get("subscriberCount", 0))
        video_count = int(stats.get("videoCount", 0))
        hidden_subs = stats.get("hiddenSubscriberCount", False)

        if not hidden_subs and subs < self.min_subs:
            logger.debug(f"  SKIP (subs {subs} < {self.min_subs}): {title}")
            return None

        if video_count < self.min_videos:
            logger.debug(f"  SKIP (videos {video_count} < {self.min_videos}): {title}")
            return None

        # Последнее видео
        uploads_id = (
            content.get("relatedPlaylists", {}).get("uploads", "")
        )
        last_upload = self._get_last_upload_date(uploads_id)
        time.sleep(0.3)

        if last_upload:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_days)
            if last_upload < cutoff:
                logger.debug(f"  SKIP (last upload {last_upload.date()}): {title}")
                return None

        channel_url = f"https://www.youtube.com/channel/{channel_id}"

        logger.info(f"  ✅ PASS: {title} | {country} | subs={subs} | videos={video_count}")

        return {
            "channel_id": channel_id,
            "title": title,
            "url": channel_url,
            "country": country,
            "subscribers": subs,
            "video_count": video_count,
            "last_upload": last_upload.date() if last_upload else "",
            "description": description,
            "email": "",  
        }

    def filter(self, channel_ids: List[str]) -> List[Dict]:
        # Фильтровка списка по ID, вернуть прошедшие каналы.
        passed = []
        total = len(channel_ids)

        for i, chunk in enumerate(self._chunks(channel_ids, CHUNK_SIZE)):
            logger.info(
                f"[FILTER] Пачка {i+1} | обработано {min((i+1)*CHUNK_SIZE, total)}/{total}"
            )
            items = self._get_channel_batch(chunk)
            for item in items:
                result = self._passes_filter(item)
                if result:
                    passed.append(result)
            time.sleep(self.delay)

        logger.info(f"[FILTER] Итог: {len(passed)} каналов прошли фильтр из {total}")
        return passed
