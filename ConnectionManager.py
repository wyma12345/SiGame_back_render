# Хранение подключенных клиентов
# версия 0.00.1
import shutil
import uuid
import random
import json
import xmltodict
from datetime import datetime, timedelta
from typing import Dict, Union, List, Set
import urllib.parse
from sqlalchemy import JSON
from sqlalchemy.orm import relationship
from starlette.websockets import WebSocket

from Settings import settings
from models import db, Package
from models import Player, Game
from my_db_func import find_game_id_for_user, unpack_zip_advanced, list_zip_contents


class ConnectionManager:

    def __init__(self):

        # region создание списков подключений
        # Хранение активных соединений в виде {game_id: {user_GUID: WebSocket, }}
        self.active_connections: Dict[int, Dict[str, Union[WebSocket, str, None]]] = {}

        # Храним данные в виде {game_id: {"screen_GUID": user_GUID, "leader_GUID": user_GUID}}
        self.main_roles: Dict[int, Dict[str, Union[str, None]]] = {}

        # Храним готовность в в иде {game_id: {"user_GUID": bool}}
        self.ready_players: Dict[int, Set[str]] = {}
        # endregion

        old_games = db.query(Game).filter(
            Game.time_created < datetime.now() - timedelta(minutes=settings["game_lifetime"]))
        if old_games:
            for old_game in old_games:
                print(old_game.code)
                db.delete(old_game)
            db.commit()

        games = db.query(Game).all()
        for game in games:
            self.active_connections[game.id] = {}
            self.main_roles[game.id] = {"screen_GUID": None, "leader_GUID": None}

        print(self.active_connections)

    def __get_screen_GUID(self, game_id) -> str:
        """
        Выдает экран по id игры
        :param game_id:
        :return:
        """
        return self.main_roles[game_id]["screen_GUID"]

    def check_add_player(self, player: Player) -> dict:
        """
        Добавление пользователя с пустым соединением
        :param player:
        :return:
        """

        if player.game_id not in self.active_connections:  # если игра только создана задаем значение основных ролей
            self.main_roles[player.game_id] = {"screen_GUID": None, "leader_GUID": None}
            self.active_connections[player.game_id] = {}

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
            if db.query(Player).filter(Player.name == player.name,  Player.game_id == player.game_id).first() is not None:
                return {"error": "имя игрока дублируется"}

        # endregion

        # self.active_connections[game_id][user_GUID] = None  # создание пустого соединения для игрока

        return {}

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
            usual_players = db.query(Player).filter(Player.game_id == player.game_id, not Player.is_screen).all()
            for usual_player in usual_players:
                if usual_player.GUID in self.active_connections[player.game_id]:
                    active_players_info.append({
                        "user_name": usual_player.name,
                        "player_ready": usual_player.GUID in self.ready_players[player.game_id],
                        "user_GUID": usual_player.GUID,
                        "is_leader": usual_player.GUID == self.main_roles[player.game_id]["leader_GUID"]
                    })

            await websocket.send_json({"event": "user_connect", "user_GUID": player.GUID, "players_info": active_players_info})

        elif player.is_leader:  # запоминаем лидера

            if self.main_roles[player.game_id]["leader_GUID"] is not None:
                return None

            self.main_roles[player.game_id]["leader_GUID"] = player.GUID
            package_list = ["test1", "test2", "test3"]

            await websocket.send_json({"user_GUID": player.GUID})
            await self.screen_cast({"event": "user_connect", "user_GUID": player.GUID, "is_leader": True,  "package_list": package_list},
                                   player.game_id)
        else:

            player_ready = player.game_id in self.ready_players and player.GUID in self.ready_players[player.game_id]  # готов ли игрок

            await websocket.send_json({"user_GUID": player.GUID,  "user_ready": player_ready})
            await self.screen_cast({"event": "user_connect", "user_GUID": player.GUID, "user_name": player.name, "user_ready": player_ready},
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
            await self.screen_cast({"event": "user_disconnect"}, player.game_id)

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
                await self.screen_cast({"event": "user_disconnected", "user_GUID": player.GUID}, player.game_id)

    async def broad_cast(self, received_data: dict, received_game_id: int):
        """
        Рассылает сообщение всем пользователям в комнате.
        """
        if received_game_id in self.active_connections:
            for _, connection in self.active_connections[received_game_id].items():  # рассылаем всем пользователям
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

            if received_game_id in self.active_connections and screen_player_GUID in self.active_connections[received_game_id]:
                connection = self.active_connections[received_game_id][screen_player_GUID]
                await connection.send_json(received_data)

    async def player_ready(self, user_GUID: str, game_id: int, is_ready: bool, ) -> list:

        if game_id not in self.ready_players:
            self.ready_players[game_id] = set()

        if is_ready:
            self.ready_players[game_id].add(user_GUID)
        else:
            self.ready_players[game_id].discard(user_GUID)

        return list(self.ready_players[game_id])

    async def start_game(self, player: Player):
        """
        Начинаем игру
        :param player:
        :return:
        """
        if not player.is_leader:
            await self.active_connections[player.game_id][player.GUID].send_json({"error": "это не лидер"})

        if self.active_connections[player.game_id] == self.ready_players[
            player.game_id]:  # если все подключенные игроки готовы
            await self.broad_cast({"event": "game_start"}, player.game_id)

    async def upload_package(self, bin_data: bin, name_package: str, player: Player) -> dict:

        exists_package: Package = db.query(Package).filter(Package.name == name_package).first()
        if exists_package is not None:
            return {"error": "Уже существует такой пак"}

        upload_package_path: str = f"packages/{name_package}.zip"
        finally_package_path: str = f"packages/unpacked/{name_package}"

        with open(upload_package_path, 'wb') as f:  # Записываем полученную строку байтов как файл
            f.write(bin_data)

        error: bool = unpack_zip_advanced(upload_package_path, finally_package_path)

        if error:
            return {"error": "не удалось распаковать файл"}

        with open(finally_package_path + "/content.xml", encoding="utf8") as xml_file:
            content = xmltodict.parse(xml_file.read())

        if content is None:
            return {"error": "не удалось перевести в JSON content.xml"}

        package: Package = Package(templates_pack=finally_package_path, name=name_package,
                                   content=content)  # загрузили пак
        db.add(package)  # сохраняем в БД
        db.commit()
        db.refresh(package)  # обновляем данные из бд, на всякий

        game = db.query(Game).filter(Game.id == player.game_id).first()  # ищем игру по коду
        game.package_id = package.id
        db.commit()  # коммитим без add, т.к. игра уже есть