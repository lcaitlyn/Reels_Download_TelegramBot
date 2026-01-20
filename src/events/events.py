"""
Модуль для определения типов событий аналитики
Все события сериализуются в JSON для отправки в Redis очередь
"""
import json
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, asdict


@dataclass
class DownloadCompletedEvent:
    """Событие завершения скачивания видео"""
    user_id: int
    video_id: str
    platform: str
    source: str  # 'message', 'inline', 'deep_link'
    timestamp: Optional[datetime] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
    
    def to_json(self) -> str:
        """Сериализация события в JSON"""
        data = asdict(self)
        # Преобразуем datetime в ISO строку для JSON
        data['timestamp'] = self.timestamp.isoformat()
        return json.dumps(data)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'DownloadCompletedEvent':
        """Десериализация события из JSON"""
        data = json.loads(json_str)
        # Преобразуем ISO строку обратно в datetime
        if isinstance(data['timestamp'], str):
            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)


@dataclass
class VideoViewClickedEvent:
    """Событие клика на видео (кнопка или deep link)"""
    user_id: int
    video_id: Optional[str]  # Может быть None для общих кликов
    event_type: str  # 'button_click', 'deep_link'
    timestamp: Optional[datetime] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
    
    def to_json(self) -> str:
        """Сериализация события в JSON"""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        return json.dumps(data)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'VideoViewClickedEvent':
        """Десериализация события из JSON"""
        data = json.loads(json_str)
        if isinstance(data['timestamp'], str):
            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)


@dataclass
class UserReferredEvent:
    """Событие реферального приглашения"""
    referrer_id: int
    new_user_id: int
    timestamp: Optional[datetime] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
    
    def to_json(self) -> str:
        """Сериализация события в JSON"""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        return json.dumps(data)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'UserReferredEvent':
        """Десериализация события из JSON"""
        data = json.loads(json_str)
        if isinstance(data['timestamp'], str):
            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)
