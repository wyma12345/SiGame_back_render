from models import db
from models import Player, Game


def find_screen_player(game_id: int) -> Player:
    return db.query(Player).filter(Player.game_id == game_id, Player.is_screen == True).first()


def find_game_id_for_user(user_GUID: str) -> int:
    return db.query(Player).filter(Player.GUID == user_GUID).first().game_id
