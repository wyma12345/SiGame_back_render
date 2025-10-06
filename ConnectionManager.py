# Хранение подключенных клиентов
# версия 0.00.1
import uuid
import random
from datetime import datetime, timedelta
from typing import Dict, Union, List, Set

from sqlalchemy.orm import relationship
from starlette.websockets import WebSocket

from Settings import settings
from models import db
from models import Player, Game
from my_db_func import find_game_id_for_user


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

    def add_user(self, game_id: int, user_GUID: str, is_screen: bool = False, is_leader: bool = False) -> dict:
        """
        Добавление пользователя с пустым соединением
        :param is_leader:
        :param is_screen:
        :param user_GUID:
        :param game_id:
        :return:
        """

        if game_id not in self.active_connections:  # если игра только создана задаем значение основных ролей
            self.main_roles[game_id] = {"screen_GUID": None, "leader_GUID": None}
            self.active_connections[game_id] = {}

        # region Проверки
        if is_screen and is_leader:
            return {"error": "Лидер не может быть экраном"}

        if is_screen:
            if self.main_roles[game_id]["screen_GUID"] is not None:
                return {"error": "экран только один"}

        elif is_leader:
            if self.main_roles[game_id]["leader_GUID"] is not None:
                return {"error": "ведущий только один"}

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
            self.main_roles[player.game_id]["screen_GUID"] = player.GUID

            active_players = self.active_connections[player.game_id].keys()  # указываем готовых игроков
            active_players_info = {}
            for player_GUID in active_players:
                active_players_info[player_GUID] = {
                    "player_ready": player_GUID in self.ready_players,
                    "user_GUID": player_GUID,
                    "is_leader": player_GUID == self.main_roles[player.game_id]["leader_GUID"],
                    "user_name": player.name,
                }

            await websocket.send_json({"event": "user_connect", "user_GUID": player.GUID})

        elif player.is_leader:  # запоминаем лидера
            self.main_roles[player.game_id]["leader_GUID"] = player.GUID
            await websocket.send_json({"user_GUID": player.GUID})
            await self.screen_cast({"event": "user_connect", "user_GUID": player.GUID, "is_leader": True},
                                   player.game_id)
        else:
            await websocket.send_json({"user_GUID": player.GUID})
            await self.screen_cast({"event": "user_connect", "user_GUID": player.GUID, "user_name": player.name},
                                   player.game_id)
        # endregion

        return player

    def disconnect(self, received_user_GUID: str, game_id: int = 0):
        """
        Закрывает соединение и удаляет его из списка активных подключений.
        Если в комнате больше нет пользователей, удаляет игру из списка активных подключений.
        """

        if game_id == 0:
            game_id = find_game_id_for_user(received_user_GUID)

        if game_id in self.active_connections and received_user_GUID in self.active_connections[game_id]:

            # если это лидер или экран очищаем данные
            if self.main_roles[game_id]["leader_GUID"] == self.active_connections[game_id][received_user_GUID]:
                self.main_roles[game_id]["leader_GUID"] = None
            if self.main_roles[game_id]["screen_GUID"] == self.active_connections[game_id][received_user_GUID]:
                self.main_roles[game_id]["screen_GUID"] = None

            del self.active_connections[game_id][received_user_GUID]  # удаляем соединение

            if not self.active_connections[game_id]:  # если игроков не осталось удаляем игру

                db.delete(db.query(Game).filter(Game.id == game_id).first())
                db.commit()

                if game_id in self.active_connections:
                    del self.active_connections[game_id]
                if game_id in self.main_roles:
                    del self.main_roles[game_id]
                if game_id in self.ready_players:
                    del self.ready_players[game_id]

            else:
                self.screen_cast({"event": "user_disconnected", "user_GUID": received_user_GUID}, game_id)

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

        if self.active_connections[player.game_id] == self.ready_players[player.game_id]:  # если все подключенные игроки готовы
            await self.broad_cast({"event": "game_start"}, player.game_id)





