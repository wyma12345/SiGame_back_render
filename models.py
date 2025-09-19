from sqlalchemy.orm import sessionmaker, configure_mappers
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Boolean, JSON, create_engine, SmallInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, Mapped
from sqlalchemy.sql import func


# строка подключения к БД
SQLALCHEMY_DATABASE_URL = "sqlite:///./sql_app.db"
# создание движка для работы с БД
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
# строка подключения PostgresSql
# SQLALCHEMY_DATABASE_URL = "postgresql://postgres:admin@localhost:5432/sigame"

# создаем сессию подключения к бд
SessionLocal = sessionmaker(autoflush=False, bind=engine)
db = SessionLocal()  # Объект для взаимодействия с БД
# создаем базовый класс для моделей
Base = declarative_base()

# 1 - screen, 2 -leader, 3- player
# создаем модель, объекты которой будут храниться в бд
class Player(Base):
    __tablename__ = 'players'
    id: Mapped[int] = Column(Integer, primary_key=True, index=True, nullable=False)
    GUID: Mapped[str] = Column(String, nullable=False)
    name: Mapped[str] = Column(String, nullable=True)
    is_leader: Mapped[bool] = Column(Boolean, nullable=False, default=False)
    is_screen: Mapped[bool] = Column(Boolean, nullable=False, default=False)

    game_id: Mapped[int] = Column(Integer, ForeignKey('games.id'), nullable=False)
    game: Mapped["Game"] = relationship("Game", back_populates="players")


# создаем модель, объекты которой будут храниться в бд
class Game(Base):
    __tablename__ = 'games'
    id: Mapped[int] = Column(Integer, primary_key=True, index=True, nullable=False)
    settings: Mapped[str] = Column(JSON, nullable=True)
    now_round: Mapped[int] = Column(Integer, nullable=False, default=0)
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    code: Mapped[str] = Column(String, nullable=False)

    players = relationship("Player", back_populates="game", cascade="all,delete")

    package_id = Column(Integer, ForeignKey('packages.id'), nullable=True)
    package: Mapped["Package"] = relationship("Package", back_populates="games")


class Package(Base):
    __tablename__ = 'packages'
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    templates_pack = Column(String, nullable=False)
    name = Column(String, nullable=False)
    content = Column(JSON, nullable=False)

    games = relationship("Game", back_populates="package")


# создаем таблицы
Base.metadata.create_all(bind=engine)
