"""
Тесты для модуля downloader.py
"""
import os
import unittest
from unittest.mock import Mock, patch, MagicMock
import tempfile
import shutil

from downloader import VideoDownloader
from utils import get_platform


class TestVideoDownloader(unittest.TestCase):
    """Тесты для класса VideoDownloader"""
    
    def setUp(self):
        """Настройка перед каждым тестом"""
        # Создаем временную директорию для тестов
        self.test_dir = tempfile.mkdtemp()
        self.downloader = VideoDownloader(download_dir=self.test_dir)
    
    def tearDown(self):
        """Очистка после каждого теста"""
        # Удаляем временную директорию
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    def test_init(self):
        """Тест инициализации VideoDownloader"""
        downloader = VideoDownloader(download_dir="test_downloads")
        self.assertEqual(downloader.download_dir, "test_downloads")
        self.assertTrue(downloader.compress_short_videos)
        self.assertTrue(os.path.exists("test_downloads"))
        
        # Очистка
        if os.path.exists("test_downloads"):
            shutil.rmtree("test_downloads")
    
    def test_get_format_for_platform_tiktok(self):
        """Тест формата для TikTok (должен быть сжатый)"""
        format_str = self.downloader._get_format_for_platform('tiktok')
        # Должен содержать ограничение по высоте или worst
        self.assertTrue('height' in format_str or 'worst' in format_str.lower())
        self.assertIn('mp4', format_str)
    
    def test_get_format_for_platform_instagram(self):
        """Тест формата для Instagram (должен быть сжатый)"""
        format_str = self.downloader._get_format_for_platform('instagram')
        self.assertTrue('height' in format_str or 'worst' in format_str.lower())
        self.assertIn('mp4', format_str)
    
    def test_get_format_for_platform_youtube(self):
        """Тест формата для YouTube"""
        format_str = self.downloader._get_format_for_platform('youtube')
        self.assertIn('mp4', format_str)
    
    def test_get_format_without_compression(self):
        """Тест формата без сжатия"""
        downloader = VideoDownloader(compress_short_videos=False)
        format_str = downloader._get_format_for_platform('tiktok')
        # Без сжатия должно быть best качество
        self.assertIn('best', format_str.lower())
    
    @patch('downloader.yt_dlp.YoutubeDL')
    def test_download_video_success(self, mock_ydl_class):
        """Тест успешного скачивания видео"""
        # Мокаем yt-dlp
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        
        # Настраиваем мок
        mock_info = {
            'id': 'test_video_123',
            'duration': 30,
            'title': 'Test Video'
        }
        mock_ydl_instance.extract_info.return_value = mock_info
        
        # Создаем фиктивный файл
        test_file_path = os.path.join(self.test_dir, 'test_video_123.mp4')
        with open(test_file_path, 'w') as f:
            f.write('test video content')
        
        # Тестируем
        url = "https://www.tiktok.com/@user/video/123"
        result = self.downloader.download_video(url)
        
        # Проверки
        self.assertIsNotNone(result)
        self.assertEqual(result, test_file_path)
        mock_ydl_instance.extract_info.assert_called_once_with(url, download=False)
        mock_ydl_instance.download.assert_called_once_with([url])
    
    @patch('downloader.yt_dlp.YoutubeDL')
    def test_download_video_platform_detection(self, mock_ydl_class):
        """Тест определения платформы при скачивании"""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        
        mock_info = {'id': 'test_123', 'duration': 15}
        mock_ydl_instance.extract_info.return_value = mock_info
        
        # Создаем файл
        test_file = os.path.join(self.test_dir, 'test_123.mp4')
        with open(test_file, 'w') as f:
            f.write('test')
        
        # Тест TikTok
        url_tiktok = "https://www.tiktok.com/@user/video/123"
        self.downloader.download_video(url_tiktok)
        
        # Проверяем, что был вызван с правильными опциями
        call_args = mock_ydl_class.call_args
        self.assertIsNotNone(call_args)
        ydl_opts = call_args[0][0] if call_args[0] else call_args[1]
        self.assertIn('format', ydl_opts)
    
    @patch('downloader.yt_dlp.YoutubeDL')
    def test_download_video_file_not_found_by_id(self, mock_ydl_class):
        """Тест когда файл не найден по ID, но найден последний измененный"""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        
        mock_info = {'id': 'wrong_id', 'duration': 10}
        mock_ydl_instance.extract_info.return_value = mock_info
        
        # Создаем файл с другим именем
        test_file = os.path.join(self.test_dir, 'actual_file.mp4')
        with open(test_file, 'w') as f:
            f.write('test content')
        
        url = "https://www.instagram.com/p/ABC123/"
        result = self.downloader.download_video(url)
        
        # Должен найти последний файл
        self.assertIsNotNone(result)
        self.assertEqual(result, test_file)
    
    @patch('downloader.yt_dlp.YoutubeDL')
    def test_download_video_download_error(self, mock_ydl_class):
        """Тест обработки ошибки скачивания"""
        import yt_dlp
        
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        
        # Эмулируем ошибку скачивания
        mock_ydl_instance.extract_info.side_effect = yt_dlp.utils.DownloadError("Video unavailable")
        
        url = "https://www.youtube.com/watch?v=invalid"
        result = self.downloader.download_video(url)
        
        # Должен вернуть None при ошибке
        self.assertIsNone(result)
    
    @patch('downloader.yt_dlp.YoutubeDL')
    def test_download_video_general_exception(self, mock_ydl_class):
        """Тест обработки общей ошибки"""
        mock_ydl_instance = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl_instance
        
        # Общая ошибка
        mock_ydl_instance.extract_info.side_effect = Exception("Unexpected error")
        
        url = "https://www.tiktok.com/@user/video/123"
        result = self.downloader.download_video(url)
        
        # Должен вернуть None и не упасть
        self.assertIsNone(result)
    
    def test_download_video_integration_skip_if_no_internet(self):
        """Интеграционный тест (пропускается, если нет интернета)"""
        # Этот тест можно запускать вручную для проверки реального скачивания
        # Для автоматических тестов он пропускается
        import socket
        
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=3)
        except OSError:
            self.skipTest("No internet connection")
        
        # Небольшой YouTube Shorts для теста (если есть)
        # url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"  # Пример
        # result = self.downloader.download_video(url)
        # self.assertIsNotNone(result)
        # self.assertTrue(os.path.exists(result))


class TestFormatSelection(unittest.TestCase):
    """Тесты для выбора формата"""
    
    def test_format_tiktok_compressed(self):
        """Проверка что TikTok использует сжатый формат"""
        downloader = VideoDownloader(compress_short_videos=True)
        format_str = downloader._get_format_for_platform('tiktok')
        # Должно быть ограничение качества
        self.assertTrue(any(keyword in format_str.lower() for keyword in ['height', 'worst']))
    
    def test_format_instagram_compressed(self):
        """Проверка что Instagram использует сжатый формат"""
        downloader = VideoDownloader(compress_short_videos=True)
        format_str = downloader._get_format_for_platform('instagram')
        self.assertTrue(any(keyword in format_str.lower() for keyword in ['height', 'worst']))
    
    def test_format_compression_disabled(self):
        """Проверка что без сжатия используется best качество"""
        downloader = VideoDownloader(compress_short_videos=False)
        format_str = downloader._get_format_for_platform('tiktok')
        # Должно быть best качество
        self.assertIn('best', format_str.lower())


if __name__ == '__main__':
    # Настройка логирования для тестов
    import logging
    logging.basicConfig(level=logging.INFO)
    
    unittest.main(verbosity=2)
