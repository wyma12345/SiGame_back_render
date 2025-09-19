import json
import uuid
import random

from fastapi import FastAPI, Cookie, Response, Header

from starlette.responses import JSONResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from ConnectionManager import ConnectionManager
from models import Player, Package, Game, db

# region Константы
length_GUID = 24
# endregion

app = FastAPI()  # запуск приложения

# экземпляр класса ConnectionManager
manager = ConnectionManager()


# @app.get("/check_game/{session_id}")
# async def check_game(session_id):
#     """
#     Проверяет наличие у пользователя активной игры
#     :param session_id: токен
#     :return:
#     """
#     player = db.query(Player).filter(Player.GUID == session_id).first()
#     print(player)
#     if player is None:  # если игрк не найден
#         return {"active_game": "None"}
#     return {"active_game": player.game_id}  # если игрок найден возвращаем id игры
#
#
# @app.get("/start_new_game")
# async def start_new_game():
#     """
#     Начинает новую игру после запроса с Экрана
#     :return:
#     """
#     code_game = str(random.randint(100000, 999999))  # случайный код игры
#     new_game = Game(code=code_game)  # создаем игру
#     db.add(new_game)
#     db.commit()
#
#     db.refresh(new_game)  # обновляем данные
#     GUID = str(uuid.uuid4())  # GUID пользователя
#     player_screen = Player(GUID=GUID, game_id=new_game.id, is_screen=True)  # создаем пользователя экран
#     db.add(player_screen)
#     db.commit()
#
#     response = JSONResponse(content={"code_game": code_game})
#     response.set_cookie(key="session_id", value=GUID)
#
#     return response


connected_clients = []


@app.websocket("/lobby")
async def websocket_endpoint_lobby(websocket: WebSocket):

    if "session_id" in websocket.cookies:  # если есть куки тогда его заносим
        player = await manager.connect(websocket, websocket.cookies["session_id"])
    elif "connect_data" in websocket.headers:
        player = await manager.connect(websocket, "", json.loads(websocket.headers["connect_data"]))
    else:
        player = None

    # Если что-то пошло не так
    if player is None:
        await websocket.send_json({"error": "Пользователь не создан"})
        await websocket.close()

    try:
        while True:
            data: dict = await websocket.receive_json()
            if "event" in data:

                if data["event"] == "player_ready":
                    await manager.screen_cast({"event": "player_ready", "user_GUID": player.GUID}, player.game_id)

            if "settings" in data and player.is_leader:
                pass



    except WebSocketDisconnect:
        manager.disconnect(player.GUID)
