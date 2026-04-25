"""GraphQL client for the MMR (Mystic Records) streaming API."""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger("adhan.api_client")

LOGIN_MUTATION = """
mutation Login($loginDto: LoginDto!) {
  login(loginDto: $loginDto) {
    accessToken
    refreshToken
    user {
      id
      email
      displayName
      planTier
    }
  }
}
"""

TRACKS_QUERY = """
query GetTracksPaginated($limit: Int!, $offset: Int!) {
  tracksPaginated(limit: $limit, offset: $offset) {
    tracks {
      id
      title
      duration
      mediaUrl
      coverArtUrl
      artistRelation {
        name
      }
    }
    total
    hasMore
  }
}
"""


class MMRApiClient:
    """Client for the Mystic Records GraphQL API."""

    def __init__(self, api_url: str, email: str, password: str):
        self.api_url = api_url
        self.email = email
        self.password = password
        self.access_token = None
        self.refresh_token = None
        self._session = requests.Session()
        self._session.headers["Content-Type"] = "application/json"

    def login(self) -> bool:
        """Authenticate and store JWT tokens. Returns True on success.

        If no credentials were provided, skips login (the API may allow
        unauthenticated track listing).
        """
        if not self.email or not self.password:
            logger.info("No credentials configured — using unauthenticated access")
            self.access_token = "__anonymous__"
            return True

        try:
            data = self._query(LOGIN_MUTATION, {
                "loginDto": {
                    "usernameOrEmail": self.email,
                    "password": self.password,
                }
            })
            result = data["login"]
            self.access_token = result["accessToken"]
            self.refresh_token = result["refreshToken"]
            self._session.headers["Authorization"] = f"Bearer {self.access_token}"

            user = result["user"]
            logger.info(
                "Logged in to MMR as %s (plan: %s)",
                user["displayName"],
                user["planTier"],
            )
            return True
        except Exception:
            logger.exception("Failed to login to MMR API at %s", self.api_url)
            return False

    def fetch_all_tracks(self) -> list[dict]:
        """Fetch the complete track catalog via paginated queries.

        Returns a list of track dicts, each with at minimum:
        id, title, duration, mediaUrl, coverArtUrl, artistRelation.
        Only tracks with a non-empty mediaUrl are included.
        """
        all_tracks = []
        offset = 0
        page_size = 100

        while True:
            try:
                data = self._query(TRACKS_QUERY, {
                    "limit": page_size,
                    "offset": offset,
                })
                page = data["tracksPaginated"]
                tracks = page["tracks"]

                playable = [t for t in tracks if t.get("mediaUrl")]
                all_tracks.extend(playable)

                logger.debug(
                    "Fetched tracks %d–%d of %d (%d playable)",
                    offset + 1,
                    offset + len(tracks),
                    page["total"],
                    len(playable),
                )

                if not page["hasMore"]:
                    break
                offset += page_size

            except Exception:
                logger.exception("Failed to fetch tracks at offset %d", offset)
                break

        logger.info("Fetched %d playable tracks from MMR catalog", len(all_tracks))
        return all_tracks

    def _query(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query/mutation and return the data dict."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        response = self._session.post(self.api_url, json=payload, timeout=30)
        response.raise_for_status()

        result = response.json()
        if "errors" in result:
            msg = result["errors"][0].get("message", str(result["errors"]))
            raise RuntimeError(f"GraphQL error: {msg}")

        return result["data"]
