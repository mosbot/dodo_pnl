"""Auth подсистема pnl-service.

Состоит из:
- models.py — ORM-модели User, Session
- passwords.py — argon2id хеширование/верификация
- users.py — CRUD-обёртки (создать юзера, найти по username, сменить пароль...)
- sessions.py — создание/валидация/отзыв сессий (в S1.3)
- middleware.py — FastAPI-middleware для извлечения user из cookie (в S1.3)
"""
