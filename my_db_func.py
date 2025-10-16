from models import db
from models import Player, Game
import zipfile
import urllib.parse
import os
import shutil

def find_screen_player(game_id: int) -> Player:
    return db.query(Player).filter(Player.game_id == game_id, Player.is_screen == True).first()


def find_game_id_for_user(user_GUID: str) -> int:
    return db.query(Player).filter(Player.GUID == user_GUID).first().game_id


def unpack_zip_advanced(zip_path, extract_to=None, max_length=255) -> bool:
    """
    Улучшенная версия с обработкой очень длинных путей

    Args:
        zip_path (str): Путь к zip-файлу
        extract_to (str): Папка для распаковки
        max_length (int): Максимальная длина пути (по умолчанию 255)
    """
    if extract_to is None:
        extract_to = os.path.splitext(zip_path)[0] + "_extracted"

    os.makedirs(extract_to, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for file_info in zip_ref.filelist:
                try:
                    # Декодируем имя файла
                    original_filename = urllib.parse.unquote(file_info.filename)

                    # Обрабатываем слишком длинные имена
                    safe_filename = make_path_safe(original_filename, extract_to, max_length)

                    # Создаем директории
                    os.makedirs(os.path.dirname(safe_filename), exist_ok=True)

                    # Извлекаем файл
                    with zip_ref.open(file_info) as source:
                        with open(safe_filename, 'wb') as target:
                            shutil.copyfileobj(source, target)

                    # print(f"Извлечен: {original_filename} -> {os.path.basename(safe_filename)}")

                except Exception as e:
                    print(f"Ошибка при извлечении {file_info.filename}: {e}")
                    return False

    except Exception as e:
        print(f"Ошибка при работе с zip-файлом: {e}")
        return False


def make_path_safe(original_path, base_dir, max_length=255):
    """
    Создает безопасный путь, избегая слишком длинных имен

    Args:
        original_path (str): Оригинальный путь
        base_dir (str): Базовая директория
        max_length (int): Максимальная длина пути

    Returns:
        str: Безопасный путь
    """
    # Полный путь
    full_path = os.path.join(base_dir, original_path)

    # Если путь не слишком длинный, возвращаем как есть
    if len(full_path) <= max_length:
        return full_path

    # Обрабатываем слишком длинные пути
    dir_name = os.path.dirname(original_path)
    file_name = os.path.basename(original_path)

    safe_name = urllib.parse.unquote(file_name)
    safe_path = os.path.join(base_dir, dir_name, safe_name)

    return safe_path


def list_zip_contents(zip_path):
    """
    Показывает содержимое zip-файла с информацией о кодировке
    """
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            print(f"Содержимое архива {zip_path}:")
            print("-" * 50)
            for file_info in zip_ref.filelist:
                decoded_name = urllib.parse.unquote(file_info.filename)
                print(f"Имя в архиве: {file_info.filename}")
                print(f"Декодированное: {decoded_name}")
                print(f"Длина: {len(decoded_name)}")
                print(f"Размер: {file_info.file_size} байт")
                print("-" * 30)
    except Exception as e:
        print(f"Ошибка при чтении архива: {e}")