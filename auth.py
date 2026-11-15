from hashlib import pbkdf2_hmac, sha256
from hmac import compare_digest
from secrets import token_hex, token_urlsafe
from time import time

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import text

bearer = HTTPBearer(auto_error=False)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=128)


def derive(password, salt):
    return pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()


def create_user(db, credentials, role):
    salt = token_hex(16)
    return db.execute(text("""
        INSERT INTO users (username, salt, password_hash, role)
        VALUES (:username, :salt, :password_hash, :role)
        ON CONFLICT (username) DO NOTHING
    """), dict(username=credentials.username.lower(), salt=salt,
               password_hash=derive(credentials.password, salt), role=role)).rowcount == 1


def install_auth(app, engine):
    def require(role):
        def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
            if credentials:
                with engine.connect() as db:
                    actual = db.execute(text("""
                        SELECT users.role, users.username FROM sessions JOIN users USING (username)
                        WHERE token_hash = :token AND expires > :now
                    """), dict(token=sha256(credentials.credentials.encode()).hexdigest(),
                               now=int(time()))).mappings().one_or_none()
                if actual and actual['role'] == role:
                    return actual['username']
                if actual:
                    raise HTTPException(403, "This role cannot perform that action")
            raise HTTPException(401, "Sign in first", headers={"WWW-Authenticate": "Bearer"})
        return authorize

    @app.post("/auth/register", status_code=201, tags=["Accounts"])
    def register(credentials: Credentials):
        with engine.begin() as db:
            if not create_user(db, credentials, "attendee"):
                raise HTTPException(409, "Username already exists")
        return {"username": credentials.username.lower(), "role": "attendee"}

    @app.post("/auth/login", tags=["Accounts"])
    def login(credentials: Credentials):
        username = credentials.username.lower()
        with engine.begin() as db:
            user = db.execute(text("""
                SELECT salt, password_hash, role FROM users WHERE username = :username
            """), dict(username=username)).mappings().one_or_none()
            calculated = derive(credentials.password, user["salt"] if user else "00" * 16)
            if not user or not compare_digest(calculated, user["password_hash"]):
                raise HTTPException(401, "Invalid username or password")
            token = token_urlsafe(32)
            db.execute(text("""
                INSERT INTO sessions (token_hash, username, expires) VALUES (:token, :username, :expires)
            """), dict(token=sha256(token.encode()).hexdigest(), username=username,
                       expires=int(time()) + 8 * 3600))
        return {"access_token": token, "token_type": "bearer", "role": user["role"]}

    return require


def initialize_auth(db):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            username VARCHAR(64) PRIMARY KEY, salt TEXT NOT NULL,
            password_hash TEXT NOT NULL, role VARCHAR(10) NOT NULL
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash VARCHAR(64) PRIMARY KEY, username VARCHAR(64) NOT NULL REFERENCES users(username),
            expires BIGINT NOT NULL
        )
    """))
