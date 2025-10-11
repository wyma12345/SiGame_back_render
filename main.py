import json
import uuid
import random

from fastapi import FastAPI, Cookie, Response, Header, Body
from starlette.middleware.cors import CORSMiddleware

from starlette.responses import JSONResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from ConnectionManager import ConnectionManager
from Settings import settings
from models import Player, Package, Game, db

# region Константы
length_GUID = 24
# endregion

app = FastAPI()  # запуск приложения

app.add_middleware(  # настраиваем CORS
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# экземпляр класса ConnectionManager
manager = ConnectionManager()


@app.post("/creategame")
async def create_game(data=Body()):
    """
    Подключает и создает нового игрока
    :return:
    """

    # region получение входящих данных из полученного Headers.new_received_data
    received_game_code = data["game_code"] if "game_code" in data else ""
    received_user_name = data["user_name"] if "user_name" in data else ""
    received_user_is_screen = str.lower(data["is_screen"]) == "true" if "is_screen" in data else False
    received_user_is_leader = str.lower(data["is_leader"]) == "true" if "is_leader" in data else False
    # endregion

    # region Создание игрока

    GUID = str(uuid.uuid4())  # GUID пользователя
    content = {}

    if received_user_is_screen:  # если это экран

        code_game = str(random.randint(100000, 999999))  # случайный код игры
        game = Game(code=code_game)  # создаем игру
        db.add(game)
        db.commit()
        db.refresh(game)  # обновляем данные

        player = Player(GUID=GUID, game_id=game.id, is_screen=True)  # создаем пользователя экран

        content.update({"game_code": game.code})

    elif received_game_code != "":  # если не экран, то код должен быть

        game = db.query(Game).filter(Game.code == received_game_code).first()  # ищем игру по коду
        if game is None:  # если игры с таким кодом нет
            return JSONResponse(content={"error": "game_not_found"}, status_code=400)

        player = Player(GUID=GUID, game_id=game.id, name=received_user_name,
                        is_leader=received_user_is_leader)  # создаем пользователя

    else:
        return JSONResponse(content={"error": "empty_code"}, status_code=400)

    # endregion

    # region проверка возможности подключения игрока
    error: dict = manager.check_add_player(player)
    if error != {}:
        return JSONResponse(content=error)
    # endregion

    db.add(player)
    db.commit()

    content.update({"event": "user_created", "user_GUID": player.GUID})

    if received_user_is_leader:
        content.update({"packege_list": ["test1", "test2"]})

    # response = JSONResponse(content=content)
    # response.set_cookie(key="session_id", value=GUID)  # установка куки
    return JSONResponse(content=content)


@app.websocket("/{user_GUID}")
async def websocket_endpoint_lobby(websocket: WebSocket, user_GUID: str):

    player = await manager.connect(websocket, user_GUID)
    if player is None:
        await websocket.close(403, "bad_user_GUID")
        return

    try:
        while True:
            data: dict = await websocket.receive_json()
            if "event" in data:

                if data["event"] in ("player_ready", "player_unready"):
                    ready_players = await manager.player_ready(player.GUID, player.game_id, data["event"] == "player_ready")
                    await websocket.send_json({"status": "success"})
                    await manager.screen_cast({"event": data["event"], "user_GUID": player.GUID,
                                               "ready_players": ready_players}, player.game_id)
                if data["event"] == "start_game":
                    await websocket.send_json(manager.start_game(player))


            if "settings" in data and player.is_leader:
                pass



    except WebSocketDisconnect:
        manager.disconnect(player.GUID, player.game_id)
