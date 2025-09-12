# Хранение подключенных клиентов
import uuid
import random
from typing import Dict, Union

from starlette.websockets import WebSocket

from models import db
from models import Player, Game
from my_db_func import find_game_id_for_user


class ConnectionManager:
    def __init__(self) :
        # Хранение активных соединений в виде {game_id: {user_GUID: WebSocket, "screen": user_GUID}}
        self.active_connections: Dict[int, Dict[str, Union[WebSocket,str, None]]] = {}

    def __get_screen_GUID(self, game_id) -> str:
        """
        Выдает экран по id игры
        :param game_id:
        :return:
        """
        return self.active_connections[game_id]["screen_GUID"]

    async def connect(self, websocket: WebSocket, received_user_GUID: str = "", new_received_data=None):
        """
        Если передан GUID ищет пользователя, иначе создает нового
        Если это экран и новый пользователь, создает новую игру, иначе ищет игру по пользователю
        Устанавливает соединение с пользователем.
        websocket.accept() — подтверждает подключение.
        """

        if new_received_data is None:
            new_received_data = {}
        new_received_game_code = new_received_data["game_code"] if "game_code" in new_received_data else ""
        new_received_user_name = new_received_data["user_name"] if "user_name" in new_received_data else ""
        new_received_user_is_screen = str.lower(new_received_data["is_screen"]) == "true" if "is_screen" in new_received_data else False
        new_received_user_is_leader = str.lower(new_received_data["is_leader"]) == "true" if "is_leader" in new_received_data else False

        await websocket.accept()  # подтверждаем соединение !важный момент!

        player = None
        if received_user_GUID != "":
            player = db.query(Player).filter(Player.GUID == received_user_GUID).first()

            if player is not None:
                return player
            elif new_received_data == {}:  # если игрок не найден, а данных для создания нового нет
                await websocket.send_json({"error": "Игрока с таким session_id нет"})
                return None

        # теперь создаем игрока если он не найден, но данные есть
        if new_received_user_is_screen:  # если это новый экран

            code_game = str(random.randint(100000, 999999))  # случайный код игры
            new_game = Game(code=code_game)  # создаем игру
            db.add(new_game)
            db.commit()

            db.refresh(new_game)  # обновляем данные
            GUID = str(uuid.uuid4())  # GUID пользователя
            player = Player(GUID=GUID, game_id=new_game.id, is_screen=True)  # создаем пользователя экран
            db.add(player)
            db.commit()

            db.refresh(player)  # обновляем данные

        elif not new_received_user_is_screen and new_received_game_code != "":  # если не экран, то код должен быть

            find_game = db.query(Game).filter(Game.code == new_received_game_code).first()  # ищем игру по коду
            if find_game is None:  # если игры с таким кодом нет
                await websocket.send_json({"error": "Игры с таким кодом нет"})
                return

            GUID = str(uuid.uuid4())  # GUID пользователя
            player = Player(GUID=GUID, game_id=find_game.id, name=new_received_user_name, is_leader=new_received_user_is_leader)  # создаем пользователя
            db.add(player)
            db.commit()

        else:
            await websocket.send_json({"error": "Пришел пустой код"})
            return

        game_id = player.game_id  # получаем id игры из игрока
        if game_id not in self.active_connections:  # если в активных соединениях нет пока игры, то добавляем
            self.active_connections[game_id] = {"screen_GUID": None}

        self.active_connections[game_id][player.GUID] = websocket  # подключение игрока

        if player.is_screen:  # запоминаем экран
            self.active_connections[game_id]["screen_GUID"] = player.GUID
            await self.screen_cast({"text": "игра создана", "code": new_game.code}, game_id)
        elif player.is_leader:
            await self.screen_cast({"text": "Игрок подключился", "user_GUID":  player.GUID, "is_leader": True}, game_id)
        else:
            await self.screen_cast({"text": "Игрок подключился", "user_GUID":  player.GUID, "is_leader": False}, game_id)

        return player

    def disconnect(self, received_user_GUID: str):
        """
        Закрывает соединение и удаляет его из списка активных подключений.
        Если в комнате больше нет пользователей, удаляет игру из списка активных подключений.
        """

        game_id = find_game_id_for_user(received_user_GUID)

        if game_id in self.active_connections and received_user_GUID in self.active_connections[game_id]:
            del self.active_connections[game_id][received_user_GUID]

            if not self.active_connections[game_id]:  # если игроков не осталось удаляем игру
                del self.active_connections[game_id]

    async def broad_cast(self, received_data: dict, received_game_id: int):
        """
        Рассылает сообщение всем пользователям в комнате.
        """
        if received_game_id in self.active_connections:
            for _, connection in self.active_connections[received_game_id].items():  # рассылаем всем пользователям кроме экрана
                if connection is not None:
                    await connection.send_json(received_data)

    async def screen_cast(self, received_data: dict, received_game_id: int):
        """
        Отправка информации на экран
        """
        if received_game_id in self.active_connections:

            screen_player_GUID: str = self.__get_screen_GUID(received_game_id)  # ищем экран
            if screen_player_GUID is None:
                await self.broad_cast({"error": "Игрок с ролью экран не найден"}, received_game_id)
                return

            connection = self.active_connections[received_game_id][screen_player_GUID]
            await connection.send_json(received_data)
