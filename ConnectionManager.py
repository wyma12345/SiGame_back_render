# Хранение подключенных клиентов
# версия 0.00.1
import os
import shutil
import uuid
import random
import json
import xmltodict
from datetime import datetime, timedelta
from typing import Dict, Union, List, Set
from urllib.parse import unquote
from sqlalchemy import JSON
from sqlalchemy.orm import relationship
from starlette.websockets import WebSocket

from Settings import settings
from models import db, Package
from models import Player, Game
from my_db_func import find_game_id_for_user, unpack_zip_advanced, list_zip_contents


async def upload_package(bin_data: bin, name_file: str, player: Player) -> dict:

    existing_file_names = [f for f in os.listdir('.') if os.path.isdir(f)]
    if name_file in existing_file_names:
        return {"error": "name_pack_already_taken"}

    upload_package_path: str = f"packages/{name_file}.zip"
    finally_package_path: str = f"packages/unpacked/{name_file}"

    with open(upload_package_path, 'wb') as f:  # Записываем полученную строку байтов как файл
        f.write(bin_data)

    error: bool = unpack_zip_advanced(upload_package_path, finally_package_path)

    if error:
        return {"error": "fail_extract_file"}

    with open(finally_package_path + "/content.xml", encoding="utf8") as xml_file:
        content = xmltodict.parse(xml_file.read())

    if content is None:
        return {"error": "error_decryption_content"}

    name_package: str = ""
    exists_package: Package = db.query(Package).filter(Package.name == name_package).first()
    if exists_package is not None:
        return {"error": "name_pack_already_taken"}

    package: Package = Package(templates_pack=finally_package_path, name=name_package,
                               content=content)  # загрузили пак
    db.add(package)  # сохраняем в БД
    db.commit()
    db.refresh(package)  # обновляем данные из бд, на всякий

    game = db.query(Game).filter(Game.id == player.game_id).first()  # ищем игру по коду
    game.package_id = package.id
    db.commit()  # коммитим без add, т.к. игра уже есть


class ConnectionManager:

    def __init__(self):

        # region создание списков подключений
        # Хранение активных соединений в виде {game_id: {user_GUID: WebSocket, }}
        self.active_connections: Dict[int, Dict[str, Union[WebSocket, str, None]]] = {}

        # Храним данные в виде {game_id: {"screen_GUID": user_GUID, "leader_GUID": user_GUID}}
        self.main_roles: Dict[int, Dict[str, Union[str, None]]] = {}

        # Храним готовность в в иде {game_id: {"user_GUID": bool}}
        self.ready_players: Dict[int, Set[str]] = {}

        self.settings: Dict[int, ""] = {}
        # endregion

        # region удаляем старые игры
        old_games = db.query(Game).filter(
            Game.time_created < datetime.today() - timedelta(hours=settings["game_lifetime"])).all()
        print("старые игры:", old_games)
        if old_games:
            for old_game in old_games:
                print("Игра удалена", old_game.code)
                db.delete(old_game)
            db.commit()
        # endregion

        # region заполнение списков подключений
        games = db.query(Game).all()
        for game in games:
            self.active_connections[game.id] = {}
            self.main_roles[game.id] = {"screen_GUID": None, "leader_GUID": None}
            self.ready_players[game.id] = set()
            self.settings[game.id] = ""
        # endregion

        print("active_connections:", self.active_connections)
        print("games:", db.query(Game).all())

    def __get_screen_GUID(self, game_id) -> str:
        """
        Выдает экран по id игры
        :param game_id:
        :return:
        """
        return self.main_roles[game_id]["screen_GUID"]

    def __get_leader_GUID(self, game_id) -> str:
        """
        Выдает ведущего по id игры
        :param game_id:
        :return:
        """
        return self.main_roles[game_id]["leader_GUID"]

    def check_add_player(self, player: Player) -> dict:
        """
        Добавление пользователя с пустым соединением
        :param player:
        :return:
        """

        if player.game_id not in self.active_connections:  # если игра только создана задаем значение основных ролей
            self.main_roles[player.game_id] = {"screen_GUID": None, "leader_GUID": None}
            self.active_connections[player.game_id] = {}
            self.ready_players[player.game_id] = set()

        # region Проверки
        if player.is_screen and player.is_leader:
            return {"error": "Лидер не может быть экраном"}

        if player.is_screen:
            if self.main_roles[player.game_id]["screen_GUID"] is not None:
                return {"error": "экран только один"}

        elif player.is_leader:
            if self.main_roles[player.game_id]["leader_GUID"] is not None:
                return {"error": "ведущий только один"}

        if not player.is_leader and not player.is_screen:
            if player.name == "":
                return {"error": "имя игрока пустое"}
            if db.query(Player).filter(Player.name == player.name,
                                       Player.game_id == player.game_id).first() is not None:
                return {"error": "имя игрока дублируется"}

        # endregion

        # self.active_connections[game_id][user_GUID] = None  # создание пустого соединения для игрока

        return {}

    async def append_settings(self, player: Player, settings: str) -> dict:
        """
        Загрузка настроек игры
        :param player:
        :return: Сообщение о загрузке
        """
        game: Game = player.game
        game.settings = settings
        db.commit()

    async def connect(self, websocket: WebSocket, user_GUID: str):
        """
        Ищет пользователя по переданному GUID
        Устанавливает соединение с пользователем.
        websocket.accept() — подтверждает подключение.
        """

        # region поиск пользователя по входящему GUID
        player = db.query(Player).filter(Player.GUID == user_GUID).first()

        # Если что-то пошло не так
        if player is None:
            return None

        await websocket.accept()  # подтверждаем соединение !важный момент!

        if player.game_id not in self.active_connections:
            self.active_connections[player.game_id] = {}
        self.active_connections[player.game_id][player.GUID] = websocket  # подключение игрока

        if player.is_screen:  # запоминаем экран, игра создается только при создании экрана

            if self.main_roles[player.game_id]["screen_GUID"] is not None:
                return None

            self.main_roles[player.game_id]["screen_GUID"] = player.GUID

            active_players_info = []
            usual_players = db.query(Player).filter(Player.game_id == player.game_id).all()

            for usual_player in usual_players:
                if usual_player.GUID in self.active_connections[player.game_id].keys() and not usual_player.is_screen:
                    active_players_info.append({
                        "user_name": usual_player.name,
                        "player_ready": usual_player.GUID in self.ready_players[player.game_id],
                        "user_GUID": usual_player.GUID,
                        "is_leader": usual_player.GUID == self.main_roles[player.game_id]["leader_GUID"]
                    })

            await websocket.send_json(
                {"event": "user_connect", "user_GUID": player.GUID, "players_info": active_players_info})

        elif player.is_leader:  # запоминаем лидера

            if self.main_roles[player.game_id]["leader_GUID"] is not None:
                return None

            self.main_roles[player.game_id]["leader_GUID"] = player.GUID
            package_list = ["test1", "test2", "test3"]

            await websocket.send_json({"user_GUID": player.GUID})
            await self.main_cast(
                {"event": "user_connect", "user_GUID": player.GUID, "is_leader": True, "package_list": package_list},
                player.game_id)
        else:

            player_ready = player.game_id in self.ready_players and player.GUID in self.ready_players[
                player.game_id]  # готов ли игрок

            await websocket.send_json({"user_GUID": player.GUID, "user_ready": player_ready})
            await self.main_cast({"event": "user_connect", "user_GUID": player.GUID, "user_name": player.name,
                                    "user_ready": player_ready},
                                 player.game_id)
        # endregion

        return player

    async def disconnect(self, player: Player):
        """
        Закрывает соединение и удаляет его из списка активных подключений.
        Если в комнате больше нет пользователей, удаляет игру из списка активных подключений.
        """

        if player.game_id == 0:
            player.game_id = find_game_id_for_user(player.GUID)

        if player.game_id in self.active_connections and player.GUID in self.active_connections[player.game_id].keys():

            # если это лидер или экран очищаем данные
            if self.main_roles[player.game_id]["leader_GUID"] == player.GUID:
                self.main_roles[player.game_id]["leader_GUID"] = None
            if self.main_roles[player.game_id]["screen_GUID"] == player.GUID:
                self.main_roles[player.game_id]["screen_GUID"] = None

            del self.active_connections[player.game_id][player.GUID]  # удаляем соединение

            if not self.active_connections[player.game_id]:  # если игроков не осталось удаляем игру

                db.delete(db.query(Game).filter(Game.id == player.game_id).first())
                db.commit()

                if player.game_id in self.active_connections:
                    del self.active_connections[player.game_id]
                if player.game_id in self.main_roles:
                    del self.main_roles[player.game_id]
                if player.game_id in self.ready_players:
                    del self.ready_players[player.game_id]

                # удаляем пак
                game: Game = player.game  # ищем игру
                if game is None:
                    return

                package: Package = game.package  # ищем пак
                if package is None or package.default:  # если пак найден или он изначально загруженный
                    return

                shutil.rmtree("packages/unpacked/" + package.name)

                db.delete(package)
                db.commit()

            else:
                await self.main_cast({"event": "user_disconnected", "user_GUID": player.GUID}, player.game_id)

    async def broad_cast(self, received_data: dict, received_game_id: int, cast_main_role: bool = False):
        """
        Рассылает сообщение всем пользователям в комнате.
        :param received_data: Данные для отправки
        :param received_game_id: game_id
        :param cast_main_role: Отправлять для главным ролям
        :return:
        """

        main_role_guid = self.main_roles[received_game_id].values()

        if received_game_id in self.active_connections:
            for user_GUID, connection in self.active_connections[received_game_id].items():  # рассылаем всем пользователям

                if not cast_main_role and user_GUID in main_role_guid:
                    continue

                if connection is not None:
                    await connection.send_json(received_data)

    async def screen_cast(self, received_data: dict, received_game_id: int):
        """
        Отправка информации на экран
        """
        if received_game_id in self.main_roles:

            screen_player_GUID = self.__get_screen_GUID(received_game_id)  # ищем экран
            if screen_player_GUID is None:
                await self.broad_cast({"error": "screen_not_found"}, received_game_id)
                return

            if received_game_id in self.active_connections and screen_player_GUID in self.active_connections[
                received_game_id]:
                connection = self.active_connections[received_game_id][screen_player_GUID]
                await connection.send_json(received_data)

    async def leader_cast(self, received_data: dict, received_game_id: int):
        """
        Отправка информации на ведущего
        """
        if received_game_id in self.main_roles:

            leader_player_GUID = self.__get_leader_GUID(received_game_id)  # ищем экран
            if leader_player_GUID is None:
                await self.screen_cast({"error": "leader_not_found"}, received_game_id)
                return

            if received_game_id in self.active_connections and leader_player_GUID in self.active_connections[
                received_game_id]:
                connection = self.active_connections[received_game_id][leader_player_GUID]
                await connection.send_json(received_data)

    async def main_cast(self, received_data: dict, received_game_id: int):

        await self.screen_cast(received_data, received_game_id)
        await self.leader_cast(received_data, received_game_id)

    async def player_ready(self, player: Player, is_ready: bool) -> list:

        if player.game_id not in self.ready_players:
            self.ready_players[player.game_id] = set()

        if is_ready:
            self.ready_players[player.game_id].add(player.GUID)
        else:
            self.ready_players[player.game_id].discard(player.GUID)

        return list(self.ready_players[player.game_id])

    async def start_game(self, player: Player):
        """
        Начинаем игру
        :param player:
        :return:
        """
        if not player.is_leader:
            await self.active_connections[player.game_id][player.GUID].send_json({"error": "is_not_leader"})

        package = db.query(Package).filter(Package.id == 1).first()
        if package is None:
            await self.active_connections[player.game_id][player.GUID].send_json({"error": "bad_package"})

        content: dict = json.loads(unquote(package.content))
        print(content)

        game_info = content["package"]
        first_round_info = content["rounds"][0]

        self.ready_players[player.game_id].add(self.__get_screen_GUID(player.game_id))
        self.ready_players[player.game_id].add(player.GUID)

        if self.active_connections[player.game_id] == self.ready_players[player.game_id]:  # если все подключенные игроки готовы
            await self.broad_cast({"event": "game_start", "first_round_info": first_round_info}, player.game_id)
            await self.main_cast({"event": "game_start", "game_info": game_info,
                                       "first_round_info": first_round_info, "settings": self.settings[player.game_id]},
                                 player.game_id)
