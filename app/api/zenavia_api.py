import os
import time
import logging
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("zenavia_api")

BASE_URL = "https://api.zenavia.net/v1"


class ZenaviaAPI:
    def __init__(self):
        self.player_token = os.getenv("ZENAVIA_PLAYER_TOKEN")
        self.access_token: Optional[str] = None
        self.token_expires_at: float = 0

        if not self.player_token:
            raise RuntimeError(
                "ZENAVIA_PLAYER_TOKEN est introuvable dans le fichier .env"
            )

    def login(self) -> str:
        response = requests.post(
            f"{BASE_URL}/auth/login",
            json={"token": self.player_token},
            timeout=15,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Erreur login Zenavia ({response.status_code}) : {response.text}"
            )

        payload = response.json()
        data = payload.get("data", {})

        access_token = data.get("accessToken")
        expires_in = data.get("expiresIn", 3600)

        if not access_token:
            raise RuntimeError("L'API Zenavia n'a pas renvoyé de accessToken.")

        self.access_token = access_token
        self.token_expires_at = time.time() + max(int(expires_in) - 60, 60)

        log.info("✅ Login Zenavia réussi.")
        return self.access_token

    def ensure_token(self) -> str:
        if not self.access_token or time.time() >= self.token_expires_at:
            return self.login()
        return self.access_token

    def _headers(self) -> dict[str, str]:
        token = self.ensure_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def get_player_stats(self, pseudo: str) -> dict[str, Any]:
        response = requests.get(
            f"{BASE_URL}/players/{pseudo}/stats",
            headers=self._headers(),
            timeout=15,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Erreur get_player_stats ({response.status_code}) : {response.text}"
            )

        return response.json().get("data", {})

    def get_player_games(self, pseudo: str, page: int = 0, size: int = 20) -> list[dict[str, Any]]:
        response = requests.get(
            f"{BASE_URL}/players/{pseudo}/games",
            headers=self._headers(),
            params={"page": page, "size": size},
            timeout=15,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Erreur get_player_games ({response.status_code}) : {response.text}"
            )

        return response.json().get("data", [])

    def get_game_detail(self, game_id: int) -> dict[str, Any]:
        response = requests.get(
            f"{BASE_URL}/games/{game_id}",
            headers=self._headers(),
            timeout=15,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Erreur get_game_detail ({response.status_code}) : {response.text}"
            )

        return response.json().get("data", {})

    def get_player_profile(self, pseudo: str) -> dict[str, Any]:
        response = requests.get(
            f"{BASE_URL}/players/{pseudo}",
            headers=self._headers(),
            timeout=15,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Erreur get_player_profile ({response.status_code}) : {response.text}"
            )

        payload = response.json()
        log.info(f"DEBUG get_player_profile({pseudo}) -> {payload}")

        return payload.get("data", {})

    def get_maps(self) -> list[dict[str, Any]]:
        response = requests.get(
            f"{BASE_URL}/maps",
            headers=self._headers(),
            timeout=15,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Erreur get_maps ({response.status_code}) : {response.text}"
            )

        return response.json().get("data", [])

    def get_maps_index(self) -> dict[str, dict[str, Any]]:
        maps = self.get_maps()

        result: dict[str, dict[str, Any]] = {}

        for m in maps:
            map_id = str(m.get("id"))
            result[map_id] = {
                "id": m.get("id"),
                "name": m.get("displayName") or m.get("mapName") or f"map_{map_id}",
                "min_players": int(m.get("minPlayer", 0) or 0),
                "max_players": int(m.get("maxPlayer", 0) or 0),
                "author": m.get("author"),
            }

        return result    